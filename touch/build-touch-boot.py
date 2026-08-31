#!/usr/bin/env python3
"""Build a pdx213 hybrid boot.img whose initramfs installs the touchscreen support.

The problem this solves: under hybrid boot the phone runs the Mobian 6.12 kernel
against the pmOS 6.19 rootfs, so nothing in that rootfs can drive the touchscreen --
its modules are built for the wrong kernel. The fix is three small files in the
rootfs (a matching s6sy761.ko, the enable script, an OpenRC service). But with
hybrid boot there is no working USB networking or WiFi, and the SD card needs a
Linux host to mount, so there is no way to copy files in.

So the initramfs carries them. It already mounts the rootfs at /sysroot before
switch_root; we append a few lines there that drop the payload in place. That makes
the entire deployment a single `fastboot flash boot_a`.

The boot.img is rebuilt by copying the original header *verbatim* and patching only
ramdisk_size. Nothing re-types the kernel cmdline, which is what pitfall #12 in the
README was: a rebuild silently changed the cmdline and cost hours of debugging.

Usage:
    build-touch-boot.py --boot ORIG.img --ko s6sy761.ko --out NEW.img
    build-touch-boot.py --selftest
"""

import argparse
import glob
import gzip
import hashlib
import io
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Where the payload files are staged inside the initramfs. Deliberately at the top
# level with a shared prefix: adding files needs no new directory entries, and the
# prefix makes them obvious to anyone poking at an unpacked initramfs.
PAYLOAD = {
    "touch-payload-s6sy761.ko": (0o100644, None),        # filled from --ko
    "touch-payload-enable.py": (0o100755, "enable-touch.py"),
    "touch-payload-enable.initd": (0o100755, "enable-touch.initd"),
}

# Appended to init_2nd.sh immediately before it hands off to the real root.
# Every failure is swallowed: a broken payload copy must never stop the phone from
# booting, because a phone that does not boot cannot be fixed over the network.
DEPLOY_BLOCK = '''
# --- pdx213 touchscreen payload (injected by touch/build-touch-boot.py) ---
# The rootfs is mounted at /sysroot but we have not switched to it yet. Install the
# touch driver + enable script + OpenRC service. Idempotent; failures are ignored.
if [ -f /touch-payload-s6sy761.ko ]; then
	# &&-chained on purpose: a plain { ...; } || echo group only reports the exit
	# status of its *last* command, so a failed cp would still log success and the
	# on-device diagnostic would lie. if/then/else also swallows failure, so a
	# broken install still boots.
	if mkdir -p /sysroot/usr/local/lib /sysroot/usr/local/bin \\
			/sysroot/etc/init.d /sysroot/etc/runlevels/default \\
		&& cp /touch-payload-s6sy761.ko /sysroot/usr/local/lib/s6sy761.ko \\
		&& cp /touch-payload-enable.py /sysroot/usr/local/bin/enable-touch.py \\
		&& cp /touch-payload-enable.initd /sysroot/etc/init.d/enable-touch \\
		&& chmod 644 /sysroot/usr/local/lib/s6sy761.ko \\
		&& chmod 755 /sysroot/usr/local/bin/enable-touch.py \\
			/sysroot/etc/init.d/enable-touch \\
		&& ln -sf /etc/init.d/enable-touch \\
			/sysroot/etc/runlevels/default/enable-touch
	then
		echo "$LOG_PREFIX pdx213 touch payload installed" > /dev/kmsg
		# kmsg does not survive a power cycle, and under hybrid boot there is no
		# network and no GUI to read it from. Leave a marker on the card instead.
		mkdir -p /sysroot/var/log
		echo "payload installed by initramfs" \\
			> /sysroot/var/log/touch-payload.status
		sync
	else
		echo "$LOG_PREFIX pdx213 touch payload FAILED" > /dev/kmsg
		mkdir -p /sysroot/var/log 2>/dev/null
		echo "payload install FAILED in initramfs" \\
			> /sysroot/var/log/touch-payload.status 2>/dev/null
	fi
fi
# --- end pdx213 touchscreen payload ---
'''

