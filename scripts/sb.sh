#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Owner-only menu and subcommands. Never run archived upstream scripts.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"

usage() {
  cat <<'HELP'
Smart | singbox-vps-safe — owner-controlled management:
  bash scripts/sb.sh --menu
  bash scripts/sb.sh --plan
  sudo bash scripts/sb.sh --doctor
  sudo bash scripts/sb.sh --install-reality PORT HANDSHAKE_DOMAIN [--network-mode dual|ipv4|ipv6] [--listen IPV6_ADDRESS]
  sudo bash scripts/sb.sh --install /absolute/config.json
  bash scripts/sb.sh --generate --protocol vless-reality --port 8443 --handshake-host www.example.org --output ./server.json
  bash scripts/sb.sh --check-update
  sudo bash scripts/sb.sh --update-core --confirm
  sudo bash scripts/sb.sh --backup
  sudo bash scripts/sb.sh --snapshots
  sudo bash scripts/sb.sh --restore SNAPSHOT_ID --confirm
  sudo bash scripts/sb.sh --export-clients PUBLIC_SERVER_ADDRESS [CLIENT_METADATA_JSON]
  sudo bash scripts/sb.sh --export-clients --server-ipv4 IPv4 --server-ipv6 IPv6
  sudo bash scripts/sb.sh --subscription-init [--public-base-url https://your-domain.example]
  sudo bash scripts/sb.sh --subscription-publish --source-dir /path/to/local/subscriptions
  sudo bash scripts/sb.sh --subscription-urls
  sudo bash scripts/sb.sh --subscription-rotate
  sudo bash scripts/sb.sh --subscription-serve
  bash scripts/sb.sh --extension-sources
  bash scripts/sb.sh --extension-status
  sudo bash scripts/sb.sh --warp-status
  sudo bash scripts/sb.sh --warp-plan --profile /root/private-warp.conf --inbound vless-reality-in --family ipv4
  sudo bash scripts/sb.sh --warp-enable --profile /root/private-warp.conf --inbound vless-reality-in --family ipv6 --confirm
  sudo bash scripts/sb.sh --warp-disable --confirm
  sudo bash scripts/sb.sh --uninstall --confirm

Notes: no unattended installs, kernel edits, firewall/DNS/SSH changes,
remote subscription conversion or third-party root shell execution.
HELP
}
status() {
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet singbox-vps-safe.service; then
      echo "Dedicated sing-box service: active"
    else
      echo "Dedicated sing-box service: inactive / not installed"
    fi
  else
    echo "systemd not available"
  fi
  if [[ -x /opt/singbox-vps-safe/sing-box ]]; then
    /opt/singbox-vps-safe/sing-box version | head -n 1
  fi
}
require_root() {
  [[ $EUID -eq 0 ]] || { echo 'Root required for this explicit operation.' >&2; return 1; }
}
extensions() {
  echo "1) Show external projects and pinned source commits"
  echo "2) Read-only local kernel / TCP BBR status"
  echo "3) Return to main menu"
  local answer
  read -r -p 'Selection: ' answer || return 0
  case "$answer" in
    1) python3 "$ROOT/scripts/extensions.py" sources ;;
    2) python3 "$ROOT/scripts/extensions.py" status ;;
    3) ;;
    *) echo 'No action' ;;
  esac
}
warp_menu() {
  echo 'Smart WARP is independent, OFF by default; no system routes change.'
  echo '1) Read-only Smart WARP status'
  echo '2) Verify private profile + config (no changes)'
  echo '3) Enable selected proxy egress via WARP (snapshot + rollback)'
  echo '4) Disable Smart WARP routing (snapshot + rollback)'
  echo '0) Back'
  local selection profile inbound family confirmation
  read -r -p 'Select: ' selection || return 0
  case "$selection" in
    1) require_root && python3 "$ROOT/scripts/warp.py" status ;;
    2|3)
      require_root || return 1
      read -r -p 'Absolute private WireGuard profile (0600): ' profile || return 0
      read -r -p 'Exact existing Smart proxy inbound tag: ' inbound || return 0
      read -r -p 'Destination family [ipv4/ipv6/dual]: ' family || return 0
      case "$family" in ipv4|ipv6|dual) ;; *) echo 'Unsupported family'; return 1 ;; esac
      if [[ "$selection" == 2 ]]; then
        python3 "$ROOT/scripts/warp.py" plan --profile "$profile" --inbound "$inbound" --family "$family"
      else
        echo 'Only this inbound and selected destination IP family will use WARP.'
        echo 'No VPS public IP allocation, default route, SSH or firewall changes.'
        read -r -p 'Type ENABLE-WARP to proceed: ' confirmation || return 0
        [[ "$confirmation" == ENABLE-WARP ]] &&
          python3 "$ROOT/scripts/warp.py" enable --profile "$profile" --inbound "$inbound" --family "$family" --confirm
      fi ;;
    4)
      require_root || return 1
      read -r -p 'Type DISABLE-WARP to restore direct routing: ' confirmation || return 0
      [[ "$confirmation" == DISABLE-WARP ]] &&
        python3 "$ROOT/scripts/warp.py" disable --confirm ;;
    0) ;;
    *) echo 'No action' ;;
  esac
}

