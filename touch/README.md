# Touchscreen on the pmOS hybrid boot

Getting the s6sy761 touchscreen working under postmarketOS on the pdx213, deployed
entirely over `fastboot` from a Mac.

## Why this is not just "install the module"

Hybrid boot runs the **Mobian 6.12 kernel** against the **pmOS 6.19 rootfs** (see the
top-level README for why: the pmOS kernel has `CONFIG_DRM_MSM` built in, kills
simpledrm, and cannot drive the DSI panel). Every kernel module in that rootfs is
built for 6.19 and will not load. Touch is a module. So touch is dead.

Touch is already solved on this *exact kernel* over in
[xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) — a cross-compiled
`s6sy761.ko` plus a GPIO dance. So the fix is a transplant, not new bring-up.

The awkward part is delivery. Under hybrid boot the phone has **no USB networking and
no WiFi** (same module mismatch), and the rootfs lives on a microSD card that needs a
Linux host to mount. There is no way to copy a file onto the phone.

So the **initramfs carries the files**. It already mounts the rootfs at `/sysroot`
before `switch_root`, so a few lines injected there drop the payload into place on the
next boot. The whole deployment becomes one `fastboot flash boot_a`.

## What gets installed into the rootfs

| Path | Purpose |
|---|---|
| `/usr/local/lib/s6sy761.ko` | Touch driver, built for `vermagic=6.12.0-sm6350` |
| `/usr/local/bin/enable-touch.py` | Raises + holds the AVDD rail, resets and binds the IC |
| `/etc/init.d/enable-touch` | OpenRC service |
| `/etc/runlevels/default/enable-touch` | Symlink enabling it at boot |

## Why the enable script looks the way it does

The patched DTB has `avdd-supply` and the `touch-en-regulator` node **removed** — with
them in place the regulator framework kills the AMOLED panel. The consequence is that
the touch IC's power rail has to be raised from userspace and *held* for the life of
the boot, which is why the script forks a child whose only job is to keep a GPIO line
handle open. Release that fd and touch dies.

It talks to the GPIO **character device**, not sysfs. The previous attempt at this
(`build--enable-touch-openrc` in the mobian release assets) used
`/sys/class/gpio/export`, which cannot work: this kernel is built with
`CONFIG_GPIO_CDEV=y` and **`CONFIG_GPIO_SYSFS` unset**, so `/sys/class/gpio` does not
exist. It also skipped the reset/rebind, without which the first probe's I2C DMA
errors are never cleaned up.

The sequence and its sleeps are load-bearing, copied from the procedure proven on
Mobian:

1. TLMM line 10 → output high (AVDD on), fd held open forever
2. `insmod s6sy761.ko` — the first probe usually fails with I2C DMA errors
3. unbind the i2c device — discard that bad probe
4. TLMM line 21 low 0.5s → high, settle 2s — hard-reset the IC
5. rebind — clean probe

Two things are resolved at runtime rather than hardcoded, because guessing them wrong
means poking an unrelated pin: the TLMM gpiochip is found by chip label and line count
(falling back to `gpiochip1`, which is what it enumerated as on Mobian), and the i2c
device name is globbed from `/sys/bus/i2c/devices/*-0048`.

## The module: `build--s6sy761.ko` from the release does not load

Worth knowing before you trust that file. It reports `vermagic=6.12.0-sm6350` with a
1088-byte `struct module`; the running kernel wants `6.12-sm6350` and 1152 bytes, so
`insmod` fails:

```
insmod: ERROR: could not insert module: Invalid module format
module s6sy761: .gnu.linkonce.this_module section size must match
                the kernel's built struct module size at run time
```

It was a build attempt against a different tree, and it never worked. No distro ships
a working one either — `CONFIG_TOUCHSCREEN_S6SY761` is unset in every Mobian config,
which is why the driver has to be built out of tree. It is also absent from the Mobian
initrd and from `images--mobian-rootfs-raw.img`.

The target ABI, measured from modules known to load on this kernel (`msm.ko`,
`dm-mod.ko`, `rmnet.ko`, `qcom_stats.ko` out of the Mobian initrd):

| Property | Required value |
|---|---|
| `vermagic` | `6.12-sm6350 SMP mod_unload aarch64` |
| `.gnu.linkonce.this_module` | 1152 bytes |
| `CONFIG_MODVERSIONS` | off — no symbol CRCs to match |
| `CONFIG_MODULE_SIG` | off — unsigned modules load |

`touch/build-module.sh` builds it. It uses **6.12.107** source even though the kernel
is 6.12.68, which is safe here for a non-obvious reason: Debian sets `KERNELRELEASE`
to plain `6.12-sm6350` with no point version — hence the odd `uname -r` — so both
trees yield the same vermagic, and 6.12.107's own modules were verified to have the
same 1152-byte `struct module`. 6.12.68 has rotated out of the archive. Building from
the orig tarball needs `SUBLEVEL` blanked and `LOCALVERSION="-sm6350"`, or vermagic
comes out as `6.12.107` and will not load.

