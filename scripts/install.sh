#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Explicit owner-only install. Archived fscarmen script is NEVER executed here.
set -Eeuo pipefail
umask 077
CORE_VERSION=1.14.4
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
    if [[ -n "$WORK" && -d "$WORK/managed-tls" ]]; then
      for staged in "$WORK"/managed-tls/*; do
        [[ -f "$staged" ]] || continue
        rm -f -- "$CONF_DIR/managed-tls/$(basename "$staged")"
      done
      rmdir "$CONF_DIR/managed-tls" >/dev/null 2>&1 || true
    fi
    if [[ "$ACTION" == --install-reality ]]; then
      rm -f -- "$STATE/client.json"
    fi
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
    echo "       sudo bash scripts/install.sh --install-reality PORT HANDSHAKE_DOMAIN [--network-mode dual|ipv4|ipv6] [--listen IPV6_ADDRESS]"
    echo "       sudo bash scripts/install.sh --install /absolute/config.json"
    exit 0 ;;
  --plan) [[ $# -eq 1 ]] || die "Unexpected extra argument" ;;
  --install) [[ $# -eq 2 && "$2" == /* ]] || die "Specify absolute config path"; SOURCE="$2" ;;
  --install-reality)
    [[ $# -ge 3 && "$2" =~ ^[0-9]+$ ]] || die "Use --install-reality PORT HANDSHAKE_DOMAIN [--network-mode MODE] [--listen ADDRESS]"
    REALITY_PORT="$2"; REALITY_DOMAIN="$3"
    shift 3
    NET_ARGS=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --network-mode|--listen)
          [[ $# -ge 2 && -n "$2" ]] || die "Missing value after $1"
          NET_ARGS+=("$1" "$2")
          shift 2 ;;
        *) die "Unknown network option: $1" ;;
      esac
    done
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
say "Pinned core v$CORE_VERSION ($ARCH). Separate service: $SERVICE."
say "Reality network default: dual IPv4/IPv6 (::), with both-family TCP port preflight."
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
[[ ! -e "$CONF_DIR" && ! -L "$CONF_DIR" ]] ||
  die "Dedicated config directory already exists; refusing overwrite"
[[ ! -e "$CONF_DIR/managed-tls" && ! -L "$CONF_DIR/managed-tls" ]] ||
  die "Refusing pre-existing managed TLS directory"
if getent passwd "$USER_NAME" >/dev/null; then die "Dedicated user exists, refusing reuse"; fi

WORK="$(mktemp -d /var/tmp/singbox-vps-safe.XXXXXXXX)"
ASSET="sing-box-$CORE_VERSION-linux-$ARCH"
URL="https://github.com/SagerNet/sing-box/releases/download/v$CORE_VERSION/$ASSET.tar.gz"
if [[ -n "${SAFE_OFFLINE_ARCHIVE:-}" ]]; then
  [[ "$SAFE_OFFLINE_ARCHIVE" == /* && -f "$SAFE_OFFLINE_ARCHIVE" && ! -L "$SAFE_OFFLINE_ARCHIVE" ]] ||
    die "Offline archive must be an absolute regular file, not a symlink"
  cp -- "$SAFE_OFFLINE_ARCHIVE" "$WORK/core.tar.gz"
else
  curl --proto '=https' --tlsv1.2 -fL --retry 2 --connect-timeout 15 --max-time 120 \
    -o "$WORK/core.tar.gz" "$URL" || die "Official download failed"
fi
printf '%s  %s\n' "$SHA" "$WORK/core.tar.gz" | sha256sum -c --status ||
  die "Core SHA256 mismatch"
tar -xOzf "$WORK/core.tar.gz" "$ASSET/sing-box" > "$WORK/sing-box" ||
  die "Expected binary missing"
chmod 755 "$WORK/sing-box"
"$WORK/sing-box" version | grep -Fx "sing-box version $CORE_VERSION" >/dev/null ||
  die "Core version mismatch"
if [[ "$ACTION" == --install-reality ]]; then
  python3 "$SOURCE_DIR/generate_config.py" \
    --protocol vless-reality --port "$REALITY_PORT" \
    --handshake-host "$REALITY_DOMAIN" "${NET_ARGS[@]}" \
    --reality-binary "$WORK/sing-box" --output "$WORK/generated.json" ||
    die "Local Reality config generation failed"
  SOURCE="$WORK/generated.json"
fi
python3 "$SOURCE_DIR/ports.py" --config "$SOURCE" || die "TCP/UDP port preflight refused"
"$WORK/sing-box" check -c "$SOURCE" || die "Config check failed before any system changes"
python3 "$SOURCE_DIR/prepare_tls.py" --config "$SOURCE" --workspace "$WORK" ||
  die "Could not securely stage explicit TLS certificates"
say "Preflight passed. Creating only dedicated resources."
MUTATED=1
useradd --system --user-group --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin "$USER_NAME" || die "Dedicated user creation failed"
CREATED_USER=1
install -d -o root -g root -m 755 "$TARGET"
install -d -o root -g "$USER_NAME" -m 750 "$CONF_DIR"
install -d -o root -g root -m 700 "$STATE"
install -m 755 "$WORK/sing-box" "$TARGET/sing-box"
if [[ -d "$WORK/managed-tls" ]]; then
  install -d -o root -g "$USER_NAME" -m 750 "$CONF_DIR/managed-tls"
  for staged in "$WORK"/managed-tls/*; do
    [[ -f "$staged" ]] || continue
    install -m 640 -o root -g "$USER_NAME" "$staged" "$CONF_DIR/managed-tls/$(basename "$staged")"
  done
fi
install -m 640 -o root -g "$USER_NAME" "$WORK/install-config.json" "$CONFIG"
"$TARGET/sing-box" check -c "$CONFIG" || die "Installed TLS/config check failed"
if [[ "$ACTION" == --install-reality ]]; then
  install -m 600 -o root -g root "$WORK/generated.json.client.json" "$STATE/client.json"
fi
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=singbox-vps-safe isolated sing-box
After=network.target
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