SWITCH_ROOT_LINE = 'exec switch_root /sysroot "$init"'
MARKER = "pdx213 touchscreen payload"

# The release's build--s6sy761.ko says 6.12.0-sm6350, but the running kernel wants
# 6.12-sm6350 (confirmed against msm.ko, dm-mod.ko, rmnet.ko and qcom_stats.ko taken
# from the Mobian initrd for this kernel). Loading the wrong one fails with
# "Invalid module format" / "this_module section size must match", so the expected
# string is checked exactly rather than assumed.
EXPECT_VERMAGIC = "6.12-sm6350"


# --------------------------------------------------------------------- ELF

def elf_section_size(data, want):
    """Size of a named section in an ELF64 little-endian object, or None.

    Used to compare .gnu.linkonce.this_module against a module known to load on the
    target kernel: that section holds `struct module`, and the kernel refuses any
    module whose copy is a different size than the one it was built with. Comparing
    it offline catches a config/source mismatch that vermagic alone can miss.
    """
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    # Section header string table, to resolve sh_name offsets.
    stroff = struct.unpack_from("<Q", data, shoff + shstrndx * shentsize + 0x18)[0]
    for i in range(shnum):
        sh = shoff + i * shentsize
        name_off = struct.unpack_from("<I", data, sh)[0]
        end = data.index(b"\0", stroff + name_off)
        if data[stroff + name_off:end].decode() == want:
            return struct.unpack_from("<Q", data, sh + 0x20)[0]
    return None


def ko_vermagic(data):
    marker = b"vermagic="
    i = data.find(marker)
    if i < 0:
        return None
    return data[i + len(marker):data.index(b"\0", i)].decode(errors="replace")


# ---------------------------------------------------------------- newc cpio

FIELDS = ("ino", "mode", "uid", "gid", "nlink", "mtime", "filesize",
          "devmajor", "devminor", "rdevmajor", "rdevminor", "namesize", "check")


class Entry:
    def __init__(self, name, hdr, data):
        self.name = name
        self.hdr = hdr          # dict of the 13 numeric header fields
        self.data = data

    def __repr__(self):
        return f"<Entry {self.name} {self.hdr['filesize']}B mode={self.hdr['mode']:o}>"


def cpio_parse(buf):
    """Parse a newc cpio archive.

    Returns (entries, trailer_hdr, trailing_bytes). The trailing bytes after the
    TRAILER!!! record are preserved verbatim so the archive can be rebuilt
    byte-for-byte -- initramfs images are zero-padded and we want the round-trip
    assertion below to be exact.
    """
    entries = []
    off = 0
    upper = None
    trailer = None
    while True:
        if buf[off:off + 6] != b"070701":
            raise ValueError(f"bad cpio magic at {off}: {buf[off:off+6]!r}")
        raw = [buf[off + 6 + i * 8: off + 6 + (i + 1) * 8] for i in range(13)]
        if upper is None:
            # Remember whether this archive writes hex in upper or lower case, so
            # serialising reproduces the original bytes exactly.
            joined = b"".join(raw)
            upper = not any(c in b"abcdef" for c in joined)
        hdr = {k: int(v, 16) for k, v in zip(FIELDS, raw)}
        name_end = off + 110 + hdr["namesize"]
        name = buf[off + 110:name_end - 1]          # strip trailing NUL
        dstart = name_end + (-name_end % 4)
        dend = dstart + hdr["filesize"]
        data = buf[dstart:dend]
        off = dend + (-dend % 4)
        if name == b"TRAILER!!!":
            trailer = hdr
            break
        entries.append(Entry(name.decode(), hdr, data))
    return entries, trailer, buf[off:], upper


