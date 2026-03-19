# Reproducing the pmOS Port on Sony Xperia 10 III (pdx213)

This document describes exactly how to go from zero to a running postmarketOS
installation with a visible GUI (Phosh/greetd) on the Sony Xperia 10 III.

Last verified: **2026-03-18 22:30 PST**

## Prerequisites

### Hardware
- Sony Xperia 10 III (XQ-BT52, codename pdx213, SoC SM6350/Snapdragon 690 5G)
- Bootloader **must be unlocked** (`fastboot oem unlock`)
- microSD card (16GB+, Class 10 recommended)
- USB cable (USB-C)
- A **Linux machine** with:
  - fastboot (`android-tools` or standalone)
  - SD card reader
  - Python 3.10+ (for pmbootstrap)
  - `sfdisk`, `e2fsck`, `resize2fs` (usually in `util-linux`, `e2fsprogs`)

### Software sources

| Component | Source | Version | SHA256 |
|-----------|--------|---------|--------|
| Mobian weekly image | https://images.mobian.org/qcom/weekly/ | `mobian-sm6350-phosh-20260308` | `b6c6209a2c063d588f2062c4f3b89a0ba49407cf77eb9afaf4cef15a0aa7fda2` (tar.xz) |
| Mobian pdx213 boot image | (inside the above tarball) | 20260308 | `f75ed379a6a1decb23090566d4caacfd9d24b2493dffdb379a4216f87ce4d8c8` |
| Sony Open Devices firmware | https://developer.sony.com/open-source/aosp-on-xperia-open-devices | `SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip` | `2596a794c0989bba32fa5b65c5caedcc5664db7b3bb1772fce1bd388320d2c2c` |
| pmaports | https://gitlab.com/postmarketOS/pmaports.git | master, `817ed870e` | — |
| pmbootstrap | https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git | 3.9.0 (`a758b3ac`) | — |
| This repo (device packages) | https://github.com/terraceonhigh/xperia-pmos | `31e5ad4` | — |
| Mobian repo (reference) | https://github.com/terraceonhigh/xperia-mobian | — | — |

## Overview

The port uses a **hybrid boot**: the Mobian 6.12.68 kernel (which has a working
display driver as a loadable module, keeping simpledrm alive) paired with a
pmOS rootfs built by pmbootstrap. This is necessary because the pmOS 6.19.0
kernel has `CONFIG_DRM_MSM` built-in, which takes over from simpledrm and then
fails to drive the DSI panel (`DSI PLL(0) lock failed`).

## Step-by-step

### 1. Extract the Mobian kernel and DTB

The Mobian weekly image contains a pre-built boot image for pdx213. We need
the kernel and DTB from it.

```bash
# Download Mobian weekly
wget https://images.mobian.org/qcom/weekly/mobian-sm6350-phosh-20260308.tar.xz
# Verify: sha256 = b6c6209a2c063d588f2062c4f3b89a0ba49407cf77eb9afaf4cef15a0aa7fda2

# Extract — contains .boot-xperia-lena-pdx213.img and .rootfs.img
tar xf mobian-sm6350-phosh-20260308.tar.xz

# The boot image contains a gzip kernel + appended DTB as the "kernel" field.
# Extract with unpackbootimg or manually:
# - Kernel starts at page 1 (offset 4096)
# - It's gzip compressed with DTB appended after the gzip stream
```

The kernel (`zImage-raw`) is a gzip-compressed Linux 6.12.68 ARM64 Image with
the pdx213 DTB concatenated at the end. For this port, we use a patched DTB
that adds WiFi, rmtfs (modem), remoteproc, and UFS support. See the
[xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) repo for
the DTS patch pipeline (`patch-rmtfs.py`, `build.sh`).

**Key artifact hashes:**

| File | Description | SHA256 |
|------|-------------|--------|
| `zImage-raw` | Mobian 6.12.68 gzip kernel | `9aef69366f461bac551e2ead2a638230961dfc2ae5106bbc6ec3a092830195b4` |
| `device-patched.dtb` | Patched DTB (WiFi+rmtfs+rproc+UFS) | `28bfdcaf591970831c887cddb44199d3566181d7bcbd697ea452be6b013f98a9` |
| `device-wifi-rproc.dts` | Base DTS before rmtfs patch | `61b07dd58ca176ab9adc7d8af2c4018bf5e708af8635257ab45083a0d70413fc` |

### 2. Build the pmOS rootfs with pmbootstrap

pmbootstrap must run on **Linux** (not macOS — requires `kpartx`, `losetup`).
Requires Python >= 3.10.

