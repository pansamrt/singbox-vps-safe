#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# singbox-vps-safe Phase 1: explicit opt-in, non-destructive installer.
set -Eeuo pipefail
umask 077

readonly VERSION="1.14.4"
readonly TARGET="/opt/singbox-vps-safe"
readonly SERVICE="/etc/systemd/system/singbox-vps-safe.service"
readonly CONFIG="/etc/singbox-vps-safe/config.json"
readonly SHA_AMD64="699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968"
readonly SHA_ARM64="436441f76ee2f7fe21b5b2d426d3cb261a480455a704865789ad533e4273ec26"
ACTION="${1:---plan}"
CONFIG_SOURCE=""
WORK=""
BACKUP=""
CHANGED=0

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[singbox-vps-safe] %s\n' "$*"; }
cleanup() { [[ -z "$WORK" ]] || rm -rf -- "$WORK"; }
rollback() {
  local status=$?
  trap - ERR EXIT
  if [[ $status -ne 0 && "$CHANGED" == 1 ]]; then
    log "Install failed; restoring owned files and service state."
    systemctl stop singbox-vps-safe.service >/dev/null 2>&1 || true
    if [[ -n "$BACKUP" && -f "$BACKUP/service" ]]; then
      cp -p -- "$BACKUP/service" "$SERVICE"
    else
      rm -f -- "$SERVICE"
    fi
    if [[ -n "$BACKUP" && -f "$BACKUP/config" ]]; then
      install -m 600 "$BACKUP/config" "$CONFIG"
    else
      rm -f -- "$CONFIG"
    fi
    if [[ -n "$BACKUP" && -f "$BACKUP/binary" ]]; then
      install -m 755 "$BACKUP/binary" "$TARGET/sing-box"
    else
      rm -f -- "$TARGET/sing-box"
    fi
    systemctl daemon-reload || true
    if [[ -n "$BACKUP" && -f "$BACKUP/was-active" ]]; then
      systemctl start singbox-vps-safe.service || true
    fi
  fi
  cleanup
  exit "$status"
}
trap rollback EXIT

usage() {
  cat <<'HELP'
Usage:
  bash scripts/install.sh --plan
  sudo bash scripts/install.sh --install /path/to/config.json

--plan never changes the system.
--install requires a user-supplied, previously reviewed sing-box JSON config.
The installer owns ONLY its own dedicated systemd unit, binary, and config.
No SSH, firewall, WARP, DNS, existing sing-box service, or cron modifications.
HELP
}
case "$ACTION" in
  --help|-h) usage; exit 0 ;;
  --plan) ;;
  --install)
    [[ $# == 2 ]] || die "Expected --install /path/to/config.json"
    CONFIG_SOURCE="$2"
    ;;
  *) usage; die "Unknown operation: $ACTION" ;;
esac

[[ "$(uname -s)" == "Linux" ]] || die "Linux required"
[[ -r /etc/os-release ]] || die "Cannot identify distro"
. /etc/os-release
case "${ID:-}" in debian|ubuntu) ;; *) die "Only Debian/Ubuntu currently supported" ;; esac
case "$(uname -m)" in
  x86_64) ARCH="amd64"; EXPECTED_SHA="$SHA_AMD64" ;;
  aarch64) ARCH="arm64"; EXPECTED_SHA="$SHA_ARM64" ;;
  *) die "Unsupported CPU architecture" ;;
esac
for cmd in curl sha256sum tar systemctl ss; do
  command -v "$cmd" >/dev/null || die "Missing required command: $cmd (install manually)"
done

log "Platform: $ID / $ARCH"
log "Version pinned: sing-box v$VERSION"
log "Dedicated service: singbox-vps-safe.service"
log "Existing sing-box units, SSH, firewall, DNS and routing remain untouched."
log "Active listening ports (inspection only):"
ss -lntup 2>/dev/null || true

if [[ "$ACTION" == "--plan" ]]; then
  log "PLAN ONLY: no downloads, configuration edits or service changes."
  exit 0
fi

[[ $EUID == 0 ]] || die "Run --install as root"
[[ -f "$CONFIG_SOURCE" && -r "$CONFIG_SOURCE" ]] || die "Config missing/unreadable"
[[ ! -L "$CONFIG_SOURCE" ]] || die "Config symlink not accepted"
[[ ! -L "$TARGET" && ! -L "$CONFIG" && ! -L "$SERVICE" ]] || die "Refusing symlink destination"
# Phase 1 does not alter any existing instance, even an earlier install.
[[ ! -e "$TARGET/sing-box" && ! -e "$SERVICE" && ! -e "$CONFIG" ]] || die "Existing safe installation detected; upgrade/rollback not yet enabled"

WORK="$(mktemp -d)"
BACKUP="$(mktemp -d /var/tmp/singbox-vps-safe-backup.XXXXXXXX)"
log "Rollback staging directory: $BACKUP"
URL="https://github.com/SagerNet/sing-box/releases/download/v${VERSION}/sing-box-${VERSION}-linux-${ARCH}.tar.gz"
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error --retry 2 --output "$WORK/sing-box.tar.gz" "$URL"
printf '%s  %s\n' "$EXPECTED_SHA" "$WORK/sing-box.tar.gz" | sha256sum --check --status || die "SHA256 mismatch"
tar -tzf "$WORK/sing-box.tar.gz" >/dev/null || die "Corrupt archive"
# Extract to temporary workspace only; read the expected member by exact path.
tar -xOzf "$WORK/sing-box.tar.gz" "sing-box-${VERSION}-linux-${ARCH}/sing-box" > "$WORK/sing-box" || die "Expected binary missing"
chmod 755 "$WORK/sing-box"
"$WORK/sing-box" version
"$WORK/sing-box" check -c "$CONFIG_SOURCE" || die "Supplied configuration failed sing-box check"
log "Config verified; checking requested listening ports against current services."
if command -v python3 >/dev/null 2>&1; then
  python3 - "$CONFIG_SOURCE" <<'PY'
import json, socket, sys
with open(sys.argv[1], encoding="utf8") as f:
    conf = json.load(f)
for inbound in conf.get("inbounds", []):
    port = inbound.get("listen_port")
    if port is None:
        continue
    if not isinstance(port, int) or not 1 <= port <= 65535:
        sys.exit("Invalid listen_port")
    proto = socket.SOCK_DGRAM if inbound.get("type") in ("hysteria2", "tuic") else socket.SOCK_STREAM
    sock = socket.socket(socket.AF_INET, proto)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        sys.exit(f"Port {port} appears unavailable: {exc}")
    finally:
        sock.close()
PY
else
  die "python3 is required for port preflight"
fi
install -d -m 700 "$TARGET" /etc/singbox-vps-safe
CHANGED=1
install -m 755 "$WORK/sing-box" "$TARGET/sing-box"
install -m 600 "$CONFIG_SOURCE" "$CONFIG"
cat > "$SERVICE" <<'UNIT'
[Unit]
Description=singbox-vps-safe dedicated sing-box service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/opt/singbox-vps-safe/sing-box run -c /etc/singbox-vps-safe/config.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadOnlyPaths=/etc/singbox-vps-safe
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "$SERVICE"
systemctl daemon-reload
systemctl start singbox-vps-safe.service || die "Failed to start dedicated service"
systemctl is-active --quiet singbox-vps-safe.service || die "Service did not become active"
systemctl enable singbox-vps-safe.service || die "Could not enable service"
CHANGED=0
log "Installation complete. Configuration: $CONFIG"
log "No firewall/DNS/SSH changes were made."
