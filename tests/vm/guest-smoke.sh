#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# CI-ONLY full Debian QEMU guest smoke test. Never run on a real VPS.
set -Eeuo pipefail
umask 077

UNIT=/etc/systemd/system/singbox-vps-safe-ci.service
BIN=/opt/singbox-vps-safe-ci/sing-box
CFG=/etc/singbox-vps-safe-ci/config.json
SRC=/mnt/sbci
AFTER=/etc/systemd/system/sbci-afterreboot.service
MARKER=/root/sbci-first-stage-ok

say() {
  printf 'SBVM: %s\n' "$*" | tee /dev/ttyS0
}
die() {
  say "FAILED: $*"
  exit 1
}
hash_file() {
  if [ -f "$1" ]; then
    sha256sum "$1" | cut -d' ' -f1
  else
    echo "ABSENT"
  fi
}
snapshot_invariants() {
  {
    hash_file /etc/resolv.conf
    hash_file /etc/ssh/sshd_config
    hash_file /etc/ufw/before.rules
    hash_file /etc/ufw/before6.rules
    systemctl is-active ssh.service || true
    systemctl is-active sing-box.service || true
    systemctl is-active nginx.service || true
  } > /root/sbci-invariants.before
  if command -v nft >/dev/null 2>&1; then
    nft list ruleset > /root/sbci-nft.before 2>/dev/null || true
  fi
  if command -v iptables-save >/dev/null 2>&1; then
    iptables-save > /root/sbci-iptables.before 2>/dev/null || true
  fi
}
check_invariants() {
  {
    hash_file /etc/resolv.conf
    hash_file /etc/ssh/sshd_config
    hash_file /etc/ufw/before.rules
    hash_file /etc/ufw/before6.rules
    systemctl is-active ssh.service || true
    systemctl is-active sing-box.service || true
    systemctl is-active nginx.service || true
  } > /root/sbci-invariants.after
  cmp -s /root/sbci-invariants.before /root/sbci-invariants.after ||
    die "Unrelated SSH/DNS/UFW/service state unexpectedly changed"
  if [ -f /root/sbci-nft.before ]; then
    nft list ruleset > /root/sbci-nft.after 2>/dev/null || true
    cmp -s /root/sbci-nft.before /root/sbci-nft.after ||
      die "Unrelated nft rules changed"
  fi
  if [ -f /root/sbci-iptables.before ]; then
    iptables-save > /root/sbci-iptables.after 2>/dev/null || true
    cmp -s /root/sbci-iptables.before /root/sbci-iptables.after ||
      die "Unrelated iptables rules changed"
  fi
  say "Unrelated service, firewall and DNS state unchanged"
}
first_stage() {
  [ "$EUID" -eq 0 ] || die "Guest test must run as root"
  [ -x "$SRC/sing-box" ] || die "Signed-off test binary not available in guest ISO"
  [ ! -e "$UNIT" ] || die "Refuse to overwrite existing unit"
  [ ! -e "$CFG" ] || die "Refuse to overwrite existing config"
  [ ! -e "$BIN" ] || die "Refuse to overwrite existing binary"
  if getent passwd sbci >/dev/null 2>&1; then die "Refuse existing test system user"; fi
  snapshot_invariants
  install -d -m 755 /opt/singbox-vps-safe-ci
  install -d -m 700 /etc/singbox-vps-safe-ci
  install -m 755 "$SRC/sing-box" "$BIN"
  cat > "$CFG" <<'JSON'
{
  "log": {"level": "warn"},
  "inbounds": [{"type":"socks","tag":"ci-socks","listen":"127.0.0.1","listen_port":21980}],
  "outbounds": [{"type":"direct","tag":"direct"}],
  "route": {"final":"direct"}
}
JSON
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin sbci
  chown root:sbci /etc/singbox-vps-safe-ci "$CFG"
  chmod 750 /etc/singbox-vps-safe-ci
  chmod 640 "$CFG"
  "$BIN" version | grep -Fq "sing-box version 1.14.4" ||
    die "Verified core version changed"
  "$BIN" check -c "$CFG" || die "Real core configuration validation failed"
  say "Official sing-box core and configuration checked"

  cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=CI only dedicated sing-box test service
After=local-fs.target

[Service]
Type=simple
User=sbci
Group=sbci
ExecStart=/opt/singbox-vps-safe-ci/sing-box run -c /etc/singbox-vps-safe-ci/config.json
Restart=on-failure
RestartSec=1
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNITFILE
  chmod 644 "$UNIT"
  systemctl daemon-reload
  systemctl enable --now singbox-vps-safe-ci.service
  sleep 2
  systemctl is-active --quiet singbox-vps-safe-ci.service ||
    die "Dedicated VM service did not start"
  say "Installed and started dedicated systemd service"

  # A new systemd one-shot runs only after the NEXT boot.
  install -m 755 "$SRC/guest-smoke.sh" /usr/local/sbin/sbci-vm-smoke
  cat > "$AFTER" <<'AFTERFILE'
[Unit]
Description=CI verify safe sing-box restart and uninstall after reboot
Requires=singbox-vps-safe-ci.service
After=singbox-vps-safe-ci.service

[Service]
Type=oneshot
ExecStart=/bin/bash /usr/local/sbin/sbci-vm-smoke after-reboot

[Install]
WantedBy=multi-user.target
AFTERFILE
  chmod 644 "$AFTER"
  systemctl daemon-reload
  systemctl enable sbci-afterreboot.service
  printf 'OK\n' > "$MARKER"
  say "SBVM_FIRST_STAGE_PASS"
  # Cloud-init power_state will reboot only after the first stage has returned.
}
after_reboot() {
  [ -f "$MARKER" ] || die "First-stage marker missing after reboot"
  systemctl is-active --quiet singbox-vps-safe-ci.service ||
    die "Dedicated service failed to restart after actual VM reboot"
  "$BIN" check -c "$CFG" || die "Installed config failed after reboot"
  say "SBVM_REBOOT_RECOVERY_PASS"

  # Fail an incompatible candidate config without replacing active files.
  printf '{"inbounds": [\n' > /tmp/sbci-invalid.json
  if "$BIN" check -c /tmp/sbci-invalid.json > /dev/null 2>&1; then
    die "Invalid candidate unexpectedly passed"
  fi
  systemctl is-active --quiet singbox-vps-safe-ci.service ||
    die "Failed preflight affected running service"
  say "SBVM_FAILED_UPDATE_PREFLIGHT_PASS"

  # Uninstall only resources created by the disposable CI test.
  systemctl disable --now singbox-vps-safe-ci.service
  rm -f -- "$UNIT" "$CFG" "$BIN"
  rmdir /opt/singbox-vps-safe-ci /etc/singbox-vps-safe-ci
  userdel sbci
  systemctl daemon-reload
  systemctl reset-failed singbox-vps-safe-ci.service >/dev/null 2>&1 || true
  [ ! -e "$UNIT" ] && [ ! -e "$CFG" ] && [ ! -e "$BIN" ] ||
    die "Owned resources not fully removed"
  check_invariants
  say "SBVM_INSTALL_REBOOT_FAILURE_UNINSTALL_PASS"
  # Power off disposable VM; this does not affect the GitHub runner.
  systemctl poweroff --no-wall
}
case "$1" in
  first) first_stage ;;
  after-reboot) after_reboot ;;
  *) die "Unknown CI stage" ;;
esac
