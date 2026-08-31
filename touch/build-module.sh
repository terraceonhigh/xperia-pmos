#!/bin/bash
# Cross-compile s6sy761.ko for the pdx213 hybrid kernel, in a container.
#
# Run this on an x86_64 Linux host with podman:  ssh humboldt 'bash -s' < build-module.sh
# It leaves the module at $WORK/s6sy761.ko on that host.
#
# Why build at all: no Mobian config enables CONFIG_TOUCHSCREEN_S6SY761, so no distro
# ships this module, and the one in the xperia-mobian release was built against a
# different tree (vermagic 6.12.0-sm6350, struct module 1088) and cannot load.
#
# The source MUST match the running kernel's point release, 6.12.68. An earlier
# version of this script used 6.12.107 (the oldest still in Mobian's archive) on the
# reasoning that vermagic and struct module size both matched, so the module would
# load -- and it did load. But loading is not the same as being ABI-compatible:
# measured with touch/check-struct-offsets.sh, sizeof(struct device) is 744 in
# 6.12.68 and 768 in 6.12.107, which shifts offsetof(struct i2c_client, irq) from 780
# to 804. The module read client->irq 24 bytes past the real field and got garbage
# (-50985), so devm_request_threaded_irq failed with -EINVAL. Fields before the
# embedded struct device (adapter, addr) read fine, which is why I2C worked and only
# the interrupt broke. CONFIG_MODVERSIONS is off, so nothing catches this at load
# time. Mobian's 6.12.68-1 has rotated out of the archive; vanilla 6.12.68 from
# kernel.org plus the running kernel's own config gives the right layout.

set -euo pipefail

VER=6.12.68
BASE=https://cdn.kernel.org/pub/linux/kernel/v6.x
SRC=src-$VER
WORK=${WORK:-$HOME/xperia-modbuild}
IMAGE=docker.io/library/debian:trixie

mkdir -p "$WORK"
cd "$WORK"

echo "=== fetching sources into $WORK ==="
[ -f "linux-${VER}.tar.xz" ] || \
	curl -fL --retry 3 -O "$BASE/linux-${VER}.tar.xz"
# The running kernel's own config, so struct layouts match what is actually booted.
[ -f config-mobian-working ] || {
	echo "config-mobian-working missing -- copy the running kernel's config here"
	exit 1
}
ls -la "linux-${VER}.tar.xz" config-mobian-working

# The inner script is written to a file rather than inlined, to keep one level of
# shell quoting instead of three.
cat > inner.sh <<'INNER'
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
	build-essential bc bison flex libssl-dev libelf-dev xz-utils \
	gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu >/dev/null

cd /work
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-

# Resumable: preparing the tree takes minutes, so only do it once.
if [ ! -f "$SRC/.prepared" ]; then
	rm -rf "$SRC" && mkdir "$SRC"
	tar xf "linux-${VER}.tar.xz" -C "$SRC" --strip-components=1
	cp config-mobian-working "$SRC/.config"

	cd "$SRC"
	# Blank SUBLEVEL so KERNELVERSION is "6.12" rather than "6.12.107"; with
	# LOCALVERSION="-sm6350" that yields the release the running kernel wants.
	sed -i 's/^SUBLEVEL = .*/SUBLEVEL =/' Makefile
	./scripts/config --file .config \
		--set-str LOCALVERSION "-sm6350" \
		--disable LOCALVERSION_AUTO \
		--module TOUCHSCREEN_S6SY761
	make -s olddefconfig
	make -j"$(nproc)" modules_prepare
	touch .prepared
	cd /work
fi

echo "--- release string check (must be 6.12-sm6350) ---"
cat "$SRC/include/config/kernel.release"
cat "$SRC/include/generated/utsrelease.h"

# Build the driver as an *external* module: one source file, one modpost run, instead
# of compiling every module in the tree. KBUILD_MODPOST_WARN is required because
# there is no vmlinux here, so modpost cannot resolve symbols and would otherwise
# fail; the symbols it cannot see (i2c, input, regulator) are all built into the
# running kernel, which is verified separately from its config.
mkdir -p mod
cp "$SRC/drivers/input/touchscreen/s6sy761.c" mod/
echo 'obj-m += s6sy761.o' > mod/Makefile

# DIAG=1 adds one dev_info before the IRQ request. Probe fails there with -EINVAL on
# a kernel and DT node identical to ones where it worked, and the two candidate
# causes need different fixes: client->irq == 0 means the interrupt never mapped,
# while a valid number means the trigger type was rejected. Nothing readable from
# outside the kernel distinguishes them, so ask the driver.
if [ "${DIAG:-0}" = "1" ]; then
	sed -i 's|^\terr = devm_request_threaded_irq(&client->dev, client->irq, NULL,|\tdev_info(\&client->dev, "DIAG irq=%d max_x=%u max_y=%u tx=%u\\n", client->irq, max_x, max_y, sdata->tx_channel);\n\terr = devm_request_threaded_irq(\&client->dev, client->irq, NULL,|' mod/s6sy761.c
	grep -q "DIAG irq=" mod/s6sy761.c || { echo "DIAG patch did not apply"; exit 1; }
	echo "DIAG instrumentation applied"
fi
make -C "$SRC" M=/work/mod KBUILD_MODPOST_WARN=1 modules

# Guard the bug that cost two hardware cycles: a module built against the wrong point
# release loads happily (vermagic and struct module size both match) but reads struct
# fields at the wrong offsets. 780 is offsetof(struct i2c_client, irq) for the kernel
# this was validated against; 6.12.107 gives 804. If this trips, the tree is wrong --
# do not ship the module, whatever the other checks say.
EXPECT_IRQ_OFFSET=780
mkdir -p off
cat > off/probe.c <<'EOF'
#include <linux/i2c.h>
#include <linux/module.h>
char probe_IRQ_OFFSET[offsetof(struct i2c_client, irq)];
EOF
echo 'obj-m += probe.o' > off/Makefile
rm -f off/probe.o
make -C "$SRC" M=/work/off probe.o >/dev/null 2>&1 || true
if [ -f off/probe.o ]; then
	got=$(readelf -sW off/probe.o | awk '/probe_IRQ_OFFSET/ { print $3 }')
	echo "offsetof(struct i2c_client, irq) = $got (expected $EXPECT_IRQ_OFFSET)"
	if [ "$got" != "$EXPECT_IRQ_OFFSET" ]; then
		echo "ABI MISMATCH: built against the wrong kernel point release"
		exit 1
	fi
else
	echo "WARNING: could not verify struct offsets"
fi

cp mod/s6sy761.ko /work/s6sy761.ko
INNER

echo "=== building in $IMAGE ==="
# DIAG must be passed in explicitly: podman does not inherit the host environment,
# and the instrumentation switch is read inside the container.
podman run --rm -e "DIAG=${DIAG:-0}" -e "VER=$VER" -e "SRC=$SRC" -v "$WORK:/work:z" -w /work "$IMAGE" \
	bash /work/inner.sh

echo "=== result ==="
ls -la "$WORK/s6sy761.ko"
strings "$WORK/s6sy761.ko" | grep -E '^vermagic=' || true