```bash
# Install pmbootstrap from git (PyPI version is ancient)
pip install git+https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git
# Verify: pmbootstrap --version → 3.9.0

# Clone pmaports
git clone https://gitlab.com/postmarketOS/pmaports.git
cd pmaports
git checkout 817ed870e  # or latest master

# Copy corrected device package from this repo
git clone https://github.com/terraceonhigh/xperia-pmos.git /tmp/xperia-pmos
cp /tmp/xperia-pmos/device-sony-pdx213/* device/testing/device-sony-pdx213/

# IMPORTANT: The APKBUILD must have stripped firmware deps for first build
# (firmware-sony-pdx213-* packages don't exist yet)
# Edit device/testing/device-sony-pdx213/APKBUILD:
#   Remove all firmware-sony-pdx213-* lines from depends

# Generate checksums
pmbootstrap checksum device-sony-pdx213

# Build
pmbootstrap init    # device: sony-pdx213, UI: console (or phosh), channel: edge
pmbootstrap install
pmbootstrap export  # → /tmp/postmarketOS-export/
```

### 3. Unsparse the rootfs image

pmbootstrap exports a **sparse Android image**. You must unsparse it:

```bash
simg2img /tmp/postmarketOS-export/sony-pdx213.img rootfs-pmos-raw.img
# Or use the simg2raw.py script from the xperia-mobian repo
```

**Rootfs hash:** `51c9fe90340db1d93a7a00d6bf235cfc72d9de4654b52fef3606e07797a11571`

### 4. Extract the pmOS initramfs and kernel cmdline UUIDs

pmbootstrap generates a boot.img with the wrong format (header v2, base 0x0)
but it contains the correct initramfs and cmdline with partition UUIDs.

```bash
# Extract initramfs from pmbootstrap's chroot
cp /path/to/.pmbootstrap/chroot_rootfs_sony-pdx213/boot/initramfs initramfs-pmos
# SHA256: 43a88e21e7af9bef5f39e76446bd2bc2a87acaa94054cb06e77af98fef0f221e

# Extract UUIDs from pmbootstrap's (broken) boot.img
python3 -c "
f = open('/tmp/postmarketOS-export/boot.img', 'rb')
d = f.read(4096)
print(d[64:64+512].split(b'\x00')[0].decode())
"
# Look for pmos_boot_uuid=... and pmos_root_uuid=...
# Our build: boot=ae80e9bb-014b-4271-838e-812e4f53e292
#            root=9a28e679-600d-4b47-9a63-1d638c624286
```

### 5. Write rootfs to SD card

```bash
# Write raw rootfs
sudo dd if=rootfs-pmos-raw.img of=/dev/sdX bs=4M status=progress

# Expand root partition to fill the card
echo ", +" | sudo sfdisk --no-reread -N 2 /dev/sdX
sudo e2fsck -fy /dev/sdXp2
sudo resize2fs /dev/sdXp2

# Verify UUIDs match what you extracted in step 4
lsblk -f /dev/sdX
```

### 6. Extract GPU firmware from Sony Open Devices

```bash
# Download SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip
# SHA256: 2596a794c0989bba32fa5b65c5caedcc5664db7b3bb1772fce1bd388320d2c2c
unzip SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip
# Contains SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.img (sparse ext2)

# Unsparse the ODM image
simg2img SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.img odm-raw.img
# Or: python3 simg2raw.py < .img 2>/dev/null > odm-raw.img

# Mount and extract firmware
sudo mount -o ro odm-raw.img /mnt
# GPU zap shader — needs pil-squasher
cd /mnt/firmware
python3 pil-squash.py /tmp/a615_zap.mbn a615_zap.mdt
# SHA256 of a619_gmu.bin: 9ef10b2b179054fb57af25008e29feb24300c2eaaeabf907bd741786bbacbf2e
# SHA256 of a630_sqe.fw:  57eece0d8032b0ab2467d856c304ce22f502ae8d4b2f8169b9d1ca890ceb463c
sudo umount /mnt
```

### 7. Install firmware and Phosh on SD card rootfs

```bash
# Mount the pmOS rootfs
sudo mount /dev/sdXp2 /mnt

# Install GPU firmware
sudo mkdir -p /mnt/lib/firmware/qcom/sm6350/sony/pdx213
sudo cp a619_gmu.bin a630_sqe.fw /mnt/lib/firmware/qcom/
sudo cp a615_zap.mbn /mnt/lib/firmware/qcom/sm6350/sony/pdx213/

# Disable boot-deploy (it generates wrong boot.img format and can corrupt boot_a)
sudo chmod 000 /mnt/usr/bin/boot-deploy

# Set up internet for package installation (do this AFTER first boot instead,
# or chroot into the rootfs to install packages)

sudo umount /mnt
```

