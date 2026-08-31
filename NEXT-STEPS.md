# Next: build `linux-sony` instead of maintaining the hybrid boot

Written 2026-08-31, after touch was confirmed working on the hybrid boot. The short
version: the hybrid boot is a workaround for a panel problem that the SoMainline
kernel appears to solve outright, and switching to it would obsolete most of this
repo's cleverness while also delivering several things on the wish list.

## What was found

[pmaports!9445](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/merge_requests/9445)
— *"Draft: Add 10 Sony devices based on a single shared -next kernel"*, by Marijn
Suijten of SoMainline — adds `device-sony-pdx213` against a new kernel package,
`linux-sony`. Its stated targets per device are: boots to console, available on USB,
working display, working touch, working GPU.

Kernel package (`device/testing/linux-sony/APKBUILD` on branch `sony` of
`Marijn/pmaports`):

| Field | Value |
|---|---|
| Source | `github.com/SoMainline/linux`, branch `marijn/panel-exclusives` |
| Commit | `36782eccb531b0a20150ebb7c742eaca27c2556b` |
| Version | 6.14 |
| Toolchain | clang/LLVM (`LLVM=1`) |
| Config | `config-sony.aarch64`, based on `arch/arm64/configs/defconfig` |
| Modules | `modules_install` is commented out — everything built in |

`config-sony.aarch64` enables, all `=y`:

- `TOUCHSCREEN_S6SY761` — touch in the kernel. No out-of-tree module, so none of the
  point-release ABI trouble documented in `touch/README.md` applies.
- `DRM_MSM` and `DRM_PANEL_SAMSUNG_SOFEF01` — the pdx213's actual panel, which
  `DEVICE_STATUS.md` in the sibling repo records as "not in upstream kernel". Real DRM;
  no simpledrm in this config at all.
- `CHARGER_QCOM_SMB2`, `BATTERY_QCOM_BATTMGR` — battery and charging, the top item on
  the old TODO, which correctly said it needed a kernel rebuild.
- `USB_CONFIGFS_ECM` — so this laptop can do USB networking directly, with no jump
  host and no `touch/apk-proxy.py`.
- `ATH10K_SNOC`, `QCOM_Q6V5_{ADSP,MSS,PAS,WCSS}` — WiFi and the remoteprocs.
- `SCSI_UFS_QCOM` — configured, but the wiki still lists internal storage as Broken,
  so assume the rootfs stays on the microSD until proven otherwise.

## Resolve this first — it contradicts our findings

Their `deviceinfo` and ours disagree about the boot image format, and getting it wrong
is a bootloop:

| | This repo (works) | pmaports!9445 |
|---|---|---|
| Kernel | raw `Image` + appended DTB | `Image.gz` (gzipped) |
| `flash_offset_base` | `0x10000000` | `0x00000000` |
| ramdisk offset | `0x01000000` | `0x02000000` |
| tags offset | `0x00000100` | `0x01e00000` |
| header version | 0 (explicit) | unset |

Our README says gzip causes a `"devices is corrupt"` bootloop and that base must be
`0x10000000`; theirs ships gzip at base `0x00000000`. Both cannot be right for the
same bootloader, so one of them is conditional on something else — most likely how the
image is assembled, or a difference between the ABL's handling of `Image.gz` versus a
raw kernel with an appended DTB. Settle this on paper before flashing: extract the
header from an image built by their tooling and compare it against
`build/boot-pmos-hybrid-touch.img`, which is known to boot.

## Suggested order

1. Clone `SoMainline/linux` at that commit and build it with the MR's config, on
   humboldt in a container (clang, `LLVM=1`, aarch64). `touch/build-module.sh` already
   has a working container pattern to copy.
2. Resolve the boot format question above, then build a boot image.
3. Keep the current SD card intact. `build/rootfs-pmos-raw.img` plus the initramfs
   payload mechanism can rebuild a card from scratch, but the card as it stands now has
   a working Phosh install and 27 GB free — worth preserving.
4. Flash and see whether the panel comes up under real DRM. If it does, most of this
   repo becomes history: no hybrid kernel, no `msm.ko` avoidance, no out-of-tree touch
   module, no GPIO dance.
5. If the panel works, retest the things the hybrid boot could not do: GPU
   acceleration, battery reporting, WiFi, and whether the Phosh *session* still fails
   (see `touch/README.md` — it fails today and always has, and llvmpipe on simpledrm
   was the obvious suspect, which this kernel removes).

## Constraint

postmarketOS [forbids contributions created wholly or partly by generative AI
tools](https://docs.postmarketos.org/policies-and-processes/development/ai-policy.html).
A previous wiki edit from this work was reverted on those grounds. Reading their work
and building it for personal use is fine; submitting anything derived from this repo to
pmOS — wiki, pmaports, or issues — is not. Anything intended for upstream has to be
written and understood independently of this material.