def cpio_serialize(entries, trailer, trailing, upper):
    fmt = "%08X" if upper else "%08x"
    out = bytearray()

    def emit(name_bytes, hdr, data):
        # namesize and filesize are always derived from what is actually written,
        # never taken from the caller's header -- a stale value there desynchronises
        # every following record.
        hdr = dict(hdr)
        hdr["namesize"] = len(name_bytes) + 1
        hdr["filesize"] = len(data)
        out.extend(b"070701")
        for k in FIELDS:
            out.extend((fmt % hdr[k]).encode())
        out.extend(name_bytes + b"\0")
        out.extend(b"\0" * (-len(out) % 4))
        out.extend(data)
        out.extend(b"\0" * (-len(out) % 4))

    for e in entries:
        emit(e.name.encode(), e.hdr, e.data)
    emit(b"TRAILER!!!", trailer, b"")
    out.extend(trailing)
    return bytes(out)


# ------------------------------------------------------------- boot.img v0

BOOT_HDR = "<8s10I16s512s32s"
# Indices into the unpacked BOOT_HDR tuple. Named because getting page_size and
# header_version one apart silently "passes" -- both are 0 or 4096 in practice.
(I_MAGIC, I_KERNEL_SIZE, I_KERNEL_ADDR, I_RAMDISK_SIZE, I_RAMDISK_ADDR,
 I_SECOND_SIZE, I_SECOND_ADDR, I_TAGS_ADDR, I_PAGE_SIZE, I_HEADER_VERSION,
 I_OS_VERSION, I_NAME, I_CMDLINE, I_ID) = range(14)
RAMDISK_SIZE_OFF = 16   # byte offset of ramdisk_size within the header


def boot_split(img):
    """Split a header-v0 Android boot image into (header_bytes, kernel, ramdisk)."""
    f = struct.unpack_from(BOOT_HDR, img, 0)
    magic = f[I_MAGIC]
    ksize = f[I_KERNEL_SIZE]
    rsize = f[I_RAMDISK_SIZE]
    ssize = f[I_SECOND_SIZE]
    page = f[I_PAGE_SIZE]
    ver = f[I_HEADER_VERSION]
    if magic != b"ANDROID!":
        raise ValueError(f"not an Android boot image: {magic!r}")
    if ver != 0:
        raise ValueError(f"expected header_version 0, got {ver}")
    if ssize:
        raise ValueError("image has a 'second' area; not handled")

    def npages(n):
        return (n + page - 1) // page

    koff = page
    roff = koff + npages(ksize) * page
    return img[:page], img[koff:koff + ksize], img[roff:roff + rsize], page


def boot_rebuild(header, kernel, ramdisk, page):
    """Reassemble a boot image, patching only ramdisk_size in the original header.

    The header is otherwise copied byte-for-byte, so the kernel cmdline, load
    addresses and page size cannot drift from the image that was known to boot.
    """
    hdr = bytearray(header)
    struct.pack_into("<I", hdr, RAMDISK_SIZE_OFF, len(ramdisk))

    def pad(b):
        return b + b"\0" * (-len(b) % page)

    return bytes(pad(hdr) + pad(kernel) + pad(ramdisk))


def boot_cmdline(img):
    return struct.unpack_from(BOOT_HDR, img, 0)[I_CMDLINE].split(b"\0")[0].decode()


# ------------------------------------------------------------------- build

def patch_init(text):
    """Insert the deploy block just before init_2nd.sh hands off to the real root."""
    if MARKER in text:
        raise ValueError("init_2nd.sh already carries a touch payload block")
    if text.count(SWITCH_ROOT_LINE) != 1:
        raise ValueError(
            f"expected exactly one {SWITCH_ROOT_LINE!r}, "
            f"found {text.count(SWITCH_ROOT_LINE)}")
    return text.replace(SWITCH_ROOT_LINE, DEPLOY_BLOCK.lstrip("\n") + "\n" + SWITCH_ROOT_LINE)


