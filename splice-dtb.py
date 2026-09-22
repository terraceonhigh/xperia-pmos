#!/usr/bin/env python3
"""Replace the appended DTB in a header-v0 Android boot image, keeping everything else.

Usage: splice-dtb.py IN_BOOT.img NEW.dtb OUT_BOOT.img

The kernel section of a pmbootstrap image is vmlinuz followed by the DTB. The DTB's
own header carries its total size, so the split point is kernel_size - dtb_totalsize
once the trailing DTB magic is located. Ramdisk, cmdline (and so pmos_root_uuid),
load addresses and page size are copied verbatim; only kernel_size changes.
"""
import struct, sys

inp, dtb_path, out = sys.argv[1:4]
img = open(inp, "rb").read()
assert img[:8] == b"ANDROID!", "not a boot image"
f = list(struct.unpack("<10I", img[8:48]))
ksz, rsz, ssz, page, hver = f[0], f[2], f[4], f[7], f[8]
assert hver == 0, f"header v{hver}, this tool handles v0 only"
pad = lambda n: (n + page - 1) // page * page
kernel = img[page:page + ksz]
rest = img[page + pad(ksz):]           # ramdisk pages + anything after, verbatim

# find the old DTB: last occurrence of the FDT magic whose declared size reaches the end
magic = b"\xd0\x0d\xfe\xed"
pos = kernel.rfind(magic)
while pos > 0:
    if struct.unpack(">I", kernel[pos + 4:pos + 8])[0] == len(kernel) - pos:
        break
    pos = kernel.rfind(magic, 0, pos)
assert pos > 0, "no appended DTB found"
vmlinuz = kernel[:pos]
old_dtb_size = len(kernel) - pos

new_dtb = open(dtb_path, "rb").read()
assert new_dtb[:4] == magic and struct.unpack(">I", new_dtb[4:8])[0] == len(new_dtb), "bad DTB"
new_kernel = vmlinuz + new_dtb
f[0] = len(new_kernel)
hdr = bytearray(img[:page])
hdr[8:48] = struct.pack("<10I", *f)
body = new_kernel + b"\0" * (pad(len(new_kernel)) - len(new_kernel))
open(out, "wb").write(bytes(hdr) + body + rest)

cmdline = img[64:576].split(b"\0")[0].decode()
print(f"vmlinuz {len(vmlinuz)} bytes | dtb {old_dtb_size} -> {len(new_dtb)} bytes | kernel_size {ksz} -> {f[0]}")
print(f"ramdisk_size {rsz} unchanged | page {page} | header v{hver} | cmdline unchanged:")
print(" ", cmdline)
