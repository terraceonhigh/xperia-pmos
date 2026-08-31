#!/bin/sh
# Dump the logs needed to diagnose a failed Phosh session, off a pmOS SD card.
#
# Usage: sudo ./touch/read-session-logs.sh [/dev/diskNsM]
#
# Read-only: debugfs is opened without -w, and only `ls` and `cat` are issued. macOS
# cannot mount ext4 and raw disk reads need root, hence sudo.
#
# Under hybrid boot the phone has no network this Mac can use, so pulling the card is
# the only way to read anything off it.

set -eu

DEBUGFS=/opt/homebrew/opt/e2fsprogs/sbin/debugfs
OUT="$(cd "$(dirname "$0")/.." && pwd)/build/card-logs"

[ -x "$DEBUGFS" ] || { echo "debugfs not found at $DEBUGFS"; exit 1; }

dev="${1:-}"
if [ -z "$dev" ]; then
	dev=$(diskutil list \
		| awk '/B921B045-1DF0-41C3-AF44-4C6F280D3FAE/ {print "/dev/" $NF}' \
		| head -1)
	[ -n "$dev" ] || { echo "no Linux aarch64 root partition found"; diskutil list; exit 1; }
fi

echo "reading $dev"
mkdir -p "$OUT"

show() {
	printf '\n===== %s =====\n' "$1"
	"$DEBUGFS" -R "cat $1" "$dev" 2>&1 | grep -v '^debugfs '
}

list() {
	printf '\n----- ls %s -----\n' "$1"
	"$DEBUGFS" -R "ls -l $1" "$dev" 2>&1 | grep -v '^debugfs '
}

{
	echo "device: $dev"
	echo "read at: $(date)"

	# Where does anything even log? Listed first so unknown paths can be chased.
	list /var/log
	list /home/user
	list /home/user/.local/state
	list /home/user/.cache

	# Our own payload's reports.
	for f in /var/log/enable-touch.log /var/log/touch-payload.status \
		/var/log/authkey.status /var/log/pwreset.status; do
		show "$f"
	done

	# Session and compositor candidates. Missing ones simply report not found.
	for f in /var/log/messages /var/log/syslog /var/log/greetd.log \
		/home/user/.xsession-errors /home/user/.cache/phosh.log \
		/var/log/phosh-install.log; do
		show "$f"
	done
} > "$OUT/session-report.txt" 2>&1

if [ -n "${SUDO_UID:-}" ]; then
	chown -R "$SUDO_UID:${SUDO_GID:-0}" "$OUT"
fi

echo "wrote $OUT/session-report.txt ($(wc -l < "$OUT/session-report.txt") lines)"
