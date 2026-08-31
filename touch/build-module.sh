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
# Why 6.12.107 source for a 6.12.68 kernel: Debian sets KERNELRELEASE to plain
# "6.12-sm6350" with no point version, so both produce the *same* vermagic. Verified
# that 6.12.107's own modules have an identical 1152-byte struct module, and that
# CONFIG_MODVERSIONS and CONFIG_MODULE_SIG are both off -- so there are no symbol CRCs
# or signatures to match either. 6.12.68 has rotated out of the archive.

set -euo pipefail

VER=6.12.107
BASE=https://repo.mobian.org/pool/main/l/linux-6.12-sm6350
WORK=${WORK:-$HOME/xperia-modbuild}
IMAGE=docker.io/library/debian:trixie

mkdir -p "$WORK"
cd "$WORK"

echo "=== fetching sources into $WORK ==="
[ -f "linux-6.12-sm6350_${VER}.orig.tar.gz" ] || \
	curl -fL --retry 3 -O "$BASE/linux-6.12-sm6350_${VER}.orig.tar.gz"
# The .deb is only here for its /boot/config-6.12-sm6350, so the module is built
# against the same configuration the running kernel uses.
[ -f linux-image.deb ] || \
	curl -fL --retry 3 -o linux-image.deb \
		"$BASE/linux-image-6.12-sm6350_${VER}-1_arm64.deb"
ls -la linux-6.12-sm6350_${VER}.orig.tar.gz linux-image.deb

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
if [ ! -f src/.prepared ]; then
	rm -rf src && mkdir src
	tar xf linux-6.12-sm6350_*.orig.tar.gz -C src --strip-components=1
	dpkg-deb -x linux-image.deb debx
	cp debx/boot/config-6.12-sm6350 src/.config

	cd src
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
cat src/include/config/kernel.release
cat src/include/generated/utsrelease.h

# Build the driver as an *external* module: one source file, one modpost run, instead
# of compiling every module in the tree. KBUILD_MODPOST_WARN is required because
# there is no vmlinux here, so modpost cannot resolve symbols and would otherwise
# fail; the symbols it cannot see (i2c, input, regulator) are all built into the
# running kernel, which is verified separately from its config.
mkdir -p mod
cp src/drivers/input/touchscreen/s6sy761.c mod/
echo 'obj-m += s6sy761.o' > mod/Makefile
make -C src M=/work/mod KBUILD_MODPOST_WARN=1 modules

cp mod/s6sy761.ko /work/s6sy761.ko
INNER

echo "=== building in $IMAGE ==="
podman run --rm -v "$WORK:/work:z" -w /work "$IMAGE" bash /work/inner.sh

echo "=== result ==="
ls -la "$WORK/s6sy761.ko"
strings "$WORK/s6sy761.ko" | grep -E '^vermagic=' || true
