#!/usr/bin/env python3
# Pair-check a pmbootstrap boot.img against its rootfs image: header fields,
# kernel compression, and that pmos_root_uuid in the cmdline matches the
# ext4 UUID of a partition inside the rootfs image (MBR or GPT).
import struct, sys, re, uuid

boot, rootfs = sys.argv[1], sys.argv[2]
d = open(boot, "rb").read(4096)
assert d[:8] == b"ANDROID!", "bad boot magic"
f = struct.unpack("<10I", d[8:48])
names = ["kernel_size","kernel_addr","ramdisk_size","ramdisk_addr","second_size",
         "second_addr","tags_addr","page_size","header_version","os_version"]
hdr = dict(zip(names, f))
for n in names: print(f"{n:15} {hex(hdr[n])}")
cmdline = d[64:64+512].split(b"\0")[0].decode()
print("cmdline:", cmdline)
k = open(boot, "rb"); k.seek(hdr["page_size"]); kb = k.read(64)
print("kernel:", "gzip" if kb[:2] == b"\x1f\x8b" else ("raw arm64 Image" if kb[56:60] == b"ARM\x64" else f"unknown {kb[:4].hex()}"))
want = re.search(r"pmos_root_uuid=([0-9a-fA-F-]+)", cmdline).group(1).lower()
wantb = re.search(r"pmos_boot_uuid=([0-9a-fA-F-]+)", cmdline).group(1).lower()
print("cmdline pmos_root_uuid:", want, " pmos_boot_uuid:", wantb)

r = open(rootfs, "rb")
r.seek(512); gpt = r.read(8) == b"EFI PART"
parts = []
if gpt:
    r.seek(1024)
    for i in range(128):
        e = r.read(128)
        if e[:16] == b"\0"*16: continue
        parts.append((i, struct.unpack("<Q", e[32:40])[0]))
else:
    r.seek(446)
    for i in range(4):
        e = r.read(16)
        typ, first = e[4], struct.unpack("<I", e[8:12])[0]
        if typ: parts.append((i+1, first))
print("table:", "GPT" if gpt else "MBR", "| partitions:", len(parts))

def fs_uuid(first):
    r.seek(first*512 + 1024); sb = r.read(1024)
    if sb[0x38:0x3a] != b"\x53\xef": return None
    return str(uuid.UUID(bytes=sb[0x68:0x78]))
ok_root = ok_boot = False
for i, first in parts:
    u = fs_uuid(first)
    tag = " <- root" if u == want else (" <- boot" if u == wantb else "")
    ok_root |= u == want; ok_boot |= u == wantb
    print(f"p{i}: first_lba={first} fs_uuid={u}{tag}")
print("PAIR CHECK:", "OK — root and boot UUIDs match" if ok_root and ok_boot else "MISMATCH — do not flash")
sys.exit(0 if (ok_root and ok_boot) else 1)