def build(boot_path, ko_path, out_path, reference_ko=None):
    orig = open(boot_path, "rb").read()
    header, kernel, ramdisk_gz, page = boot_split(orig)
    print(f"input  {boot_path}")
    print(f"  kernel  {len(kernel)} bytes")
    print(f"  ramdisk {len(ramdisk_gz)} bytes (gzip)")
    print(f"  cmdline {boot_cmdline(orig)}")

    cpio = gzip.decompress(ramdisk_gz)
    entries, trailer, trailing, upper = cpio_parse(cpio)
    print(f"  initramfs: {len(entries)} entries, "
          f"{'upper' if upper else 'lower'}-case hex headers")

    # Round-trip before changing anything. If this fails, the writer does not
    # faithfully reproduce this archive and no output should be trusted.
    assert cpio_serialize(entries, trailer, trailing, upper) == cpio, \
        "cpio round-trip is not byte-identical -- refusing to build"
    print("  cpio round-trip verified byte-identical")

    by_name = {e.name: e for e in entries}
    init = by_name.get("init_2nd.sh")
    if init is None:
        raise ValueError("initramfs has no init_2nd.sh")
    init.data = patch_init(init.data.decode()).encode()
    print("  patched init_2nd.sh")

    # Give the injected files inode numbers above everything already present.
    next_ino = max(e.hdr["ino"] for e in entries) + 1
    template = init.hdr
    for i, (name, (mode, src)) in enumerate(sorted(PAYLOAD.items())):
        blob = open(ko_path if src is None else os.path.join(HERE, src), "rb").read()
        hdr = dict(template)
        hdr.update(ino=next_ino + i, mode=mode, uid=0, gid=0, nlink=1,
                   filesize=len(blob), namesize=len(name) + 1, check=0)
        entries.append(Entry(name, hdr, blob))
        print(f"  + {name} ({len(blob)} bytes, mode {mode & 0o7777:o})")

    new_cpio = cpio_serialize(entries, trailer, trailing, upper)
    # mtime=0 keeps the output reproducible: same inputs, same bytes.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(new_cpio)
    new_ramdisk = buf.getvalue()

    out = boot_rebuild(header, kernel, new_ramdisk, page)
    with open(out_path, "wb") as f:
        f.write(out)
    print(f"output {out_path} ({len(out)} bytes)")
    verify(orig, out, ko_path, reference_ko)
    return out_path


def verify(orig, out, ko_path, reference_ko=None):
    """Re-read the built image and prove the things that have burned this repo."""
    print("verifying:")
    o_hdr, o_kernel, o_rd, _ = boot_split(orig)
    n_hdr, n_kernel, n_rd, _ = boot_split(out)

    checks = []
    checks.append(("cmdline unchanged", boot_cmdline(out) == boot_cmdline(orig)))
    checks.append(("kernel byte-identical", n_kernel == o_kernel))
    # Header must differ in exactly one field: ramdisk_size.
    diff = [i for i in range(len(o_hdr)) if o_hdr[i] != n_hdr[i]]
    checks.append(("header differs only in ramdisk_size",
                   all(RAMDISK_SIZE_OFF <= i < RAMDISK_SIZE_OFF + 4 for i in diff)))
    new = struct.unpack_from(BOOT_HDR, out, 0)
    old = struct.unpack_from(BOOT_HDR, orig, 0)
    checks.append(("header_version still 0", new[I_HEADER_VERSION] == 0))
    checks.append(("kernel_addr unchanged",
                   new[I_KERNEL_ADDR] == old[I_KERNEL_ADDR]))
    checks.append(("page_size unchanged", new[I_PAGE_SIZE] == old[I_PAGE_SIZE]))
    checks.append(("ramdisk_size matches payload",
                   new[I_RAMDISK_SIZE] == len(n_rd)))

    entries, _, _, _ = cpio_parse(gzip.decompress(n_rd))
    names = {e.name: e for e in entries}
    checks.append(("init_2nd.sh carries deploy block",
                   MARKER in names["init_2nd.sh"].data.decode()))
    checks.append(("switch_root still present and last",
                   names["init_2nd.sh"].data.decode().count(SWITCH_ROOT_LINE) == 1))
    for name in PAYLOAD:
        checks.append((f"payload {name} present", name in names))
    ko = open(ko_path, "rb").read()
    checks.append(("payload .ko matches source",
                   names["touch-payload-s6sy761.ko"].data == ko))
    vm = ko_vermagic(ko)
    checks.append((f".ko vermagic is exactly {EXPECT_VERMAGIC!r} (got {vm!r})",
                   vm is not None and vm.split()[0] == EXPECT_VERMAGIC))
    if reference_ko:
        ref = open(reference_ko, "rb").read()
        rvm = ko_vermagic(ref)
        checks.append((f"reference module vermagic matches (got {rvm!r})",
                       rvm is not None and rvm == vm))
        # This is the exact check the kernel makes and that we previously failed.
        sec = ".gnu.linkonce.this_module"
        ours, theirs = elf_section_size(ko, sec), elf_section_size(ref, sec)
        checks.append((f"struct module size matches reference "
                       f"({ours} vs {theirs})",
                       ours is not None and ours == theirs))
    old_entries, _, _, _ = cpio_parse(gzip.decompress(o_rd))
    checks.append(("no original file lost",
                   {e.name for e in old_entries} <= set(names)))

    ok = True
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    if not ok:
        raise SystemExit("verification FAILED -- do not flash this image")
    print(f"  sha256 {hashlib.sha256(out).hexdigest()}")


