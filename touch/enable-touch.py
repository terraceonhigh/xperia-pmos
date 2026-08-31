#!/usr/bin/python3
"""Enable the s6sy761 touchscreen on Sony Xperia 10 III (pdx213) under postmarketOS.

Port of the procedure proven on Mobian (see xperia-mobian/enable-touch.sh), with the
SSH-from-the-Mac wrapper removed so it can run on-device under OpenRC.

Why any of this is needed: the patched DTB has `avdd-supply` and the
`touch-en-regulator` node removed, because letting the regulator framework manage
that rail kills the AMOLED panel. So the touch IC's AVDD rail has to be raised from
userspace and *held* for as long as touch is wanted.

The sequence and the sleeps are load-bearing -- do not reorder or shorten them:

  1. TLMM line 10 -> output high        AVDD on; fd held open by a forked child
  2. insmod s6sy761.ko                  first probe usually fails, I2C DMA errors
  3. unbind the i2c device              discard the bad probe
  4. TLMM line 21 low 0.5s -> high      hard-reset the IC, then settle 2s
  5. rebind                             clean probe

This uses the GPIO character device, not sysfs: the hybrid kernel is built with
CONFIG_GPIO_CDEV=y and CONFIG_GPIO_SYSFS unset, so /sys/class/gpio does not exist.
"""

import fcntl
import glob
import os
import struct
import sys
import time

MODULE = "/usr/local/lib/s6sy761.ko"
DRIVER = "/sys/bus/i2c/drivers/s6sy761"
I2C_ADDR = "0048"          # touch IC sits at i2c address 0x48
AVDD_LINE = 10             # TLMM gpio powering the touch IC AVDD rail
RESET_LINE = 21            # TLMM gpio driving the touch IC reset pin

# ioctl numbers for the v1 GPIO chardev ABI (CONFIG_GPIO_CDEV_V1=y)
GPIO_GET_CHIPINFO_IOCTL = 0x8044B401
GPIO_GET_LINEHANDLE_IOCTL = 0xC16CB403
GPIOHANDLE_SET_LINE_VALUES_IOCTL = 0xC040B409
GPIOHANDLE_REQUEST_OUTPUT = 0x02


def log(msg):
    # Uptime is stamped on every line so this log can be lined up against dmesg
    # timestamps, which is the only way to tell our actions from the driver's.
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
    except OSError:
        up = 0.0
    print(f"[{up:9.3f}] {msg}", flush=True)


def find_tlmm_chip():
    """Return the /dev/gpiochipN that is the SoC's TLMM pin controller.

    Resolved by chip info rather than hardcoded: the PMIC also exposes a gpiochip,
    and writing line 10 on the wrong chip would poke an unrelated pin. Falls back to
    gpiochip1, which is what TLMM enumerated as on Mobian with this same kernel.
    """
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue
        try:
            info = fcntl.ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, bytes(68))
            name = info[0:32].split(b"\0")[0].decode(errors="replace")
            label = info[32:64].split(b"\0")[0].decode(errors="replace")
            lines = struct.unpack_from("<I", info, 64)[0]
        except OSError:
            continue
        finally:
            os.close(fd)
        # SM6350 TLMM has 157 lines; the PMIC chips are much smaller.
        if ("tlmm" in label.lower() or "pinctrl" in label.lower()) and lines >= 100:
            log(f"TLMM is {path} (label={label!r}, name={name!r}, {lines} lines)")
            return path
    log("WARNING: no TLMM gpiochip identified, falling back to /dev/gpiochip1")
    return "/dev/gpiochip1"


def request_line(chip_path, line, value, label):
    """Request one GPIO line as an output with an initial value.

    Returns the line-handle fd, or None if the line is already held by someone else.
    The returned fd owns the line: closing it releases the line back to its default,
    which is why the AVDD handle has to be kept open.
    """
    req = bytearray(364)
    struct.pack_into("<I", req, 0, line)                    # lineoffsets[0]
    struct.pack_into("<I", req, 256, GPIOHANDLE_REQUEST_OUTPUT)
    req[260] = value                                        # default_values[0]
    tag = label.encode()
    req[324:324 + len(tag)] = tag                           # consumer_label
    struct.pack_into("<I", req, 356, 1)                     # lines = 1

    fd = os.open(chip_path, os.O_RDWR)
    try:
        res = fcntl.ioctl(fd, GPIO_GET_LINEHANDLE_IOCTL, bytes(req))
    except OSError as e:
        if e.errno == 16:  # EBUSY -- already requested, e.g. we already ran
            log(f"GPIO {line} already held (OK)")
            return None
        log(f"ERROR: GPIO {line}: {e}")
        sys.exit(1)
    finally:
        os.close(fd)
    return struct.unpack_from("<i", bytearray(res), 360)[0]


def set_line(handle_fd, value):
    values = bytearray(64)
    values[0] = value
    fcntl.ioctl(handle_fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, bytes(values))


