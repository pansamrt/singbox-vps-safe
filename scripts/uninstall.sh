#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Remove only the dedicated files proven to belong to singbox-vps-safe.
set -Eeuo pipefail
umask 077
UNIT=/etc/systemd/system/singbox-vps-safe.service
CORE=/opt/singbox-vps-safe/sing-box
CONFIG=/etc/singbox-vps-safe/config.json
SERVICE=singbox-vps-safe.service
USER_NAME=singbox-safe
ACTION="${1:---plan}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
case "$ACTION" in
  --help|-h)
    echo "Usage: bash scripts/uninstall.sh --plan"
    echo "       sudo bash scripts/uninstall.sh --uninstall --confirm"
    exit 0 ;;
  --plan) [[ $# -eq 1 ]] || die "Unexpected arguments" ;;
  --uninstall) [[ $# -eq 2 && "$2" == --confirm ]] || die "Use --uninstall --confirm" ;;
  *) die "Unknown uninstall option" ;;
esac
if [[ -e "$UNIT" || -L "$UNIT" ]]; then
  [[ -f "$UNIT" && ! -L "$UNIT" ]] || die "Unexpected unit file type"
  grep -Fxq "User=singbox-safe" "$UNIT" || die "Unit owner does not match"
  grep -Fxq "ExecStart=/opt/singbox-vps-safe/sing-box run -c /etc/singbox-vps-safe/config.json" "$UNIT" ||
    die "Unit ExecStart does not match dedicated installation"
else
  die "Owned service unit not found: refusing to remove unknown files"
fi
for path in "$CORE" "$CONFIG"; do
  [[ -f "$path" && ! -L "$path" ]] || die "Expected dedicated file missing/symlinked"
done
for dir in /opt/singbox-vps-safe /etc/singbox-vps-safe; do
  [[ -d "$dir" && ! -L "$dir" ]] || die "Unexpected directory: $dir"
done
if [[ "$ACTION" == --plan ]]; then
  echo "Only the dedicated $SERVICE and these paths will be removed:"
  printf '  %s\n' "$UNIT" "$CORE" "$CONFIG"
  echo "Backups, generated subscriptions, TLS certificates and unrelated services are retained."
  echo "No firewall, SSH, DNS, WARP, Hysteria2 or other proxy will be modified."
  exit 0
fi
[[ $EUID -eq 0 ]] || die "Explicit root approval required"
echo "Stopping only $SERVICE; state backups and secrets remain untouched."
systemctl disable --now "$SERVICE"
rm -f -- "$UNIT" "$CORE" "$CONFIG"
systemctl daemon-reload
systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
rmdir /opt/singbox-vps-safe /etc/singbox-vps-safe 2>/dev/null || true
if getent passwd "$USER_NAME" >/dev/null; then
  HOME_FIELD="$(getent passwd "$USER_NAME" | cut -d : -f 6)"
  SHELL_FIELD="$(getent passwd "$USER_NAME" | cut -d : -f 7)"
  if [[ "$HOME_FIELD" == /nonexistent && "$SHELL_FIELD" == /usr/sbin/nologin ]]; then
    userdel "$USER_NAME" || die "Dedicated user could not be removed"
  else
    echo "Retaining changed service user (no longer matches original profile)." >&2
  fi
fi
echo "Scoped uninstall complete. Private backup/subscribe directories retained for owner review."
