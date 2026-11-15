#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Safe management menu for the dedicated singbox-vps-safe instance.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

usage() {
  cat <<'TXT'
Usage: bash scripts/sb.sh [--menu|--status|--plan|--generate ...|--install CONFIG]\n       sudo bash scripts/sb.sh [--backup|--snapshots|--restore ID --confirm]
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

extensions_menu() {
  local choice target token tool
  while true; do
    printf '\n=== Optional VPS extensions (NOT installed automatically) ===\n'
    printf '1) Show upstream sources, fixed commits and risks\n'
    printf '2) Read-only kernel/BBR/KejiLion status\n'
    printf '3) Download KejiLion pinned script for source review ONLY\n'
    printf '4) Download BBRv3 pinned script for source review ONLY\n'
    printf '0) Back\n'
    read -r -p 'Select [0-4]: ' choice || return 0
    case "$choice" in
      1) python3 "$ROOT/scripts/extensions.py" sources ;;
      2) python3 "$ROOT/scripts/extensions.py" status ;;
      3|4)
        if [[ "$choice" == 3 ]]; then tool=kejilion; else tool=bbr-v3; fi
        echo 'WARNING: third-party scripts can modify firewall, kernel, SSH, routing or networking.'
        echo 'This operation only downloads a pinned, verified, NON-EXECUTABLE file for inspection.'
        read -r -p 'Enter a NEW local file path for review copy: ' target || return 0
        if [[ -z "$target" ]]; then echo 'Cancelled'; continue; fi
        read -r -p 'Type DOWNLOAD to confirm: ' token || return 0
        if [[ "$token" != DOWNLOAD ]]; then echo 'Cancelled'; continue; fi
        python3 "$ROOT/scripts/extensions.py" download "$tool" "$target"
        ;;
      10) extensions_menu ;;
      0) return 0 ;;
      *) echo 'Invalid selection; no changes.' >&2 ;;
    esac
  done
}

menu() {
  local choice config reply
  echo 'Checking official stable sing-box version (read-only; 12s timeout)...'
  python3 "$ROOT/scripts/update.py" --check || echo 'Version check unavailable; nothing was changed.' >&2
  while true; do
    printf '\n=== singbox-vps-safe (experimental) ===\n'
    printf '1) View dedicated service status\n'
    printf '2) Read-only installation plan\n'
    printf '3) Generate one protocol configuration\n'
    printf '4) Install reviewed configuration (root, disposable VPS only)\n'
    printf '5) Create versioned backup (root)\n'
    printf '6) List versioned backups (root)\n'
    printf '7) Restore named backup (root + explicit confirmation)\n'
    printf '8) Check latest official sing-box version (read-only)\n'
    printf '9) One-click manual sing-box core update (root + confirmation)\n'
    printf '10) Optional external VPS tool references (separate; no auto-install)\n'
    printf '0) Exit\n'
    read -r -p 'Select [0-10]: ' choice || return 0
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
      5)
        if [[ $EUID -ne 0 ]]; then echo 'Root required for backups.' >&2; continue; fi
        python3 "$ROOT/scripts/state.py" --backup
        ;;
      6)
        if [[ $EUID -ne 0 ]]; then echo 'Root required to list private backups.' >&2; continue; fi
        python3 "$ROOT/scripts/state.py" --list
        ;;
      7)
        if [[ $EUID -ne 0 ]]; then echo 'Root required to restore backups.' >&2; continue; fi
        local snap confirm
        read -r -p 'Snapshot ID: ' snap || return 0
        read -r -p 'Type RESTORE to confirm: ' confirm || return 0
        if [[ "$confirm" != 'RESTORE' ]]; then echo 'Cancelled'; continue; fi
        python3 "$ROOT/scripts/state.py" --restore "$snap" --confirm
        ;;
      8)
        python3 "$ROOT/scripts/update.py" --check
        ;;
      9)
        if [[ $EUID -ne 0 ]]; then
          echo 'Please use sudo bash scripts/sb.sh --update-core --confirm' >&2
          continue
        fi
        echo 'Only the dedicated singbox-vps-safe core is updated.'
        echo 'This will create a backup, verify SHA256 and new config, restart the dedicated service.'
        read -r -p 'Type UPDATE to proceed (anything else cancels): ' reply || return 0
        if [[ "$reply" != 'UPDATE' ]]; then echo 'Cancelled, no changes.'; continue; fi
        python3 "$ROOT/scripts/update.py" --update --confirm
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
  --backup) [[ $# -eq 1 ]] || { usage; exit 2; }; python3 "$ROOT/scripts/state.py" --backup ;;
  --snapshots) [[ $# -eq 1 ]] || { usage; exit 2; }; python3 "$ROOT/scripts/state.py" --list ;;
  --restore)
    [[ $# -eq 3 && "$3" == '--confirm' ]] || { usage; exit 2; }
    python3 "$ROOT/scripts/state.py" --restore "$2" --confirm
    ;;
  --check-update)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    python3 "$ROOT/scripts/update.py" --check
    ;;
  --update-core)
    [[ $# -eq 2 && "$2" == '--confirm' ]] || { usage; exit 2; }
    python3 "$ROOT/scripts/update.py" --update --confirm
    ;;
  --extensions) [[ $# -eq 1 ]] || { usage; exit 2; }; extensions_menu ;;
  --extension-sources) [[ $# -eq 1 ]] || { usage; exit 2; }; python3 "$ROOT/scripts/extensions.py" sources ;;
  --extension-status) [[ $# -eq 1 ]] || { usage; exit 2; }; python3 "$ROOT/scripts/extensions.py" status ;;
  --help|-h) usage ;;
  *) usage; exit 2 ;;
esac
