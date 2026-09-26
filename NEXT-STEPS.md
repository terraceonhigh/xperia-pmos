# Next steps

## Current state (2026-09-26)

The plan below is done: the phone boots mainline pmOS (kernel r3 on the card, boot image
`build/boot-pmos-sm6350-7.2.0-r3-pdmapper-2026-09-21.img`). Display, GPU, touch, Phosh, battery,
UFS, WiFi and USB networking work. The live problem is the **modem watchdog loop** (DOG fires
~40 s after every modem start), which blocks telephony. The open list and the ranked suspects are
at the bottom of this file ("2026-09-21 evening: closed" onward).

Every remaining modem test needs the phone on USB (swap `ipa_fws.mbn`, splice a DTB with IPA
disabled, run `tqftpserv` verbose), so the next session has to be hands-on. Builds run on the
Linux pmbootstrap host (humboldt), not on this Mac (Pitfall #1).

Things that don't need the phone: diff the March Mobian DTB against `build/pdx213-r2-ufs.dtb`
for the IPA node and the rmtfs-mem address (suspect 3 below), and prepare an IPA-disabled DTB for
`splice-dtb.py` (suspect 1). The Mobian `device-patched.dtb` is not on this Mac any more; it
comes back via `../xperia-mobian/restore.sh` (GitHub Release download), and its patch scripts
(`patch-rmtfs.py` for the `0x9b000000` rmtfs-mem node, `patch-remoteproc.py`, `patch-wifi.py`) show the edits. Needs `dtc` (not installed here).

---

# Original plan: reuse the packaged sm6350 kernel instead of building one

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

## 2026-09-21: first-boot images built

- `pmbootstrap install --password 1234` (numeric, keypad constraint) + `export` on
  humboldt, UI phosh, systemd default, our `build/pdx213_key.pub` installed for `user`.
- **Dropped `deviceinfo_flash_sparse`** — a Fairphone leftover. With it, the exported
  rootfs is an Android sparse image, which cannot be `dd`'d to a card and which shifts
  every offset (my first pair-check read garbage partition entries for exactly this
  reason). Raw GPT image now.
- `paircheck-bootimg.py boot.img rootfs.img`: parses the boot header, reports kernel
  compression, and confirms `pmos_root_uuid`/`pmos_boot_uuid` in the cmdline match the
  ext4 UUIDs inside the rootfs image. The silent failure mode it guards against is a
  black screen with the initramfs waiting forever for a root that isn't there.
- Stock Sony `boot.000` carries a **raw** arm64 `Image` (no gzip). pmOS ships gzip.
  March's "gzip bootloops" was observed with stock AVB still enabled — "device is
  corrupt" is AVB's message — so gzip on this ABL is unproven either way. Rollback is
  one `fastboot flash boot_a build/boot-pmos-hybrid-touch.img`.
- Images: `build/boot-pmos-sm6350-7.2.0-2026-09-21.img`,
  `build/rootfs-pmos-sm6350-2026-09-21.img` (2.0 GiB raw; initramfs grows root on
  first boot).

## 2026-09-21 evening: closed

First mainline boot worked; UFS patch the same evening unblocked modem, WiFi and
`qbootctl`. Everything above this line is done. See `FIRST-BOOT.md` and the README status
table. Still open, roughly in order:

1. **Telephony.** `msm-modem-uim-selection` waits 45 s, QMI `uim` answers `Internal`,
   ModemManager never starts. SIM is present (Macau). First check: restart the two units
   after the modem has been up a few minutes; if it then works, it's a boot-order timeout.
2. **Audio.** No sound node in the pdx213 DTS. ADSP is running and the q6 modules load;
   needs the `sound {}` machine node + codec (Sony downstream DTBO has it).
3. **Sensors.** Extract `hexagonfs` from the vendor image, re-add `hexagonrpcd`.
4. **Bluetooth pairing test.** Controller is up.
5. Small ones: backlight `EPROTO`; DSI PLL first-probe errors; USB DHCP race (report
   upstream? — no, policy; just document the workaround); `pkgrel` bumps on the shared
   kernel config are local forever.

### Telephony — where it stands after 2026-09-21 night

Not rmtfs/EFS anymore (that was crash #1 on the first boot and UFS fixed it). The modem now
comes up, runs WLAN firmware fine, then the modem-side watchdog fires **~40 s after each
start**: `dog_hal_common.c:180: DOG detects stalled initialization, triage with IMAGE OWNER`.
Recovery loops forever; `msm-modem-uim-selection` (waits 45 s, QMI `uim` → `Internal`) fails,
so ModemManager never starts.

Tried and **ruled out**:
- `qcom_pd_mapper` SM6350 table missing `msm/modem/root_pd`. Sony's `modemr.jsn` declares it
  (instance 180: `tms/servreg`, `tms/pdr_enabled`, `gps/gps_service`); the in-kernel table
  (and torvalds master) has only `wlan_pd` for sm6350. Added `&mpss_root_pd_gps_pdr`
  (`kernel-patches/0002-…`, kernel r3, verified loaded as `#4`). **No change** — same DOG,
  same cadence. Left in place (harmless, matches the jsn). Untried variant: plain
  `mpss_root_pd` (no gps) — low odds given the jsn, but it's the cheap remaining pd-mapper move.

Still-open suspects, cheapest first:
1. **IPA firmware choice.** Package ships `lagoon_ipa_fws.*` → `ipa_fws.mbn`; vendor also has
   a plain `ipa_fws.*` set (different `.b01` size). IPA logs no error, but `qcom,gsi-loader =
   "self"` means a wrong blob fails quietly on the modem side. Test: swap the file on the
   phone (`/usr/lib/firmware/qcom/sm6350/sony/pdx213/ipa_fws.mbn`), reboot. Or disable IPA
   in the DTS for one boot (DTB-only → `splice-dtb.py`): if the modem stays up, it's IPA.
2. **tqftpserv.** Modem writes `server_check.txt`/`mcfg.tmp` (so QRTR works) and asks for
   `ota_firewall/ruleset` (missing, rejected — usually harmless). Check what else it asks for
   with `tqftpserv` in verbose mode, and that `modem_pr/mcfg/configs` is where it looks.
3. **Compare against the March Mobian setup** that had a stable modem: kernel 6.12, userspace
   `pd-mapper` + `tqftpserv` + `rmtfs`, IPA *not* enabled, rmtfs-mem at `0x9b000000` (upstream:
   `0xfe901000`). Two real differences: IPA, and the rmtfs-mem address.
4. Sony's own ramdump/DIAG would name the stalled task; no path to that without downstream tools.

Kernel state on the card: **r3** (UFS + pd-mapper patches). Boots and runs everything else.
Rollback pair kept in `build/`: `boot-pmos-sm6350-7.2.0-r2-ufs-2026-09-21.img` + r2 apk.
`deviceinfo_flash_kernel_on_update` is **false** on the device (set before installing r3 so
boot-deploy would build `/boot/boot.img` without writing `boot_a` itself). Never run
`apk upgrade --prune/--available`: it would remove our local device/firmware packages.
