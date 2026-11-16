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
bash scripts/install.sh --install-reality 53579 www.example.org
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

# After complete uninstall, discard test-only state (isolated Docker container).
rm -rf /var/lib/singbox-vps-safe
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
