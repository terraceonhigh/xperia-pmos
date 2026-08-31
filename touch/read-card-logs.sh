#!/bin/sh
# Dump the touchscreen diagnostics off a pmOS SD card, on a Mac.
#
# Under hybrid boot the phone has no network a Mac can use and (before Phosh) no
# GUI, so the payload writes its results to the card and we read them here. macOS
# cannot mount ext4, and raw disk access needs root, hence sudo + debugfs.
#
# Usage: sudo ./touch/read-card-logs.sh [/dev/diskNsM]
#        defaults to the pmOS root partition found by GPT type GUID

set -eu

DEBUGFS=/opt/homebrew/opt/e2fsprogs/sbin/debugfs
OUT="$(cd "$(dirname "$0")/.." && pwd)/build/card-logs"

[ -x "$DEBUGFS" ] || { echo "debugfs not found at $DEBUGFS"; exit 1; }

dev="${1:-}"
if [ -z "$dev" ]; then
	# B921B045-1DF0-41C3-AF44-4C6F280D3FAE is the Linux aarch64 root partition type
	dev=$(diskutil list \
		| awk '/B921B045-1DF0-41C3-AF44-4C6F280D3FAE/ {print "/dev/" $NF}' \
		| head -1)
	[ -n "$dev" ] || {
		echo "Could not find a Linux aarch64 root partition."
		echo "Pass it explicitly, e.g.: sudo $0 /dev/disk4s2"
		diskutil list
		exit 1
	}
fi

echo "reading $dev"
mkdir -p "$OUT"

dump() {
	# stderr is kept: debugfs exits 0 even for a missing file, so its message is
	# the only thing distinguishing "absent" from "empty" -- and a bad-superblock
	# error would otherwise show up as a silently empty section.
	printf '===== %s =====\n' "$1"
	"$DEBUGFS" -R "cat $1" "$dev" || true
	echo
}

{
	echo "device: $dev"
	echo "read at: $(date)"
	echo
	dump /var/log/touch-payload.status
	dump /var/log/enable-touch.log

	# If the log is missing, these say whether the initramfs stage ran at all.
	echo "===== installed payload files ====="
	for p in /usr/local/lib /usr/local/bin /etc/init.d /etc/runlevels/default; do
		printf -- '-- %s\n' "$p"
		"$DEBUGFS" -R "ls -l $p" "$dev" 2>/dev/null \
			| grep -iE "s6sy761|enable-touch" || echo "   (nothing matching)"
	done
	echo
	# Expect this to be empty: rc_logger is off by default in pmOS, so an absent
	# rc.log is normal and is not evidence of a failure.
	echo "===== OpenRC service log (usually absent -- rc_logger is off) ====="
	dump /var/log/rc.log
} > "$OUT/report.txt" 2>&1

# Make the report readable by the invoking user, not just root.
if [ -n "${SUDO_UID:-}" ]; then
	chown -R "$SUDO_UID:${SUDO_GID:-0}" "$OUT"
fi

echo "wrote $OUT/report.txt"
