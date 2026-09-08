# Next: reuse the packaged sm6350 kernel instead of building one

Written 2026-08-31, rewritten 2026-09-08 after finding that almost everything this
plan set out to build already ships in postmarketOS's package tree today. The short
version got shorter: don't build a kernel — flip one config flag on an existing one.

## What changed since the first draft

The original plan (below, kept for the boot-format warning) was to clone
`SoMainline/linux` at a Draft MR's pinned commit and cross-compile it in a container.
That's no longer the right target. Two things found on 2026-09-08:

1. The [wiki page](https://wiki.postmarketos.org/wiki/Sony_Xperia_10_III_(sony-pdx213))
   names a second, more active fork: **`sm6350-mainline/linux`** — last pushed
   2026-09-01, versioned branches through `sm6350-7.2.y`. Its `sm6350-sony-xperia-lena-pdx213.dts`
   already wires up touch (`s6sy761` on `988000.i2c`, same bus we fought with), the real
   panel (`samsung,sofef01-m-ams597ut04` under `DRM_MSM`/`mdss_dsi0`), the PM7250B
   charger + fuel gauge + ADC (`status = "okay"`, monitored-battery, thermistor table),
   and Bluetooth (`qcom,wcn3991-bt` on UART1). All on the same I²C bus and same GPIOs
   documented in `touch/README.md` — this is upstream of the point where our March
   PM7250B DTS work regressed touch, and it does *not* regress there.

2. **`sm6350-mainline/linux` is not just "prior art to read" — it's already the exact
   source of a package sitting in `pmaports/device/community/`:**
   `linux-postmarketos-qcom-sm6350`, currently `pkgver=7.2.0`, built from tag
   `v7.2.0-sm6350` of that same repo. It's the kernel Fairphone 4's device package
   (`device-fairphone-fp4`) already depends on and pmOS already builds. `dtbs_install`
   in its `package()` installs every DTB the source tree produces — including
   `sm6350-sony-xperia-lena-pdx213.dtb` — with zero pdx213-specific packaging.

Checked `config-postmarketos-qcom-sm6350.aarch64` (the config this package actually
ships) against what pdx213 needs:

| Config | State | Needed for |
|---|---|---|
| `CONFIG_DRM_MSM` / `_MDSS` / `_DPU` / `_DSI` | `=y` | real panel, not simpledrm |
| `CONFIG_DRM_PANEL_SAMSUNG_SOFEF01` | `=m` | the pdx213 panel specifically |
| `CONFIG_CHARGER_QCOM_SMB2` | `=y` | battery — the thing we could never build |
| `CONFIG_SCSI_UFS_QCOM` | `=y` | internal storage controller |
| `CONFIG_ATH10K_SNOC` | `=m` | WiFi |
| `CONFIG_BT_HCIUART_QCA` | `=y` | Bluetooth |
| `CONFIG_TOUCHSCREEN_S6SY761` | **not set** | touch — the one thing missing |
| `CONFIG_USB_CONFIGFS_ECM` | not set | Mac USB networking, convenience only |

Everything this repo spent two hardware cycles chasing an out-of-tree module for is
one kconfig line away from being in-tree, in a kernel pmOS already builds.

## The one real gap: UFS is not wired for pdx213

`CONFIG_SCSI_UFS_QCOM=y` is compiled in, and the shared `sm6350.dtsi` has the UFS
host controller and PHY nodes — but `sm6350-sony-xperia-lena-pdx213.dts` never
references `&ufs_mem_hc` or `&ufs_mem_phy` to turn them on or wire regulator
supplies. The wiki's "Internal storage: Broken" and "blocks modem and wifi" notes are
about this specific gap, not the kernel config.

We may not need to close it to get WiFi and modem, though:
[xperia-mobian/DEVICE_STATUS.md](../xperia-mobian/DEVICE_STATUS.md) already has both
working on this exact hardware, via `patch-wifi.py` and `patch-remoteproc.py`,
without touching UFS at all — that route pulls firmware straight from the rootfs
instead of reading calibration data off a UFS partition at runtime. Worth trying
that route again here before spending a session bisecting UFS regulator wiring from
scratch.

## Leaner suggested order

1. On the Bazzite pmbootstrap host, bump `pkgrel` and flip
   `CONFIG_TOUCHSCREEN_S6SY761` to `m` (or `y`) in
   `device/community/linux-postmarketos-qcom-sm6350/config-postmarketos-qcom-sm6350.aarch64`,
   rebuild the kernel package. No container, no manual cross-compile — this is a normal
   `pmbootstrap build`.
2. Build `device-sony-pdx213` and `firmware-sony-pdx213` as new packages, using
   `device-fairphone-fp4` / `firmware-fairphone-fp4` as the template (same kernel
   dependency, same SoC family) — this was already the intended reference per
   `xperia-mobian/CLAUDE.md`. Firmware paths pdx213.dts expects:
   `qcom/sm6350/sony/pdx213/{adsp,cdsp,modem,ipa_fws,a615_zap}.mbn` and BT's
   `crnv32u.bin` — all of which we already extracted and have sitting in
   `xperia-mobian`'s rootfs from the Mobian work.
3. **Resolve the boot format before flashing anything** — this did not go away:

   | | This repo (works, hybrid boot) | Fairphone 4's real deviceinfo | pmaports!9445 (Sony devices, Draft) |
   |---|---|---|---|
   | `flash_offset_base` | `0x10000000` | `0x00000000` | `0x00000000` |
   | kernel offset | (n/a, concatenated) | `0x00008000` | (unset in MR) |
   | ramdisk offset | `0x01000000` | `0x01000000` | `0x02000000` |
   | tags offset | `0x00000100` | `0x00000100` | `0x01e00000` |

   FP4 and the Sony MR agree with each other and disagree with us on base address.
   That's two independent data points against our one — raises the odds the
   `0x10000000` requirement is an artifact of *our own* boot-image assembly
   (`build-touch-boot.py`/Sony ABL header reuse), not a hard Sony ABL constraint. Still:
   verify by extracting the header from a `pmbootstrap export`-built image and diffing
   against `build/boot-pmos-hybrid-touch.img` (known-good) before the first flash, per
   the original plan.
4. Keep the SD card as-is. It has a working Phosh install and 27 GB free; nothing here
   requires touching it until a new boot image is ready to test.
5. Once flashed: confirm what actually broke or worked that the hybrid boot couldn't
   test — GPU acceleration, battery reporting through the packaged `CHARGER_QCOM_SMB2`
   driver, Bluetooth, and whether the Phosh *session* failure (pre-existing, see
   `touch/README.md`) was specific to simpledrm/llvmpipe, which this kernel removes
   entirely.

## Constraint (unchanged, more relevant now)

postmarketOS [forbids contributions created wholly or partly by generative AI
tools](https://docs.postmarketos.org/policies-and-processes/development/ai-policy.html).
A previous wiki edit from this work was reverted on those grounds. Reading and
building against `sm6350-mainline/linux` and the existing pmaports packages for
personal use is fine — it's normal open-source consumption, not a pmOS contribution.
But this plan is now one config flag and a device package away from something that
*looks like* a pmaports submission. If any of `device-sony-pdx213`,
`firmware-sony-pdx213`, or the kconfig change is ever meant to go upstream, it has to
be rewritten and understood independently of this material — not copied from here.