def hold_forever(fd):
    """Fork a child that does nothing but keep the AVDD line handle open.

    The child is orphaned deliberately: init reparents it, and the rail stays up for
    the life of the boot. Releasing the fd drops AVDD and touch dies.
    """
    pid = os.fork()
    if pid == 0:
        os.setsid()
        # Drop the inherited stdio: holding OpenRC's pipes open is the classic way
        # a service start hangs waiting for EOF on output that never closes.
        null = os.open(os.devnull, os.O_RDWR)
        for stdio in (0, 1, 2):
            os.dup2(null, stdio)
        while True:
            time.sleep(3600)
    log(f"AVDD held by pid {pid}")


def wait_for_i2c_device(timeout=90):
    """Wait for the touch i2c device to appear, returning its name (e.g. '0-0048').

    OpenRC's default runlevel can start before the i2c bus has enumerated, so poll
    rather than assume.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = glob.glob(f"/sys/bus/i2c/devices/*-{I2C_ADDR}")
        if found:
            return os.path.basename(found[0])
        time.sleep(1)
    return None


def write_sysfs(path, text):
    try:
        with open(path, "w") as f:
            f.write(text)
        return True
    except OSError as e:
        log(f"  {path}: {e}")
        return False


def reset_pulse(rst_fd, settle):
    """Assert reset for 0.5s, release it, then let the IC boot for `settle` seconds.

    The IC must reboot with AVDD already up. gpio21 has bias-pull-up in the DT, so it
    comes out of reset by itself at pinctrl time -- long before userspace can raise
    AVDD -- and a chip that booted without analog power reports a zeroed panel (the
    "axis have not been set" warning) even though its firmware-integrity check passes.
    """
    log(f"  reset: GPIO {RESET_LINE} LOW")
    set_line(rst_fd, 0)
    time.sleep(0.5)
    set_line(rst_fd, 1)
    log(f"  reset: GPIO {RESET_LINE} HIGH, settling {settle}s")
    time.sleep(settle)


def probe_ok(dev):
    """True only if the driver is bound *and* an input device exists."""
    bound = os.path.exists(f"{DRIVER}/{dev}")
    events = glob.glob(f"/sys/bus/i2c/devices/{dev}/input/input*/event*")
    return bound and bool(events), bound, events


def main():
    if os.geteuid() != 0:
        sys.exit("must run as root")

    dev = wait_for_i2c_device()
    if not dev:
        sys.exit(f"ERROR: no i2c device at address {I2C_ADDR} appeared")
    log(f"touch i2c device: {dev}")

    chip = find_tlmm_chip()

    # 1. AVDD on, held for the life of the boot, and given time to settle before
    #    anything talks to the IC.
    avdd = request_line(chip, AVDD_LINE, 1, "touch_avdd")
    if avdd is not None:
        log(f"GPIO {AVDD_LINE} HIGH (AVDD on)")
        hold_forever(avdd)
    time.sleep(1.0)

    # 2. Take the reset line and hold it. Requested already deasserted so that
    #    claiming it cannot itself reset the IC at an awkward moment.
    rst = request_line(chip, RESET_LINE, 1, "touch_rst")
    if rst is None:
        sys.exit(f"ERROR: could not take GPIO {RESET_LINE} for reset")

    # 3. Reboot the IC now that AVDD is present, *before* the driver ever probes.
    #    This is the ordering the Mobian procedure could not use: there the module
    #    auto-loaded ~100s into boot, so the reset could only ever come afterwards.
    log("resetting IC before first probe (AVDD is up)")
    reset_pulse(rst, settle=5)

    # 4. Now load the driver, so its first probe sees a properly booted IC.
    if not os.path.isdir(DRIVER):
        log(f"insmod {MODULE}")
        if os.system(f"insmod {MODULE} 2>&1") != 0:
            log("  insmod returned non-zero (may already be loaded)")
    else:
        log("driver already registered")
    time.sleep(1.0)

    ok, bound, events = probe_ok(dev)
    if ok:
        log(f"OK: touch enabled on first probe ({os.path.basename(events[0])})")
        hold_forever(rst)
        return 0
    log(f"first probe did not bind (bound={bound}); retrying with longer settles")

    # 5. Retry the whole power-cycle-and-rebind dance, giving the IC more time each
    #    round. Escalating rather than fixed because the settle needed is unknown --
    #    2s was demonstrably too short.
    for settle in (5, 8, 12):
        write_sysfs(f"{DRIVER}/unbind", dev)      # no-op if it never bound
        time.sleep(0.5)
        reset_pulse(rst, settle)
        write_sysfs(f"{DRIVER}/bind", dev)
        time.sleep(1.0)
        ok, bound, events = probe_ok(dev)
        log(f"  after {settle}s settle: bound={bound} events={events}")
        if ok:
            log(f"OK: touch enabled ({os.path.basename(events[0])})")
            hold_forever(rst)
            return 0

    hold_forever(rst)
    log(f"ERROR: touch not enabled (bound={bound} events={events})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