To install Phosh, either:
- Boot the system first, set up NAT for internet (see README.md pitfall #10), then `apk add postmarketos-ui-phosh`
- Or chroot into the rootfs from the Linux machine and install packages there

### 8. Build the hybrid boot.img

```bash
# Concatenate Mobian kernel + patched DTB
cat zImage-raw device-patched.dtb > zImage-hybrid

# Build boot.img with pmOS initramfs
python3 mkbootimg.py \
    --kernel zImage-hybrid \
    --ramdisk initramfs-pmos \
    --base 0x10000000 \
    --pagesize 4096 \
    --header_version 0 \
    --cmdline "androidboot.hardware=qcom androidboot.usbcontroller=a600000.dwc3 \
lpm_levels.sleep_disabled=1 service_locator.enable=1 swiotlb=2048 rootwait \
pmos_boot_uuid=ae80e9bb-014b-4271-838e-812e4f53e292 \
pmos_root_uuid=9a28e679-600d-4b47-9a63-1d638c624286 \
pmos_rootfsopts=defaults" \
    -o boot-pmos-hybrid.img

# CRITICAL: Verify the cmdline before flashing
python3 -c "f=open('boot-pmos-hybrid.img','rb');d=f.read(4096);print(d[64:64+512].split(b'\x00')[0])"
# Must contain pmos_boot_uuid and pmos_root_uuid (NOT pmos_boot=LABEL)
```

**Hybrid boot.img hash:** `1be9dac0c9309ed10b9513bc8593b4171dd9c00c0c27df043dde4001b99efef3`

### 9. Flash and boot

```bash
# Insert SD card into phone
# Enter fastboot: hold Volume Up while plugging USB cable

# Flash boot.img
fastboot flash boot_a boot-pmos-hybrid.img

# Reboot
fastboot reboot
```

### 10. What you should see

1. Kernel console messages flash briefly on screen (~1 second)
2. Screen goes dark briefly (initramfs switching VTs)
3. pmOS bootsplash animation
4. **greetd login screen** appears (Phosh compositor running)

Login credentials: `user` / `password` (or whatever you set during `pmbootstrap install`)

### 11. What does NOT work yet

| Feature | Why | Fix |
|---------|-----|-----|
| Touch | s6sy761 module version mismatch (rootfs=6.19.0, kernel=6.12.68) | Install 6.12.68 kernel modules |
| USB networking | Mobian kernel has RNDIS only, pmOS expects ECM | Not critical if display works |
| WiFi | ath10k_snoc module version mismatch | Install 6.12.68 kernel modules |
| Modem | Module version mismatch | Install 6.12.68 kernel modules |
| GPU acceleration | msm.ko intentionally not loaded (keeps simpledrm alive) | Fix DSI PLL in upstream kernel |

## Tools

This repo and the [xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) repo contain helper scripts:

| Script | Purpose |
|--------|---------|
| `mkbootimg.py` | Build Android boot images (header v0/v2) |
| `pil-squash.py` | Convert PIL split firmware (.mdt + .b*) to single .mbn |
| `simg2raw.py` | Convert Android sparse images to raw (pipe: `< sparse > raw`) |
| `parse-bootimg.py` | Parse and display Android boot image header fields |
| `build.sh` | Full Mobian boot image build pipeline (patch DTS → compile → mkbootimg) |
| `patch-rmtfs.py` | Add rmtfs, UFS, firmware-name properties to DTB |

## Boot format reference

Sony ABL bootloader on pdx213 requires:

| Parameter | Value | Wrong value (causes silent failure) |
|-----------|-------|-------------------------------------|
| Header version | **0** | 2 |
| Base address | **0x10000000** | 0x00000000 |
| Kernel format | **gzip compressed + appended DTB** | uncompressed Image |
| Page size | **4096** | — |

## Checkpoint hashes

| Checkpoint | File | SHA256 |
|------------|------|--------|
| 01-mobian-working | boot.img | `59a1167af6f3ce2331d7881fba06e69695480ca9749130380c04919399ebe115` |
| 02-pmos-first-boot | boot.img | `8b7022dd4cd0f0357c8b65d34adabf51122a5babe72e1fbd8a12f6d4fccecd63` |
| 02-pmos-first-boot | rootfs-raw.img | `51c9fe90340db1d93a7a00d6bf235cfc72d9de4654b52fef3606e07797a11571` |
| 03-pmos-hybrid-display | boot.img | `1be9dac0c9309ed10b9513bc8593b4171dd9c00c0c27df043dde4001b99efef3` |
