#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Safe management menu for the dedicated singbox-vps-safe instance.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

usage() {
  cat <<'TXT'
Usage: bash scripts/sb.sh [--menu|--status|--plan|--generate ...|--install CONFIG]
Only the dedicated singbox-vps-safe unit is managed.
No SSH, DNS, firewall, routing, WARP or unrelated service modifications.
TXT
}

status() {
  printf 'Dedicated service: '
  if ! command -v systemctl >/dev/null 2>&1; then
    echo 'systemd unavailable'
  elif systemctl is-active --quiet singbox-vps-safe.service; then
    echo 'active'
  else
    echo 'inactive or not installed'
  fi
  if [[ -f /etc/singbox-vps-safe/config.json ]]; then
    echo 'Dedicated configuration: present (contents hidden)'
  else
    echo 'Dedicated configuration: absent'
  fi
}

generate_interactive() {
  local protocol choice port output domain cert key host
  echo 'Choose a protocol: 1) VLESS-Reality 2) Hysteria2 3) TUIC v5 4) AnyTLS'
  read -r -p 'Choice [1-4]: ' choice || return
  case "$choice" in
    1) protocol='vless-reality' ;;
    2) protocol='hysteria2' ;;
    3) protocol='tuic' ;;
    4) protocol='anytls' ;;
    *) echo 'Invalid selection; no changes.' >&2; return 1 ;;
  esac
  read -r -p 'Listen port (1-65535): ' port || return
  read -r -p 'New local config JSON path (must not exist): ' output || return
  if [[ -z "$output" ]]; then echo 'Output path required' >&2; return 1; fi
  local -a args=(--protocol "$protocol" --port "$port" --output "$output")
  if [[ "$protocol" == 'vless-reality' ]]; then
    read -r -p 'Reality handshake DNS hostname (e.g. www.example.org): ' host || return
    args+=(--handshake-host "$host")
    echo 'Reality requires a reviewed sing-box binary available in PATH.'
  else
    read -r -p 'TLS certificate hostname: ' domain || return
    read -r -p 'Absolute TLS certificate path on target VPS: ' cert || return
    read -r -p 'Absolute TLS private key path on target VPS: ' key || return
    args+=(--server-name "$domain" --tls-cert "$cert" --tls-key "$key")
  fi
  python3 "$ROOT/scripts/generate_config.py" "${args[@]}"
}

menu() {
  local choice config
  while true; do
    printf '\n=== singbox-vps-safe (experimental) ===\n'
    printf '1) View dedicated service status\n'
    printf '2) Read-only installation plan\n'
    printf '3) Generate one protocol configuration\n'
    printf '4) Install reviewed configuration (root, disposable VPS only)\n'
    printf '0) Exit\n'
    read -r -p 'Select [0-4]: ' choice || return 0
    case "$choice" in
      1) status ;;
      2) bash "$ROOT/scripts/install.sh" --plan ;;
      3) generate_interactive ;;
      4)
        if [[ $EUID -ne 0 ]]; then
          echo 'Use sudo bash scripts/sb.sh --install /path/to/config.json' >&2
          continue
        fi
        read -r -p 'Reviewed config JSON path: ' config || return
        bash "$ROOT/scripts/install.sh" --install "$config"
        ;;
      0) return 0 ;;
      *) echo 'Invalid choice; no changes.' >&2 ;;
    esac
  done
}
case "${1:---menu}" in
  --menu) [[ $# -eq 1 ]] || { usage; exit 2; }; menu ;;
  --status) [[ $# -eq 1 ]] || { usage; exit 2; }; status ;;
  --plan) [[ $# -eq 1 ]] || { usage; exit 2; }; bash "$ROOT/scripts/install.sh" --plan ;;
  --generate) shift; python3 "$ROOT/scripts/generate_config.py" "$@" ;;
  --install)
    shift
    [[ $# -eq 1 ]] || { usage; exit 2; }
    bash "$ROOT/scripts/install.sh" --install "$1"
    ;;
  --help|-h) usage ;;
  *) usage; exit 2 ;;
esac
