# singbox-vps-safe — owner-controlled sing-box VPS installer

**Status: manual-test candidate; not yet production-approved. No deployment to the owner's VPS has occurred.**

Based on [fscarmen/sing-box](https://github.com/fscarmen/sing-box), fixed upstream
commit [7a666fc](https://github.com/fscarmen/sing-box/commit/7a666fc870d027790b6b831d219d8d99f13a73a4).
Retains GPL-3.0 and original authorship. The previous yonggekkk baseline is cancelled.

The original fscarmen script is archived under upstream/fscarmen for audit,
**NOT for direct root execution**. Read [UPSTREAM_AUDIT.md](UPSTREAM_AUDIT.md).

## Implemented functions

- Dedicated, manually initiated installer with a standalone non-root
  singbox-safe systemd user and singbox-vps-safe.service; existing proxy,
  SSH, firewall, DNS, routing, WARP, and kernel/BBR are not edited.
- Fixed official sing-box 1.14.4 core (SHA256-verified for amd64/arm64).
- First-install VLESS-Reality with local keypair generation, or user-reviewed
  JSON configurations for Hysteria2, TUIC and AnyTLS with manual TLS material.
- IPv4/IPv6 and TCP/UDP socket preflight; config checks before system mutation.
- Private local client subscriptions for sing-box, V2RayN, Clash/Mihomo,
  Shadowrocket, plus independent revocable token and loopback-only HTTP server.
- Root-only versioned snapshots, manual confirmed core-only upgrades with
  checksums and startup rollback, and scoped uninstall.
- KejiLion and Actions-bbr-v3 are optional **read-only source/status tools**,
  never installed as part of sing-box.

## Manual test (new disposable VPS strongly recommended)

Repository is private. Authenticate to GitHub yourself using your usual
GitHub CLI / SSH access. Never paste GitHub tokens into a public command.

~~~bash
gh repo clone pansamrt/singbox-vps-safe
cd singbox-vps-safe

# After the tested changes are merged, use the default branch:
git switch main

# Always first inspect ports; no system changes:
bash install.sh --plan

# Interactive management menu, no automatic installation:
bash install.sh --menu
~~~

Initial review environment: Debian 12 or 13 amd64. Ubuntu 24/26 and arm64
have code-level support but remain pending the same full integration tests.
Python 3.10+, curl, tar, coreutils, iproute2, systemd and user-account utilities
must be installed by the owner beforehand.

### First install: VLESS-Reality

~~~bash
# 8443 is only an example. Use a free port and a suitable real handshake DNS host.
sudo bash install.sh --install-reality 8443 www.example.org
sudo bash install.sh --status
sudo systemctl status singbox-vps-safe.service
~~~

The service binds only the ports specified in your configuration. It never
opens firewalls automatically. The example handshake domain must be replaced
with one you understand and have verified.

Dedicated installed paths:

- /opt/singbox-vps-safe/sing-box
- /etc/singbox-vps-safe/config.json (root:singbox-safe, mode 0640)
- /etc/systemd/system/singbox-vps-safe.service
- /var/lib/singbox-vps-safe/client.json (root-only, where generated)
- /var/lib/singbox-vps-safe/snapshots/ (root-only backups)

### Hysteria2 / TUIC / AnyTLS

You must provision a real certificate and matching private key yourself.
The generator does not issue certificates, control DNS, or modify firewall
rules. For example:

~~~bash
python3 scripts/generate_config.py \
  --protocol hysteria2 --port 8443 --server-name vpn.example.org \
  --tls-cert /absolute/path/fullchain.pem \
  --tls-key /absolute/path/privkey.pem \
  --output ./hy2.json

# Review the private config and matching .client.json file.
sudo bash install.sh --install /absolute/path/hy2.json
~~~

The installer stages user-provided TLS files privately and copies them into
a dedicated managed-tls directory readable only by root and the service
group. The original TLS source files are not modified and input symlinks
are rejected. Real renewals still require an owner-managed certificate
workflow. For multiple inbounds, generate fragments individually and combine
with scripts/combine_configs.py before explicit installation.

For JSON-based Hysteria2, TUIC or AnyTLS installations, the locally generated
private client sidecar is **not** automatically copied into the VPS state.
Export from that specific sidecar by passing it as the second argument:

~~~bash
sudo bash install.sh --export-clients YOUR_VPS_PUBLIC_IP /absolute/path/hy2.json.client.json
~~~

### Local subscription URLs (no third-party conversion)

After a Reality first install:

~~~bash
# Replace with the actual public IP or DNS hostname, without URL scheme:
sudo bash install.sh --export-clients YOUR_VPS_PUBLIC_IP

# This prints a private output directory; use THAT actual directory below.
sudo bash install.sh --subscription-init
sudo bash install.sh --subscription-publish \
  --source-dir /var/lib/singbox-vps-safe/client-subscriptions-EXAMPLE
sudo bash install.sh --subscription-urls
sudo bash install.sh --subscription-serve
~~~

The directory suffix EXAMPLE is not literal; use the path printed by the
exporter. All subscription credentials are saved privately. The default URL
format is http://127.0.0.1:8787/PRIVATE_TOKEN/auto, which works only locally.
For access from other devices, you must **separately configure your own
trusted HTTPS reverse proxy** pointing to 127.0.0.1:8787, then rotate the
public base URL:

~~~bash
sudo bash install.sh --subscription-rotate \
  --public-base-url https://sub.example.org
~~~

Changing the public base URL does not install nginx, obtain a certificate,
set DNS, or create public connectivity. Disable reverse-proxy access logs
for token-bearing paths. Never place subscription secrets in GitHub, logs,
screenshots or third-party QR generator services.

### Updates, rollback, and exact-scope uninstall

~~~bash
bash install.sh --check-update
sudo bash install.sh --backup
sudo bash install.sh --snapshots
sudo bash install.sh --update-core --confirm

# Replace with a real snapshot ID from --snapshots:
sudo bash install.sh --restore SNAPSHOT_ID --confirm

bash scripts/uninstall.sh --plan
sudo bash install.sh --uninstall --confirm
~~~

Uninstall removes only the owned service, binary and installed configuration.
TLS source files, private metadata and snapshots are preserved to prevent
accidental credential loss. Review and manually remove them separately if
no longer needed. There is **no unattended update timer or cron job**.

KejiLion / BBRv3 independent references:

~~~bash
bash install.sh --extension-sources
bash install.sh --extension-status
~~~

## Current acceptance status

- [x] GPL-3.0 upstream pinned and hazardous original install routes audited.
- [x] Python unit tests, real sing-box v1.14.4 server/client JSON checks.
- [x] Debian 12/13 disposable core runtime and HTTPS subscription testing.
- [x] Debian 12/13 disposable install: nonroot permission verification,
  versioned backup/restore, local credentials, subscriptions, scoped uninstall
  and simulated startup failure cleanup (systemctl simulated).
- [ ] Real systemd installation, reboot/recovery and uninstall in an isolated
  full Debian VM; previous QEMU work remains draft.
- [ ] Live end-to-end VLESS-Reality/Hysteria2/TUIC/AnyTLS network handshakes,
  trusted public CA, cert renewals, varied firewall scenarios and ARM64 testing.

**Do not confuse passed container tests with completed production acceptance.
No Germany VPS or existing Hysteria2 instance was accessed or changed.**

[SUBSCRIPTIONS.md](SUBSCRIPTIONS.md) · [UPSTREAM_AUDIT.md](UPSTREAM_AUDIT.md) ·
[GPL-3.0](LICENSE)