Run it on a Linux host with podman (macOS can't run kbuild, and Debian's prebuilt
kbuild host tools are Linux binaries):

```bash
ssh humboldt 'bash -s' < touch/build-module.sh
```

## Building the boot image

```bash
python3 touch/build-touch-boot.py \
    --boot build/boot-pmos-hybrid-ROLLBACK.img \
    --ko   build/s6sy761.ko \
    --reference-ko build/reference-msm.ko \
    --out  build/boot-pmos-hybrid-touch.img
```

Always pass `--reference-ko` — any module known to load on the target kernel. It makes
the build compare vermagic *and* `struct module` size against that reference, which is
the same test the kernel applies at load time, done offline. Without it a module like
the broken release asset sails through and costs a full boot-and-pull cycle to
discover.

The input is the checkpoint image with an attested GUI
(`checkpoints/03-pmos-hybrid-display/boot.img`). The rebuild copies the original
header **verbatim** and patches only `ramdisk_size` — the kernel is reused
byte-identical and the cmdline is never re-typed. That is deliberate: pitfall #12 in
the top-level README was a rebuild silently changing the cmdline, which cost hours.

Every build asserts a byte-identical cpio round-trip before touching anything, then
re-reads its own output and checks 15 properties (cmdline unchanged, kernel identical,
header differs in exactly one field, no original initramfs file lost, `.ko` vermagic
correct, …). It refuses to write an image that fails any of them.

`python3 touch/build-touch-boot.py --selftest` runs the parser/patcher checks with no
inputs needed.

## Flashing

The phone charges only in fastboot or powered off, so plug in early.

```bash
# Hold Volume Up while plugging in USB -- the phone powers on into fastboot.
fastboot devices                  # expect HQ616S35A9
fastboot getvar current-slot      # confirm slot a before flashing boot_a
fastboot flash boot_a build/boot-pmos-hybrid-touch.img
fastboot reboot
```

`fastboot boot` is **not** supported on this device; the image must be flashed.

**Check which SD card is inserted first.** This image and the pmOS rollback both
mount the rootfs by pmOS partition UUID. If the *Mobian* card is in the phone, both
hang in the initramfs with a black screen and no network. `boot-mobian-ROLLBACK.img`
is staged for exactly that case -- it is the only recovery that does not require
swapping the card.

### Rollback

`build/boot-pmos-hybrid-ROLLBACK.img` is the untouched checkpoint image that is known
to reach the greetd login screen.

```bash
fastboot flash boot_a build/boot-pmos-hybrid-ROLLBACK.img
```

| Image | sha256 |
|---|---|
| `boot-pmos-hybrid-touch.img` | `a36b370378254d572fa62d0ebe0f359966471084fa8d71b104332d3da35b19ce` |
| `boot-pmos-hybrid-ROLLBACK.img` (pmOS rootfs) | `1be9dac0c9309ed10b9513bc8593b4171dd9c00c0c27df043dde4001b99efef3` |
| `boot-mobian-ROLLBACK.img` (Mobian rootfs) | `59a1167af6f3ce2331d7881fba06e69695480ca9749130380c04919399ebe115` |

## Verifying on the device

Success is touch responding on the greetd login screen. With no working network under
hybrid boot, the diagnostics are on-device:

```sh
dmesg | grep -iE 's6sy|touch payload'   # payload install + driver probe
rc-service enable-touch start           # re-run by hand
/usr/local/bin/enable-touch.py          # verbose output
```

`pdx213 touch payload installed` in the log means the initramfs stage worked; the
driver stage is separate. The two log lines are trustworthy: the install commands are
`&&`-chained, so a partial failure logs `FAILED` rather than claiming success.

## Known limitations

- **Boot risk is low but not zero.** The injected block is wrapped so any failure is
  logged and ignored rather than halting boot, and `switch_root` is asserted to remain
  the final action. Still, it edits the boot path — hence the staged rollback image.
- **A post-login rebind may be needed.** On Mobian the greeter→session transition
  required `rebind-touch.sh` as a user service. If touch works on the lock screen and
  dies after unlocking, that is this same problem and needs the equivalent for Phosh
  under pmOS. Not built yet — no point guessing before seeing it.
- **`systemctl restart phosh` has no pmOS equivalent worth trying.** Restarting the
  compositor crashes the I2C bus and kills touch. Always full reboot.
- The whole in-kernel chain touch needs was confirmed built into the hybrid kernel
  (`I2C_QCOM_GENI`, `PINCTRL_SM6350`, `INPUT_EVDEV`, `GPIO_CDEV` all `=y`), which
  matters because the pmOS rootfs supplies no usable modules at all.
  `TOUCHSCREEN_S6SY761` is unset there -- hence the out-of-tree module.
- Only the pre-Phosh rootfs image could be inspected offline (the live SD card is the
  expanded, Phosh-installed version). `python3` was confirmed present there, and
  package installs only add, so the interpreter the script needs will be there.
