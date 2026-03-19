# postmarketOS for Sony Xperia 10 III (pdx213)

postmarketOS device packages for the Sony Xperia 10 III (codename: pdx213, SoC: Qualcomm SM6350 / Snapdragon 690 5G).

## Status

**First successful boot: 2026-03-18 21:20 PST**

postmarketOS edge boots to userspace with kernel 6.19.0 (`linux-postmarketos-qcom-sm6350`). SSH accessible over USB networking. Rootfs on SD card (ext4). Display is currently dark (GPU driver takes over from simpledrm but fails to initialize — see Pitfalls below).

### What works (pmOS kernel 6.19.0)

| Feature | Status | Notes |
|---------|--------|-------|
| Kernel boot | **Yes** | 6.19.0, multi-core, boots in ~1s |
| Rootfs mount | **Yes** | SD card (mmcblk0), ext4, pmOS_root label |
| USB networking | **Yes** | CDC ECM, 172.16.42.1, SSH works |
| SSH | **Yes** | user/password over USB network |
| Display (fbcon) | Partial | simpledrm works during early boot, then msm_dpu takes over and kills it |
| GPU | **Broken** | msm_dpu loads, finds a619_gmu.bin + a630_sqe.fw, but a615_zap init fails |
| SPMI/PMIC | **Yes** | PM6350 arbiter v5 |
| RAM | **Yes** | 5.3GB detected, zram swap configured |
| Phosh | Installed | 791 packages, but no display output yet |
| Touch HW | Probing | himax_hx83112 driver initializes |
| WiFi | Not tested | Needs firmware |
| Modem | Not tested | Needs firmware |
| Bluetooth | Not tested | Needs firmware |
| Battery | Not tested | Needs kernel module (CONFIG_CHARGER_QCOM_SMB2) |

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

### 4. boot-deploy ignores deviceinfo (CRITICAL)

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

### 6. Exported rootfs is a sparse image

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

### 10. Phone internet requires NAT on the host

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

## Boot format (critical)

Sony ABL bootloader on pdx213 requires:
- **Header version 0** (NOT v2)
- **Base address 0x10000000** (NOT 0x0)
- **gzip-compressed kernel + appended DTB**
- Page size 4096

Using header v2 or base 0x0 causes silent boot failure.

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
