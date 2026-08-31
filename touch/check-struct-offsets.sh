#!/bin/bash
# Compare offsetof(struct i2c_client, irq) between two kernel source trees.
#
# Run on a Linux host with podman:  ssh humboldt 'bash -s' < check-struct-offsets.sh
#
# Why: a module built against 6.12.107 headers loaded fine on the 6.12.68 kernel
# (same vermagic, same 1152-byte struct module, MODVERSIONS off so no symbol CRCs)
# but read client->irq as -50985 -- garbage -- while client->adapter and client->addr
# read correctly. Those two sit before the embedded struct device and irq sits after
# it, so the suspicion is that struct device changed size between the two point
# releases. This measures the offset in both trees instead of assuming.
#
# The 6.12.68 tree is also what the real module gets built from afterwards, so
# preparing it here is not wasted work.

set -euo pipefail

WORK=${WORK:-$HOME/xperia-modbuild}
IMAGE=docker.io/library/debian:trixie
cd "$WORK"

echo "=== fetching vanilla 6.12.68 (Mobian's 6.12.68-1 has rotated out of the archive) ==="
[ -f linux-6.12.68.tar.xz ] || \
	curl -fL --retry 3 -O https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.68.tar.xz
ls -la linux-6.12.68.tar.xz

cat > offsets.sh <<'INNER'
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
	build-essential bc bison flex libssl-dev libelf-dev xz-utils \
	gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu >/dev/null

cd /work
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-

# The running kernel's own config, so struct sizes match what is actually booted.
CONFIG=/work/config-mobian-working
[ -f "$CONFIG" ] || { echo "missing $CONFIG"; exit 1; }

if [ ! -f src68/.prepared ]; then
	rm -rf src68 && mkdir src68
	tar xf linux-6.12.68.tar.xz -C src68 --strip-components=1
	cp "$CONFIG" src68/.config
	cd src68
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

# Arrays whose *sizes* are the values we want, so nm -S reads them straight out --
# no decoding .rodata bytes by hand.
mkdir -p off
cat > off/probe.c <<'EOF'
#include <linux/i2c.h>
#include <linux/module.h>
char probe_IRQ_OFFSET[offsetof(struct i2c_client, irq)];
char probe_I2C_CLIENT_SIZE[sizeof(struct i2c_client)];
char probe_DEVICE_SIZE[sizeof(struct device)];
EOF
echo 'obj-m += probe.o' > off/Makefile

for tree in src68 src; do
	[ -d "$tree" ] || continue
	rm -f off/probe.o
	make -C "$tree" M=/work/off probe.o >/dev/null 2>&1 || true
	[ -f off/probe.o ] || { echo "$tree: probe build failed"; continue; }
	rel=$(cat "$tree/include/config/kernel.release" 2>/dev/null || echo unknown)
	sub=$(grep -m1 '^SUBLEVEL' "$tree/Makefile")
	echo "===== tree=$tree release=$rel ($sub) ====="
	# nm -S prints "<value> <size> <type> <name>"; size is the number we encoded.
	aarch64-linux-gnu-nm -S --size-sort off/probe.o | grep probe_ | \
		awk '{ printf "  %-24s = %d\n", $4, strtonum("0x" $2) }'
done
INNER

echo "=== running in $IMAGE ==="
podman run --rm -v "$WORK:/work:z" -w /work "$IMAGE" bash /work/offsets.sh