subscriptions() {
  local n backend
  echo "1) Initialize private subscription token"
  echo "2) Publish pre-generated local subscription files"
  echo "3) Print private addresses (do not share screenshots)"
  echo "4) Rotate token / revoke old URLs"
  echo "5) Launch private IPv4/IPv6 loopback subscription server (foreground)"
  echo "0) Back"
  read -r -p 'Select: ' n || return 0
  case "$n" in
    1)
      require_root || return 1
      read -r -p 'Local subscription loopback [dual/ipv4/ipv6] (default dual): ' backend || return 0
      backend="${backend:-dual}"
      case "$backend" in
        dual|ipv4|ipv6) python3 "$ROOT/scripts/local_subscriptions.py" init --loopback-mode "$backend" ;;
        *) echo 'Invalid subscription loopback mode' >&2; return 1 ;;
      esac ;;
    2)
      require_root || return 1
      local p
      read -r -p 'Existing private subscription directory: ' p || return 0
      [[ -n "$p" ]] && python3 "$ROOT/scripts/local_subscriptions.py" publish --source-dir "$p"
      ;;
    3) require_root && python3 "$ROOT/scripts/local_subscriptions.py" urls ;;
    4)
      require_root || return 1
      local confirmation
      read -r -p 'Type ROTATE to revoke prior URL: ' confirmation || return 0
      if [[ "$confirmation" == ROTATE ]]; then
        python3 "$ROOT/scripts/local_subscriptions.py" rotate
      fi ;;
    5)
      require_root && python3 "$ROOT/scripts/local_subscriptions.py" serve ;;
    0) ;;
    *) echo 'No action' ;;
  esac
}
menu() {
  echo 'Checking official stable sing-box version; this is read-only.'
  python3 "$ROOT/scripts/update.py" --check || echo 'Official version check unavailable; no changes.' >&2
  local selection port hostname confirmation server snap mode listen_addr ip4 ip6
  while true; do
    printf '\n=== Smart (singbox-vps-safe) — owner-managed ===\n'
    echo '1) Dedicated service status'
    echo '2) Read-only install plan and port inventory'
    echo '3) Install dedicated VLESS-Reality (root, first install only)'
    echo '4) Check official stable update'
    echo '5) Manually upgrade sing-box core (backup + rollback)'
    echo '6) Snapshot current dedicated service'
    echo '7) List snapshots / restore with confirmation'
    echo '8) Export private client subscriptions'
    echo '9) Local subscription management'
    echo '10) Independent KejiLion / BBRv3 information (NO install)'
    echo '11) Scoped uninstall (preserve backups)'\n    echo '12) Read-only installation integrity check'
    echo '13) Optional WARP IPv4/IPv6 proxy egress (OFF by default)'
    echo '0) Exit'
    read -r -p 'Select [0-13]: ' selection || return 0
    case "$selection" in
      1) status ;;
      2) bash "$ROOT/scripts/install.sh" --plan ;;
      3)
        require_root || continue
        read -r -p 'Port: ' port || return 0
        read -r -p 'Reality handshake DNS hostname: ' hostname || return 0
        read -r -p 'Network [dual/ipv4/ipv6] (default dual): ' mode || return 0
        mode="${mode:-dual}"
        case "$mode" in dual|ipv4|ipv6) ;; *) echo 'Unsupported mode'; continue ;; esac
        if [[ "$mode" == ipv6 ]]; then
          read -r -p 'Concrete VPS IPv6 listening address (not ::): ' listen_addr || return 0
          [[ -n "$listen_addr" ]] || { echo 'IPv6 address required'; continue; }
        fi
        echo 'Only the dedicated service will be created. No firewall will be opened.'
        read -r -p 'Type INSTALL to approve: ' confirmation || return 0
        if [[ "$confirmation" == INSTALL ]]; then
          if [[ "$mode" == ipv6 ]]; then
            bash "$ROOT/scripts/install.sh" --install-reality "$port" "$hostname" --network-mode ipv6 --listen "$listen_addr"
          else
            bash "$ROOT/scripts/install.sh" --install-reality "$port" "$hostname" --network-mode "$mode"
          fi
        fi
        ;;
      4) python3 "$ROOT/scripts/update.py" --check ;;
      5)
        require_root || continue
        read -r -p 'Type UPDATE to approve core-only upgrade and restart: ' confirmation || return 0
        [[ "$confirmation" == UPDATE ]] && python3 "$ROOT/scripts/update.py" --update --confirm
        ;;
      6) require_root && python3 "$ROOT/scripts/state.py" --backup ;;
      7)
        require_root || continue
        python3 "$ROOT/scripts/state.py" --list
        read -r -p 'Snapshot ID to restore (empty to cancel): ' snap || return 0
        if [[ -n "$snap" ]]; then
          read -r -p 'Type RESTORE to confirm: ' confirmation || return 0
          [[ "$confirmation" == RESTORE ]] &&
            python3 "$ROOT/scripts/state.py" --restore "$snap" --confirm
        fi ;;
      8)
        require_root || continue
        read -r -p 'Public DNS/IP (blank to enter both IPv4 and IPv6): ' server || return 0
        if [[ -n "$server" ]]; then
          python3 "$ROOT/scripts/export_subscriptions.py" --server "$server"
        else
          read -r -p 'Public IPv4 address: ' ip4 || return 0
          read -r -p 'Public IPv6 address: ' ip6 || return 0
          [[ -n "$ip4" && -n "$ip6" ]] || { echo 'Both addresses are required'; continue; }
          python3 "$ROOT/scripts/export_subscriptions.py" --server-ipv4 "$ip4" --server-ipv6 "$ip6"
        fi ;;
      9) subscriptions ;;
      10) extensions ;;
      11)
        require_root || continue
        bash "$ROOT/scripts/uninstall.sh" --plan
        read -r -p 'Type UNINSTALL to remove dedicated service: ' confirmation || return 0
        [[ "$confirmation" == UNINSTALL ]] &&
          bash "$ROOT/scripts/uninstall.sh" --uninstall --confirm
        ;;
      12)
        require_root || continue
        python3 "$ROOT/scripts/doctor.py" --strict ;;
      13) warp_menu ;;
      0) return 0 ;;
      *) echo 'Invalid choice' ;;
    esac
  done
}
if [[ $# -eq 0 ]]; then MODE=--menu; else MODE="$1"; shift; fi
case "$MODE" in
  --help|-h) usage ;;
  --menu) menu ;;
  --status) status ;;
  --doctor) require_root; python3 "$ROOT/scripts/doctor.py" --strict ;;
  --plan) bash "$ROOT/scripts/install.sh" --plan ;;
  --install-reality) require_root; bash "$ROOT/scripts/install.sh" --install-reality "$@" ;;
  --install) require_root; bash "$ROOT/scripts/install.sh" --install "$@" ;;
  --generate) python3 "$ROOT/scripts/generate_config.py" "$@" ;;
  --combine) python3 "$ROOT/scripts/combine_configs.py" "$@" ;;
  --check-update) python3 "$ROOT/scripts/update.py" --check ;;
  --update-core)
    [[ $# -eq 1 && "$1" == --confirm ]] || { usage; exit 2; }
    require_root
    python3 "$ROOT/scripts/update.py" --update --confirm ;;
  --backup) require_root; python3 "$ROOT/scripts/state.py" --backup ;;
  --snapshots) require_root; python3 "$ROOT/scripts/state.py" --list ;;
  --restore)
    [[ $# -eq 2 && "$2" == --confirm ]] || { usage; exit 2; }
    require_root; python3 "$ROOT/scripts/state.py" --restore "$1" --confirm ;;
  --export-clients)
    require_root
    if [[ $# -ge 1 && "$1" == --* ]]; then
      python3 "$ROOT/scripts/export_subscriptions.py" "$@"
    elif [[ $# -eq 2 ]]; then
      python3 "$ROOT/scripts/export_subscriptions.py" --server "$1" --client-meta "$2"
    elif [[ $# -eq 1 ]]; then
      python3 "$ROOT/scripts/export_subscriptions.py" --server "$1"
    else
      usage; exit 2
    fi ;;
  --subscription-init) require_root; python3 "$ROOT/scripts/local_subscriptions.py" init "$@" ;;
  --subscription-publish) require_root; python3 "$ROOT/scripts/local_subscriptions.py" publish "$@" ;;
  --subscription-urls) require_root; python3 "$ROOT/scripts/local_subscriptions.py" urls ;;
  --subscription-rotate) require_root; python3 "$ROOT/scripts/local_subscriptions.py" rotate "$@" ;;
  --subscription-serve) require_root; python3 "$ROOT/scripts/local_subscriptions.py" serve ;;
  --extension-sources) python3 "$ROOT/scripts/extensions.py" sources ;;
  --extension-status) python3 "$ROOT/scripts/extensions.py" status ;;
  --warp-status) require_root; python3 "$ROOT/scripts/warp.py" status ;;
  --warp-plan) require_root; python3 "$ROOT/scripts/warp.py" plan "$@" ;;
  --warp-enable) require_root; python3 "$ROOT/scripts/warp.py" enable "$@" ;;
  --warp-disable) require_root; python3 "$ROOT/scripts/warp.py" disable "$@" ;;
  --uninstall)
    [[ $# -eq 1 && "$1" == --confirm ]] || { usage; exit 2; }
    require_root; bash "$ROOT/scripts/uninstall.sh" --uninstall --confirm ;;
  *) usage; exit 2 ;;
esac