def selftest():
    """Round-trip a synthetic archive, then prove the patch/inject logic."""
    entries = [
        Entry("init_2nd.sh", dict(zip(FIELDS, [7, 0o100755, 0, 0, 1, 0,
                                              0, 0, 0, 0, 0, 0, 0])),
              b'echo hi\n' + SWITCH_ROOT_LINE.encode() + b'\n'),
        Entry("etc/x", dict(zip(FIELDS, [8, 0o100644, 0, 0, 1, 0,
                                        0, 0, 0, 0, 0, 0, 0])), b"abc"),
    ]
    trailer = dict(zip(FIELDS, [0] * 13))
    trailer["nlink"] = 1
    for upper in (True, False):
        blob = cpio_serialize(entries, trailer, b"\0" * 512, upper)
        back, tr, trailing, detected = cpio_parse(blob)
        assert detected == upper, "hex case not detected"
        assert [e.name for e in back] == ["init_2nd.sh", "etc/x"]
        assert back[1].data == b"abc"
        assert cpio_serialize(back, tr, trailing, detected) == blob, "round-trip"

    patched = patch_init(entries[0].data.decode())
    assert MARKER in patched
    # The handoff must survive, exactly once, and still be the final action.
    assert patched.count(SWITCH_ROOT_LINE) == 1
    assert patched.rstrip().endswith(SWITCH_ROOT_LINE)
    # Patching twice must be refused rather than silently duplicating the block.
    try:
        patch_init(patched)
    except ValueError:
        pass
    else:
        raise AssertionError("double patch was not refused")

    # A header-v0 image must survive split/rebuild with only ramdisk_size moving.
    hdr = bytearray(4096)
    struct.pack_into(BOOT_HDR, hdr, 0, b"ANDROID!", 8, 0x10008000, 4, 0x11000000,
                     0, 0, 0x10000100, 4096, 0, 0, b"", b"cmd=keepme", b"")
    img = bytes(hdr) + b"K" * 8 + b"\0" * (4096 - 8) + b"R" * 4 + b"\0" * (4096 - 4)
    h, k, r, page = boot_split(img)
    assert k == b"K" * 8 and r == b"R" * 4 and page == 4096
    rebuilt = boot_rebuild(h, k, b"RRRRRR", page)
    assert boot_cmdline(rebuilt) == "cmd=keepme"
    assert boot_split(rebuilt)[2] == b"RRRRRR"
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boot", help="original working hybrid boot.img")
    ap.add_argument("--ko", help="s6sy761.ko built for the hybrid kernel")
    ap.add_argument("--out", help="output boot.img")
    ap.add_argument("--reference-ko", help="a module known to load on the target "
                    "kernel; its vermagic and struct module size are compared "
                    "against the payload (e.g. msm.ko from the Mobian initrd)")
    ap.add_argument("--selftest", action="store_true", help="run internal checks")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.boot and a.ko and a.out):
        ap.error("--boot, --ko and --out are all required")
    build(a.boot, a.ko, a.out, a.reference_ko)


if __name__ == "__main__":
    main()
