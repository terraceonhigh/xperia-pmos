# postmarketOS for Sony Xperia 10 III (pdx213)

postmarketOS device packages for the Sony Xperia 10 III (codename: pdx213, SoC: Qualcomm SM6350 / Snapdragon 690 5G).

## Status

**Work in progress.** The kernel boots to console (kernel messages visible on display) but does not yet mount the rootfs. Active debugging.

### What works (pmOS kernel 6.19.0)

| Feature | Status | Notes |
|---------|--------|-------|
| Kernel boot | Yes | Console output visible, multi-core, ~1s to btrfs init |
| Display | Yes | simpledrm (bootloader framebuffer) |
| SPMI/PMIC | Yes | PM6350 arbiter v5 |
| Touch HW | Probing | himax_hx83112 driver initializes |
| SDHCI | Yes | Host controller loads |
| Rootfs mount | **No** | initramfs cannot find rootfs on SD card — debugging |

### What works (verified on Mobian 6.12.68)

| Feature | Status | Notes |
|---------|--------|-------|
| Display | Working | simpledrm (bootloader framebuffer) |
| Touchscreen | Working | Samsung s6sy761, GPIO toggle workaround |
| WiFi | Working | WCN3990/ath10k_snoc |
| Modem | Working | MPSS via remoteproc + rmtfs |
| UFS storage | Working | Internal storage, 79 partitions |
| Bluetooth | Partial | Firmware loads, UART setup fails (EILSEQ) |
| Battery | Partial | PM7250B DTS verified, needs kernel module |
| GPU | Not yet | msm.ko exists, needs DRM setup |

## Boot format (critical)

Sony ABL bootloader on pdx213 requires:
- **Header version 0** (NOT v2)
- **Base address 0x10000000** (NOT 0x0)
- **gzip-compressed kernel + appended DTB**
- Page size 4096

Using header v2 or base 0x0 causes silent boot failure. This was the root cause of all previous boot failures with the pmOS sm6350 kernel.

### Known issue: pmbootstrap boot-deploy ignores deviceinfo

As of pmbootstrap 3.9.0, `boot-deploy` generates boot.img with header v2 and base 0x0 regardless of deviceinfo settings. **Workaround**: extract vmlinuz + initramfs + DTB from the pmbootstrap build, then package manually:

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
    --cmdline "androidboot.hardware=qcom androidboot.usbcontroller=a600000.dwc3 ..." \
    -o boot.img
```

### Known issue: sparse rootfs image

`pmbootstrap export` produces a sparse Android image. You must unsparse it before `dd`:

```bash
simg2img sony-pdx213.img sony-pdx213-raw.img
dd if=sony-pdx213-raw.img of=/dev/sdX bs=4M status=progress
```

## Packages

### device-sony-pdx213

Device package with corrected boot parameters, based on the Fairphone 4 (same SoC) as reference. Uses `linux-postmarketos-qcom-sm6350` shared kernel.

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
```

**Note:** pmbootstrap does not run on macOS — it requires `kpartx` and `losetup` (Linux-only). Also fails with `arm64` arch on Apple Silicon. Build on a Linux machine.

## Firmware extraction

Firmware source files:
1. `SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip` from [Sony Open Devices](https://developer.sony.com/open-source/aosp-on-xperia-open-devices)
2. Stock firmware `XQ-BT52_EE UK_62.1.A.0.675.zip`

Extraction requires sinunpack (LZ4 variant from j4nn/sinunpack) and simg2img. See the [xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) repo for detailed extraction notes.

## Related

- [xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) — Mobian (Debian) port for the same device, used as the testbed for all hardware bring-up
- [sm6350-mainline](https://github.com/sm6350-mainline/linux) — Upstream mainline kernel with SM6350 support
- [pmaports MR !5472](https://gitlab.com/postmarketOS/pmaports/-/merge_requests/5472) — Original pmOS MR for pdx213
