# postmarketOS for Sony Xperia 10 III (pdx213)

postmarketOS device packages for the Sony Xperia 10 III (codename: pdx213, SoC: Qualcomm SM6350 / Snapdragon 690 5G).

## Status

**Work in progress.** The sm6350-mainline 6.11 kernel has been verified to boot on this device with the correct boot format parameters. Full pmOS integration is ongoing.

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

### Boot format (critical)

Sony ABL bootloader on pdx213 requires:
- **Header version 0** (NOT v2)
- **Base address 0x10000000** (NOT 0x0)
- **gzip-compressed kernel + appended DTB**
- Page size 4096

Using header v2 or base 0x0 causes silent boot failure. This was the root cause of all previous boot failures with the pmOS sm6350 kernel.

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

# Build with pmbootstrap
pmbootstrap init          # select sony-pdx213, phosh
pmbootstrap install
pmbootstrap export
```

## Firmware extraction

Firmware source files:
1. `SW_binaries_for_Xperia_Android_13_4.19_v1b_lena.zip` from [Sony Open Devices](https://developer.sony.com/open-source/aosp-on-xperia-open-devices)
2. Stock firmware `XQ-BT52_EE UK_62.1.A.0.675.zip`

Extraction requires sinunpack (LZ4 variant from j4nn/sinunpack) and simg2img. See the [xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) repo for detailed extraction notes.

## Related

- [xperia-mobian](https://github.com/terraceonhigh/xperia-mobian) — Mobian (Debian) port for the same device, used as the testbed for all hardware bring-up
- [sm6350-mainline](https://github.com/sm6350-mainline/linux) — Upstream mainline kernel with SM6350 support
- [pmaports MR !5472](https://gitlab.com/postmarketOS/pmaports/-/merge_requests/5472) — Original pmOS MR for pdx213
