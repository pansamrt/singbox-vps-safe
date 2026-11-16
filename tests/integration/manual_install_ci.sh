#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Disposable Docker-only integration: NEVER run on a VPS.
set -Eeuo pipefail
umask 077
[[ -f /fixture/core.tgz && -f /work/scripts/install.sh ]] || exit 2
[[ "$(uname -m)" == x86_64 ]] || exit 2
mkdir -p /var/tmp/mockbin /tmp/mockctl
cat > /var/tmp/mockbin/curl <<'MOCKCURL'
#!/usr/bin/env bash
set -Eeuo pipefail
expected='https://github.com/SagerNet/sing-box/releases/download/v1.14.4/sing-box-1.14.4-linux-amd64.tar.gz'
url=''
dest=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) dest="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    --insecure|-k) echo 'TLS bypass not allowed' >&2; exit 1 ;;
    *) shift ;;
  esac
done
[[ "$url" == "$expected" && "$dest" == /* ]] || exit 1
cp /fixture/core.tgz "$dest"
MOCKCURL
cat > /var/tmp/mockbin/systemctl <<'MOCKSYSTEMCTL'
#!/usr/bin/env bash
set -Eeuo pipefail
operation="$1"
if [[ "$operation" == daemon-reload ]]; then exit 0; fi
if [[ "$operation" == reset-failed ]]; then exit 0; fi
if [[ "$operation" == is-active ]]; then
  [[ "$2" == --quiet && "$3" == singbox-vps-safe.service ]] || exit 40
  [[ -f /tmp/mockctl/active ]] && exit 0 || exit 3
fi
if [[ "$operation" == start ]]; then
  [[ "$2" == singbox-vps-safe.service ]] || exit 41
  if [[ -f /tmp/mockctl/fail-start ]]; then exit 1; fi
  runuser -u singbox-safe -- /opt/singbox-vps-safe/sing-box check \
    -c /etc/singbox-vps-safe/config.json >/dev/null
  touch /tmp/mockctl/active
  exit 0
fi
if [[ "$operation" == enable ]]; then
  [[ "$2" == singbox-vps-safe.service ]] || exit 42
  touch /tmp/mockctl/enabled
  exit 0
fi
if [[ "$operation" == disable ]]; then
  [[ "$2" == --now && "$3" == singbox-vps-safe.service ]] || exit 43
  rm -f /tmp/mockctl/active /tmp/mockctl/enabled
  exit 0
fi
if [[ "$operation" == stop ]]; then
  [[ "$2" == singbox-vps-safe.service ]] || exit 44
  rm -f /tmp/mockctl/active
  exit 0
fi
echo "Unexpected systemctl operation: $*" >&2
exit 45
MOCKSYSTEMCTL
chmod 755 /var/tmp/mockbin/*
export PATH="/var/tmp/mockbin:$PATH"
export SAFE_OFFLINE_ARCHIVE=/fixture/core.tgz
cd /work
cp /etc/resolv.conf /tmp/original-resolv.conf
echo "FAKE_SSH_REMAINS" > /tmp/ssh-sentinel

bash scripts/install.sh --plan > /tmp/plan.log
[[ ! -e /opt/singbox-vps-safe && ! -e /etc/systemd/system/singbox-vps-safe.service ]]
# An IPv6-only bind to an unassigned documentation address MUST fail before
# creating users, changing a service or writing installation files.
if bash scripts/install.sh --install-reality 53579 www.example.org \
  --network-mode ipv6 --listen 2001:db8::cafe >/tmp/ipv6-unassigned.log 2>&1; then
  echo "ERROR: unassigned IPv6 address unexpectedly passed preflight" >&2
  exit 1
fi
test ! -e /opt/singbox-vps-safe
test ! -e /etc/systemd/system/singbox-vps-safe.service
test ! -e /etc/singbox-vps-safe
test ! -e /var/lib/singbox-vps-safe
! getent passwd singbox-safe >/dev/null
echo "PASS: unassigned IPv6 preflight fails before any system changes"
bash scripts/install.sh --install-reality 53579 www.example.org
python3 - <<'PY'
import json
with open("/etc/singbox-vps-safe/config.json", encoding="utf8") as f:
    config = json.load(f)
assert config["inbounds"][0]["listen"] == "::"
print("PASS: default installed Reality configuration is dual-stack ::")
PY
test -x /opt/singbox-vps-safe/sing-box
test -f /etc/systemd/system/singbox-vps-safe.service
test -f /var/lib/singbox-vps-safe/client.json
test "$(stat -c %a /etc/singbox-vps-safe/config.json)" = 640
grep -Fxq 'User=singbox-safe' /etc/systemd/system/singbox-vps-safe.service
runuser -u singbox-safe -- /opt/singbox-vps-safe/sing-box check \
  -c /etc/singbox-vps-safe/config.json
test -f /tmp/mockctl/active
test -f /tmp/mockctl/enabled
echo "PASS: actual native Reality generation and dedicated nonroot config access"

python3 scripts/state.py --backup
SNAP="$(python3 scripts/state.py --list | tail -n 1)"
test -n "$SNAP"
python3 scripts/state.py --restore "$SNAP" --confirm
runuser -u singbox-safe -- /opt/singbox-vps-safe/sing-box check \
  -c /etc/singbox-vps-safe/config.json
echo "PASS: versioned snapshot + real file restore and service group access"

python3 scripts/export_subscriptions.py --server 127.0.0.1 --output-parent /tmp \
  > /tmp/subexport.log
FOLDER="$(find /tmp -maxdepth 1 -type d -name 'client-subscriptions-*' | head -n 1)"
test -f "$FOLDER/sing-box"
test -f "$FOLDER/clash"
python3 scripts/local_subscriptions.py --state-dir /tmp/localsubs init
python3 scripts/local_subscriptions.py --state-dir /tmp/localsubs publish \
  --source-dir "$FOLDER"
python3 - <<'PY'
import json
from pathlib import Path
from sys import path
path.insert(0, "/work/scripts")
import local_subscriptions as sub
root = Path("/tmp/localsubs")
record = sub.settings(root)
assert record["token"] != "01234567-89ab-4def-8123-0123456789ab"
assert record["generation"] is not None
print("PASS: private loopback subscription generated from installed credentials")
PY

bash scripts/uninstall.sh --plan
bash scripts/uninstall.sh --uninstall --confirm
test ! -e /etc/systemd/system/singbox-vps-safe.service
test ! -e /opt/singbox-vps-safe/sing-box
test ! -e /etc/singbox-vps-safe/config.json
if getent passwd singbox-safe >/dev/null; then exit 1; fi
test -f /var/lib/singbox-vps-safe/client.json
test -f /tmp/ssh-sentinel
cmp /etc/resolv.conf /tmp/original-resolv.conf
echo "PASS: scoped uninstall preserves private records and untouched DNS"

# Exercise real Hysteria2 TLS initial install with pre-existing manually supplied
# certificate/key. All operations are inside this disposable Docker overlay.
rm -rf /var/lib/singbox-vps-safe
mkdir -p /var/tmp/test-tls
openssl req -new -x509 -days 1 -nodes -newkey rsa:2048 \
  -subj '/CN=vpn.example.org' \
  -keyout /var/tmp/test-tls/private.key \
  -out /var/tmp/test-tls/cert.pem >/dev/null 2>&1
chmod 600 /var/tmp/test-tls/private.key
python3 scripts/generate_config.py --protocol hysteria2 --port 53580 \
  --server-name vpn.example.org \
  --tls-cert /var/tmp/test-tls/cert.pem \
  --tls-key /var/tmp/test-tls/private.key \
  --output /var/tmp/test-tls/hy2.json
bash scripts/install.sh --install /var/tmp/test-tls/hy2.json
test -f /etc/singbox-vps-safe/managed-tls/tls-0.key
test "$(stat -c %a /etc/singbox-vps-safe/managed-tls/tls-0.key)" = 640
test "$(stat -c %a /var/tmp/test-tls/private.key)" = 600
grep -Fq '/etc/singbox-vps-safe/managed-tls/tls-0.key' /etc/singbox-vps-safe/config.json
runuser -u singbox-safe -- /opt/singbox-vps-safe/sing-box check \
  -c /etc/singbox-vps-safe/config.json
echo "PASS: Hysteria2 TLS installed and native-checked with nonroot readable copied key"
# Verify snapshot captures the actual managed TLS key/certificate and safely
# recovers an invalid key on the disposable Debian test filesystem.
python3 scripts/state.py --backup
TLS_SNAPSHOT="$(python3 scripts/state.py --list | tail -n 1)"
test -n "$TLS_SNAPSHOT"
test -f "/var/lib/singbox-vps-safe/snapshots/$TLS_SNAPSHOT/tls/tls-0.key"
cp /etc/singbox-vps-safe/managed-tls/tls-0.key /tmp/ci-original-tls.key
printf 'invalid changed key\n' > /etc/singbox-vps-safe/managed-tls/tls-0.key
chown root:singbox-safe /etc/singbox-vps-safe/managed-tls/tls-0.key
chmod 640 /etc/singbox-vps-safe/managed-tls/tls-0.key
python3 scripts/state.py --restore "$TLS_SNAPSHOT" --confirm
cmp /tmp/ci-original-tls.key /etc/singbox-vps-safe/managed-tls/tls-0.key
runuser -u singbox-safe -- /opt/singbox-vps-safe/sing-box check -c /etc/singbox-vps-safe/config.json
echo "PASS: actual managed TLS key restored from hashed backup after key corruption"
bash scripts/uninstall.sh --uninstall --confirm
test -f /etc/singbox-vps-safe/managed-tls/tls-0.key
test "$(stat -c %a /etc/singbox-vps-safe/managed-tls/tls-0.key)" = 600
test "$(stat -c %g /etc/singbox-vps-safe/managed-tls/tls-0.key)" = 0
echo "PASS: scoped uninstall preserves certificates but revokes retired service-group access"
# Purge only explicitly known CI-created fixtures inside disposable container.
rm -rf /etc/singbox-vps-safe/managed-tls /var/lib/singbox-vps-safe
rmdir /etc/singbox-vps-safe

# Simulate systemd startup failure on a fresh isolated installation.
touch /tmp/mockctl/fail-start
if bash scripts/install.sh --install-reality 53578 www.example.org; then
  echo "FAIL: expected simulated systemd startup failure" >&2
  exit 1
fi
test ! -e /etc/systemd/system/singbox-vps-safe.service
test ! -e /opt/singbox-vps-safe/sing-box
test ! -e /etc/singbox-vps-safe/config.json
test ! -e /var/lib/singbox-vps-safe/client.json
if getent passwd singbox-safe >/dev/null; then exit 1; fi
test ! -e /tmp/mockctl/active
test -f /tmp/ssh-sentinel
cmp /etc/resolv.conf /tmp/original-resolv.conf
echo 'PASS: failed install rolled back own system files without touching DNS or SSH sentinel'
echo 'SAFE_MANUAL_INSTALL_CI_PASS'
