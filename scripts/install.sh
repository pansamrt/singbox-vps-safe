#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Explicit owner-only install. Archived fscarmen script is NEVER executed here.
set -Eeuo pipefail
umask 077
VERSION=1.14.4
SHA_AMD64=699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968
SHA_ARM64=436441f76ee2f7fe21b5b2d426d3cb261a480455a704865789ad533e4273ec26
TARGET=/opt/singbox-vps-safe
CONF_DIR=/etc/singbox-vps-safe
CONFIG=$CONF_DIR/config.json
UNIT=/etc/systemd/system/singbox-vps-safe.service
STATE=/var/lib/singbox-vps-safe
SERVICE=singbox-vps-safe.service
USER_NAME=singbox-safe
SOURCE_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
ACTION="${1:---plan}"
WORK=''
MUTATED=0
CREATED_USER=0
say() { printf '[singbox-vps-safe] %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ $rc -ne 0 && $MUTATED -eq 1 ]]; then
    say "Install failed, recovering dedicated files only."
    systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
    rm -f -- "$UNIT" "$CONFIG" "$TARGET/sing-box"
    systemctl daemon-reload >/dev/null 2>&1 || true
    rmdir "$TARGET" "$CONF_DIR" "$STATE" >/dev/null 2>&1 || true
    if [[ $CREATED_USER -eq 1 ]]; then userdel "$USER_NAME" >/dev/null 2>&1 || true; fi
  fi
  if [[ -n "$WORK" ]]; then rm -rf -- "$WORK"; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

case "$ACTION" in
  --help|-h)
    echo "Usage: bash scripts/install.sh --plan"
    echo "       sudo bash scripts/install.sh --install-reality PORT HANDSHAKE_DOMAIN"
    echo "       sudo bash scripts/install.sh --install /absolute/config.json"
    exit 0 ;;
  --plan) [[ $# -eq 1 ]] || die "Unexpected extra argument" ;;
  --install) [[ $# -eq 2 && "$2" == /* ]] || die "Specify absolute config path"; SOURCE="$2" ;;
  --install-reality)
    [[ $# -eq 3 && "$2" =~ ^[0-9]+$ ]] || die "Use --install-reality PORT HANDSHAKE_DOMAIN"
    REALITY_PORT="$2"; REALITY_DOMAIN="$3"
    ;;
  *) die "Invalid action. Use --plan, --install or --install-reality." ;;
esac
[[ "$(uname -s)" == Linux ]] || die "Linux only"
# shellcheck disable=SC1091
. /etc/os-release
case "$ID:$VERSION_ID" in
  debian:12*|debian:13*|ubuntu:24*|ubuntu:26*) ;;
  *) die "Requires Debian 12/13 or Ubuntu 24/26" ;;
esac
case "$(uname -m)" in
  x86_64) ARCH=amd64; SHA="$SHA_AMD64" ;;
  aarch64) ARCH=arm64; SHA="$SHA_ARM64" ;;
  *) die "Unsupported CPU architecture" ;;
esac
for cmd in curl sha256sum tar python3 systemctl useradd userdel getent install ss; do
  command -v "$cmd" >/dev/null || die "Missing prerequisite: $cmd"
done
say "Pinned core v$VERSION ($ARCH). Separate service: $SERVICE."
say "SSH, DNS, firewall, WARP, BBR, original sing-box and Hysteria2 stay untouched."
if [[ "$ACTION" == --plan ]]; then
  say "PLAN ONLY, no downloads or modifications."
  ss -lntup 2>/dev/null || true
  exit 0
fi
[[ $EUID -eq 0 ]] || die "Root required for explicit install"
if [[ "$ACTION" == --install ]]; then
  [[ -f "$SOURCE" && -r "$SOURCE" && ! -L "$SOURCE" ]] || die "Refusing missing or symlinked config"
fi
[[ ! -e "$UNIT" && ! -L "$UNIT" && ! -e "$TARGET" && ! -L "$TARGET" && ! -e "$CONFIG" && ! -L "$CONFIG" ]] ||
  die "Existing dedicated installation. Refusing overwrite."
[[ ! -e "$STATE" && ! -L "$STATE" ]] || die "State directory already exists."
[[ ! -L "$CONF_DIR" ]] || die "Config directory symlink is forbidden"
if [[ -e "$CONF_DIR" ]]; then
  [[ -d "$CONF_DIR" && "$(stat -c %u "$CONF_DIR")" == 0 ]] ||
    die "Pre-existing TLS config directory must be root-owned"
fi
if getent passwd "$USER_NAME" >/dev/null; then die "Dedicated user exists, refusing reuse"; fi

WORK="$(mktemp -d /tmp/singbox-vps-safe.XXXXXXXX)"
ASSET="sing-box-$VERSION-linux-$ARCH"
URL="https://github.com/SagerNet/sing-box/releases/download/v$VERSION/$ASSET.tar.gz"
curl --proto '=https' --tlsv1.2 -fL --retry 2 --connect-timeout 15 --max-time 120 \
  -o "$WORK/core.tar.gz" "$URL" || die "Official download failed"
printf '%s  %s\n' "$SHA" "$WORK/core.tar.gz" | sha256sum -c --status ||
  die "Core SHA256 mismatch"
tar -xOzf "$WORK/core.tar.gz" "$ASSET/sing-box" > "$WORK/sing-box" ||
  die "Expected binary missing"
chmod 755 "$WORK/sing-box"
"$WORK/sing-box" version | grep -Fx "sing-box version $VERSION" >/dev/null ||
  die "Core version mismatch"
if [[ "$ACTION" == --install-reality ]]; then
  python3 "$SOURCE_DIR/generate_config.py" \
    --protocol vless-reality --port "$REALITY_PORT" \
    --handshake-host "$REALITY_DOMAIN" \
    --reality-binary "$WORK/sing-box" --output "$WORK/generated.json" ||
    die "Local Reality config generation failed"
  SOURCE="$WORK/generated.json"
fi
python3 "$SOURCE_DIR/ports.py" --config "$SOURCE" || die "TCP/UDP port preflight refused"
"$WORK/sing-box" check -c "$SOURCE" || die "Config check failed before any system changes"
say "Preflight passed. Creating only dedicated resources."
MUTATED=1
useradd --system --user-group --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin "$USER_NAME" || die "Dedicated user creation failed"
CREATED_USER=1
install -d -o root -g root -m 755 "$TARGET"
install -d -o root -g "$USER_NAME" -m 750 "$CONF_DIR"
install -d -o root -g root -m 700 "$STATE"
install -m 755 "$WORK/sing-box" "$TARGET/sing-box"
install -m 640 -o root -g "$USER_NAME" "$SOURCE" "$CONFIG"
if [[ "$ACTION" == --install-reality ]]; then
  install -m 600 -o root -g root "$WORK/generated.json.client.json" "$STATE/client.json"
fi
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=singbox-vps-safe isolated sing-box
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=singbox-safe
Group=singbox-safe
ExecStart=/opt/singbox-vps-safe/sing-box run -c /etc/singbox-vps-safe/config.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
UMask=0077
[Install]
WantedBy=multi-user.target
UNITFILE
chmod 644 "$UNIT"
systemctl daemon-reload
systemctl start "$SERVICE" || die "Dedicated service start failure"
sleep 2
systemctl is-active --quiet "$SERVICE" || die "Service did not stay active"
systemctl enable "$SERVICE" || die "Could not enable dedicated service"
MUTATED=0
say "Installation successful. Manage via sudo bash scripts/sb.sh --menu"
