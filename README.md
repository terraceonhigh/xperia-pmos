# postmarketOS for Sony Xperia 10 III (pdx213)

postmarketOS device packages for the Sony Xperia 10 III (codename: pdx213, SoC: Qualcomm SM6350 / Snapdragon 690 5G).

## Status

**Mainline postmarketOS since 2026-09-21.** Kernel `linux-postmarketos-qcom-sm6350` 7.2.0
(the packaged [sm6350-mainline](https://github.com/sm6350-mainline/linux) tree Fairphone 4
uses) with two local changes: `CONFIG_TOUCHSCREEN_S6SY761=m`, and a DTS patch enabling UFS
(`kernel-patches/`). Phosh, systemd, rootfs on microSD. Full record in [FIRST-BOOT.md](FIRST-BOOT.md).

| Feature | Status | Notes |
|---------|--------|-------|
| Display | **Yes** | real DRM: `msm_dpu` + `panel-samsung-sofef01` |
| GPU | **Yes** | freedreno, OpenGL ES 3.2, Mesa 26.2 |
| Touch | **Yes** | in-tree `s6sy761` |
| Phosh session | **Yes** | apps launch after `apk upgrade pango` (edge skew at build time) |
| Battery / charging | **Yes** | PM7250B charger + `qcom_qg` fuel gauge |
| Internal storage (UFS) | **Yes** | 128 GB Micron, all partitions — needs the local DTS patch |
| Modem (remoteproc) | **Yes** | stable once UFS gives `rmtfs` its partitions |
| WiFi | **Yes** | WCN3990, associates, IPv4+IPv6, internet |
| Bluetooth | Controller up | `hci0` present; pairing untested |
| USB networking | **Yes** | NCM, macOS-native; DHCP races at boot (see FIRST-BOOT.md) |
| Telephony (ModemManager) | **No** | `msm-modem-uim-selection` times out; QMI `uim` returns `Internal`. Next item. |
| Audio | **No** | no sound node in the DTS |
| Sensors | **No** | no `hexagonfs` extracted yet |
| Camera | Untested | |

### History

- 2026-03-18: first GUI on a hybrid boot (Mobian 6.12.68 kernel + pmOS rootfs), simpledrm.
- 2026-08-31: touch on the hybrid boot via an out-of-tree module (see [touch/](touch/)).
- 2026-09-08: found the packaged sm6350 kernel already had everything but one kconfig flag
  ([NEXT-STEPS.md](NEXT-STEPS.md)).
- 2026-09-21: first mainline boot; UFS patch the same evening. The hybrid boot and the
  module work are obsolete but kept as history.

## Pitfalls (read before you start)

This section documents every non-obvious failure we hit, in the order we hit them. Each one cost real time to diagnose.

### 1. pmbootstrap does not run on macOS

pmbootstrap requires `kpartx` and `losetup` (Linux-only). On Apple Silicon, it also crashes with `ValueError: Unsupported machine type 'arm64'`. You can patch `pmb/core/arch.py` to map `arm64` → `aarch64`, but it still fails on missing `kpartx`/`losetup`.

**Fix:** Build on a Linux machine. We used a Bazzite desktop over SSH.

### 2. pmbootstrap PyPI version is ancient

`pip install pmbootstrap` gives v2.1.0 (all versions yanked). pmaports requires >= 2.3.0. The current version (3.9.0) is only available from git.

**Fix:** `pip install git+https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git` — requires Python >= 3.10.

### 3. APKBUILD maintainer must be RFC822

`# Maintainer: username` fails with `'username' is not a valid rfc822 address`. Alpine's `abuild` requires an email address.

**Fix:** `# Maintainer: username <user@example.com>`

### 4. boot-deploy ignores deviceinfo (CRITICAL) — RESOLVED, and the premise was wrong

**2026-09-21:** pmbootstrap 3.11 generated header v0 at base `0x00000000` and it booted. Stock Sony
`boot.000` is header **v2** at base `0x00000000`. Neither v2 nor base 0 is the problem this pitfall
describes; the March failures were AVB (stock `vbmeta`) rejecting unsigned images. Kept for history:

As of pmbootstrap 3.9.0, `boot-deploy` generates boot.img with **header v2 and base 0x0** regardless of what `deviceinfo` says. The generated boot.img will NOT boot on pdx213.

**Fix:** Extract vmlinuz + initramfs + DTB from the pmbootstrap chroot, then build boot.img manually with `mkbootimg.py`:

```bash
# Gzip kernel and append DTB
gzip -c vmlinuz > vmlinuz.gz
cat vmlinuz.gz sm6350-sony-xperia-lena-pdx213.dtb > zImage-combined

# Build boot.img with correct Sony format
python3 mkbootimg.py \
    --kernel zImage-combined \
    --ramdisk initramfs \
    --base 0x10000000 \
    --pagesize 4096 \
    --header_version 0 \
    --cmdline "androidboot.hardware=qcom androidboot.usbcontroller=a600000.dwc3 \
        lpm_levels.sleep_disabled=1 service_locator.enable=1 swiotlb=2048 rootwait \
        pmos_boot_uuid=<UUID> pmos_root_uuid=<UUID> pmos_rootfsopts=defaults" \
    -o boot.img
```

### 5. Kernel cmdline must include partition UUIDs

Without `pmos_boot_uuid` and `pmos_root_uuid` in the cmdline, the pmOS initramfs cannot find the rootfs. The kernel boots, shows console messages, then hangs after "Btrfs loaded".

**Fix:** Extract the UUIDs from pmbootstrap's generated (broken) boot.img:
```bash
python3 -c "f=open('boot.img','rb');d=f.read(4096);print(d[64:64+512].split(b'\x00')[0])"
```
Or run `lsblk -f` on the written SD card.

### 6. Exported rootfs is a sparse image — RESOLVED: it was `deviceinfo_flash_sparse="true"`

A Fairphone 4 leftover. Removed from `deviceinfo`; the export is a raw GPT image now. Kept for history:

`pmbootstrap export` produces an Android sparse image (magic `0xed26ff3a`). If you `dd` it directly to an SD card, you get garbage — no partition table, no filesystem.

**Fix:** Unsparse before writing:
```bash
simg2img sony-pdx213.img sony-pdx213-raw.img
dd if=sony-pdx213-raw.img of=/dev/sdX bs=4M status=progress
```

### 7. Default rootfs is too small for Phosh

The default pmbootstrap rootfs image is ~810MB. Installing `postmarketos-ui-phosh` (791 packages, ~1GB) fills it completely. `apk add` fails with "No space left on device" and leaves the system in a broken state.

**Fix:** After writing the rootfs to SD card, expand the partition before first boot:
```bash
echo ", +" | sfdisk --no-reread -N 2 /dev/sdX   # expand partition
e2fsck -fy /dev/sdXp2                             # check filesystem
resize2fs /dev/sdXp2                               # grow filesystem
```

### 8. GPU driver kills simpledrm (display goes black)

The `msm_dpu` display driver is **built into the kernel** (not a module — cannot be blacklisted). It takes over `fb0` from simpledrm during boot. If the GPU fails to initialize (missing firmware), the display goes permanently black. The OS continues running (SSH still works).

**What we tried:**
- `blacklist msm` / `blacklist adreno` in `/etc/modprobe.d/` — **does not work**, driver is built-in
- Installing `a619_gmu.bin` + `a630_sqe.fw` — driver finds them but still needs `a615_zap.mbn`

### 9. GPU zap shader format matters

The kernel expects the zap shader at `qcom/sm6350/sony/pdx213/a615_zap.mbn`. The Sony ODM partition has it in PIL split format (`.mdt` + `.b00` + `.b01` + `.b02`) and as a combined `.elf`.

**What we tried:**
- Copying the `.elf` as `.mbn` → `error -22` (EINVAL) — wrong format
- Copying the split `.mdt` + `.b*` files → testing (the kernel PIL loader should handle these)

The correct approach is to use `pil-squasher` to produce a proper `.mbn` from the `.mdt` + `.b*` files. This is what the FP4 firmware package does.

### 10. Phone internet requires NAT on the host — or `touch/apk-proxy.py`, or just WiFi now

pmOS USB networking (172.16.42.0/24) has no internet access by default. You need NAT on the host machine to install packages:

```bash
# On the host (Linux laptop connected to phone via USB)
sudo sysctl net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 172.16.42.0/24 -o wlp1s0 -j MASQUERADE

# On the phone
sudo ip route add default via 172.16.42.2
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
```

### 11. `console=tty0` crashes the kernel

Adding `console=tty0` or `loglevel=7` to the kernel cmdline causes the phone to reboot immediately after loading btrfs. Without these parameters, the kernel hangs at btrfs (actually the initramfs is running but producing no visible output on the framebuffer).

### 12. boot.img cmdline gets silently overwritten by rebuilds (CRITICAL)

When rebuilding `boot.img` with `mkbootimg.py`, the output file is overwritten in place. If you rebuild with a different cmdline (e.g. switching from UUID-based to LABEL-based root finding) and then later re-flash the same filename, you may be flashing the wrong cmdline without realizing it.

**What happened to us:** We rebuilt `boot-pmos-full.img` multiple times during debugging — once with UUID-based cmdline (which worked), then with LABEL-based cmdline (which doesn't work). We then spent hours reflashing `boot-pmos-full.img` thinking it was the working version. Every boot hung at "Btrfs loaded" and we blamed the rootfs, the firmware, the initramfs — everything except the cmdline.

**Symptoms:** Kernel boots, shows console messages, hangs after "Btrfs loaded". USB networking comes up (ping 172.16.42.1 works) but SSH is refused. The initramfs is stuck searching for a rootfs it can't find.

**The cmdline that works:**
```
pmos_boot_uuid=<UUID> pmos_root_uuid=<UUID>
```

**The cmdline that does NOT work:**
```
pmos_boot=LABEL=pmOS_i_boot pmos_root=LABEL=pmOS_root
```

The pmOS initramfs does not understand `pmos_boot`/`pmos_root` — it only looks for `pmos_boot_uuid`/`pmos_root_uuid`.

**Prevention:** Always verify the cmdline in a boot.img before flashing:
```bash
python3 -c "f=open('boot.img','rb');d=f.read(4096);print(d[64:64+512].split(b'\x00')[0])"
```

## Hybrid boot (workaround for DSI PLL failure)

Until the pmOS kernel's DSI PLL issue is fixed, use the Mobian 6.12.68 kernel with the pmOS rootfs:

```bash
# Combine Mobian kernel (gzip) + Mobian patched DTB
cat build/zImage-raw build/device-patched.dtb > build/zImage-hybrid

# Build boot.img with pmOS initramfs
python3 mkbootimg.py \
    --kernel build/zImage-hybrid \
    --ramdisk build/initramfs-pmos \
    --base 0x10000000 --pagesize 4096 --header_version 0 \
    --cmdline "androidboot.hardware=qcom androidboot.usbcontroller=a600000.dwc3 \
        lpm_levels.sleep_disabled=1 service_locator.enable=1 swiotlb=2048 rootwait \
        pmos_boot_uuid=ae80e9bb-014b-4271-838e-812e4f53e292 \
        pmos_root_uuid=9a28e679-600d-4b47-9a63-1d638c624286 \
        pmos_rootfsopts=defaults" \
    -o build/boot-pmos-hybrid.img
```

Key files:
- `build/zImage-raw` — Mobian 6.12.68 kernel (gzip, from Mobian weekly SM6350 image)
- `build/device-patched.dtb` — DTB with WiFi, rmtfs, remoteproc, UFS patches
- `build/initramfs-pmos` — pmOS initramfs (from pmbootstrap build)

The Mobian kernel has `CONFIG_DRM_MSM=m` (module) so simpledrm keeps the bootloader framebuffer alive. The pmOS kernel has it built-in, which kills simpledrm when the DSI PLL fails.

**Trade-off**: Display works, but kernel modules from pmOS rootfs (6.19.0) won't load on the 6.12.68 kernel. Touch, WiFi, modem are all modules and won't work until matching 6.12.68 modules are installed.

## Boot format

Verified 2026-09-21 by booting: header **v0**, base **`0x00000000`** (`kernel_addr 0x8000`,
`ramdisk 0x1000000`, `tags 0x100`), page size 4096, **gzip** kernel with appended DTB — i.e.
exactly what pmbootstrap generates from the current `deviceinfo`. Stock Sony ships header v2 at the
same base with a raw kernel. The earlier belief that base `0x10000000` and header v0 were required
came from Mobian's image, which the hybrid boot inherited; "device is corrupt" was AVB, disabled
since 2026-08 on all four `vbmeta` partitions.

`paircheck-bootimg.py boot.img rootfs.img` checks header fields and that the cmdline's
`pmos_root_uuid`/`pmos_boot_uuid` match the rootfs image. `splice-dtb.py` swaps only the DTB in an
existing image for DTB-only kernel changes.

## Packages

### device-sony-pdx213

Device package based on Fairphone 4 (same SoC), on the shared `linux-postmarketos-qcom-sm6350` kernel. Cmdline lives in `kernel-cmdline.conf` (the `deviceinfo_kernel_cmdline*` fields are schema-obsolete). **Local only** — postmarketOS's AI policy forbids submitting this work.

### firmware-sony-pdx213

Proprietary firmware extracted from Sony Open Devices binaries (`SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip`).

**TODO:** Host firmware blobs (similar to [FairBlobs/FP4-firmware](https://github.com/FairBlobs/FP4-firmware)) so pmbootstrap can fetch them automatically.

## Building

```bash
# Clone pmaports and add these packages
cp -r device-sony-pdx213/ /path/to/pmaports/device/testing/
cp -r firmware-sony-pdx213/ /path/to/pmaports/device/testing/

# Build with pmbootstrap (must run on Linux, not macOS)
pmbootstrap init          # select sony-pdx213, console, edge
pmbootstrap install
pmbootstrap export

# Then manually rebuild boot.img (see Pitfall #4 above)
```

## Firmware extraction

Firmware source files:
1. `SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip` from [Sony Open Devices](https://developer.sony.com/open-source/aosp-on-xperia-open-devices)
2. Stock firmware `XQ-BT52_EE UK_62.1.A.0.675.zip`

The ODM partition image inside the SW_binaries zip is a sparse ext2 filesystem. Unsparse it with `simg2img`, then mount to extract firmware. Key files:
- `a615_zap.mdt` + `.b*` → needs `pil-squasher` → `a615_zap.mbn` (GPU zap shader)
- `a619_gmu.bin` (GPU GMU firmware — also in generic `firmware-qcom-adreno-a630`)
- ADSP, CDSP, modem, venus, WLAN firmware in the same location

## Related

- [xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) — Mobian (Debian) port for the same device, used as the testbed for all hardware bring-up
- [sm6350-mainline](https://github.com/sm6350-mainline/linux) — Upstream mainline kernel with SM6350 support
- [pmaports MR !5472](https://gitlab.com/postmarketOS/pmaports/-/merge_requests/5472) — Original pmOS MR for pdx213
