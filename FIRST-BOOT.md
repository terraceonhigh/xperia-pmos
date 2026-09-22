# First boot on the mainline kernel — 2026-09-21

postmarketOS edge, `linux-postmarketos-qcom-sm6350` 7.2.0-r1 (sm6350-mainline tag
`v7.2.0-sm6350` + `CONFIG_TOUCHSCREEN_S6SY761=m`), Phosh, systemd. Rootfs on a 16 GB
microSD, boot image on `boot_a`. Raw snapshot of everything below is in
`build/first-boot-snapshot-2026-09-21.txt` (gitignored).

## What came up on the first try

| Feature | Evidence |
|---|---|
| Boot format | base `0x00000000`, header v0, **gzip kernel**, appended DTB — booted. The March "gzip bootloops" was AVB, not the ABL. |
| Panel / DRM | Phosh greeter and session render. `simpledrm` hands off to `msm_dpu` + `panel-samsung-sofef01`. Screen blanks after the handoff; power tap wakes it. |
| GPU | `phoc`: `EGL driver name: msm`, `GL vendor: freedreno`, OpenGL ES 3.2, Mesa 26.2.3. `a630_sqe.fw` / `a619_gmu.bin` load from the rootfs (the initramfs-stage "failed to load" lines are the early attempt). |
| Touch | in-tree `s6sy761` on `988000.i2c` → `event3`; PIN typed on the greeter keypad. |
| Battery | `pm7250b-charger: Charging`, `qcom_qg` capacity 100 % / 4.43 V, `tcpm-source-psy` 5 V. |
| Bluetooth | `hci0` up, controller `02:00:8E:5A:A0:47`. Not paired to anything yet. |
| ADSP / CDSP | both `running` with our `firmware-sony-pdx213` blobs. |
| USB | NCM gadget; macOS binds `AppleUSBNCMData` natively — no jump host. |
| Phosh session | **works.** The hybrid boot's session crash was simpledrm/llvmpipe. |

## What didn't, and why

- **Modem crash loop** (`EFS: rmts_get_buffer api failed`, 525 crashes in ~20 min).
  `rmtfs` can't open `/dev/disk/by-partlabel/modemst1` — those partitions are on UFS, and
  the upstream pdx213 DTS never enables UFS. `systemctl stop rmtfs` ends the loop (the
  remoteproc write races recovery and returns EIO; stopping rmtfs is what works).
- **No `wlan0`.** WCN3990's WiFi firmware runs on the modem DSP; same root cause.
- **Root partition not grown.** The initramfs resized the *filesystem* to the 2.6 G
  partition but did not grow the partition. Fixed online with `sfdisk -d` → edit
  `last-lba` and p2 `size=` → `sfdisk --no-reread -f` → `partx -u` → `resize2fs`.
  Now 14.4 G.
- **GTK4 apps (Console, Settings, Mobile Settings) timed out on launch.** Not GPU:
  `Error relocating /lib/libgtk-4.so.1: pango_font_description_set_width: symbol not
  found` — gtk4 4.24.0 built against pango 1.58, rootfs had 1.57.1 (edge package skew
  at build time). `apk upgrade pango` through `touch/apk-proxy.py` fixed it.
- **USB DHCP race.** `unudhcpd@usb0` starts at boot before developer mode's
  NetworkManager connection puts `172.16.42.1` on `usb0`, so the Mac's DHCP gets no
  answer and falls back to link-local. Workaround on the Mac:
  `sudo ifconfig enN inet 172.16.42.2 netmask 255.255.0.0`.
- `qbootctl.service` failed — reads the A/B boot-control block from UFS. Check
  `fastboot getvar slot-retry-count:a` next time in fastboot.
- Audio: no sound card (DTS has no sound node). Expected.
- Notes, not blockers: `dsi_pll_10nm_vco_prepare *ERROR* PLL(0) lock failed` twice at
  probe with stack traces (first attempt before clocks; panel works). Phosh
  `Setting backlight on DSI-1 failed: EPROTO` — brightness control.

## UFS fix — confirmed 2026-09-21, second boot

`kernel-patches/0001-arm64-dts-qcom-sm6350-sony-pdx213-enable-UFS.patch` — enables
`&ufs_mem_hc` / `&ufs_mem_phy` with supplies from Sony's stock DTBO overlay
(`vcc=L7E`, `vccq2=L12A`, `vdda-phy=L18A`, `vdda-pll=L22A`), which are exactly
Fairphone 4's mainline override on the same PMIC set. Applied to the shared kernel
package as pkgrel 2. DTB-only change, so `splice-dtb.py` swaps it into the existing
card-matched boot image rather than regenerating one (keeps `pmos_root_uuid`).

Result on boot: `ufshcd-qcom 1d84000.ufshc` probes, `sda` = Micron MT128GASAO4U21
(128 GB, 4096-byte blocks), full GPT visible, `/dev/disk/by-partlabel/{modemst1,modemst2,
fsc,fsg,boot_a,boot_b}` resolve. `rmtfs` starts clean, **modem `running` with zero
crashes**, `wlan0` appears (ath10k loads `WLAN.HL.3.3.1` from the modem DSP), and
`qbootctl` marks slot `a` successful. Harmless probe notes: `vdd-hba-supply` and
`vccq-supply` "not found, assuming enabled" (neither is wired in Sony's overlay either),
`freq-table-hz property not specified`.

## On-device credentials

`user` / `1234` (numeric — the greeter keypad has no letters). ssh key
`build/pdx213_key` installed for `user`. Developer mode is on by default on USB.
