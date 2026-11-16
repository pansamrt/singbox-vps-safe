# Smart — singbox-vps-safe

**Smart** is the owner-controlled sing-box installer and local subscription manager. **Status: manual-test candidate; not yet production-approved. No deployment to the owner's VPS has occurred.**

**Upstream attribution (retained for GPL-3.0 compliance):** Based on [fscarmen/sing-box](https://github.com/fscarmen/sing-box), fixed upstream
commit [7a666fc](https://github.com/fscarmen/sing-box/commit/7a666fc870d027790b6b831d219d8d99f13a73a4).
Retains GPL-3.0 and original authorship. The previous yonggekkk baseline is cancelled.

The original upstream installation script is archived under `upstream/fscarmen` for audit,
**NOT for direct root execution**. Read [UPSTREAM_AUDIT.md](UPSTREAM_AUDIT.md).

## Implemented functions

- Dedicated, manually initiated installer with a standalone non-root
  singbox-safe systemd user and singbox-vps-safe.service; existing proxy,
  SSH, firewall, DNS, routing, WARP, and kernel/BBR are not edited.
- Fixed official sing-box 1.14.4 core (SHA256-verified for amd64/arm64).
- First-install VLESS-Reality with local keypair generation, or user-reviewed
  JSON configurations for Hysteria2, TUIC and AnyTLS with manual TLS material.
- Default IPv4 + IPv6 dual-stack inbound, explicit IPv4-only or concrete IPv6-only
  modes, and both-family TCP/UDP port preflight before system mutation.
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
# Default: dual-stack (::) for IPv4 + IPv6, subject to VPS IPv6 support
sudo bash install.sh --status
sudo systemctl status singbox-vps-safe.service
~~~

The service binds only the ports specified in your configuration. It never
opens firewalls automatically. The example handshake domain must be replaced
with one you understand and have verified.

**Smart network modes (new installs, not in-place changes):**

~~~bash
# Default: IPv4 + IPv6 in one :: wildcard listener, both-family port checks
sudo bash install.sh --install-reality 8443 www.example.org

# IPv4-only wildcard
sudo bash install.sh --install-reality 8443 www.example.org --network-mode ipv4

# IPv6-only: requires your VPS's assigned concrete IPv6 address; :: is rejected
sudo bash install.sh --install-reality 8443 www.example.org \
  --network-mode ipv6 --listen 2001:db8::10
~~~

The IPv6 example uses a **documentation-only** address; replace it with an
IPv6 address actually assigned to the test VPS. Smart does not allocate public
IPv6 addresses, modify DNS AAAA records or open the host/provider firewall.
Dual mode uses the single `::` inbound; a real IPv4 and IPv6 connection
test must succeed for the VPS's kernel/network. The preflight verifies both
TCP or UDP port families, but it cannot prove public Internet reachability.
Only-IPv6 mode requires a concrete native address to avoid accepting
IPv4-mapped clients on an IPv6 wildcard.

For user-provided Hysteria2, TUIC or AnyTLS configurations, the generator
defaults to dual-stack too. Use `--network-mode ipv4` or
`--network-mode ipv6 --listen YOUR_ASSIGNED_IPV6` to override.

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
# Or export separate IPv4 and IPv6 endpoints from the same node credentials:
sudo bash install.sh --export-clients \
  --server-ipv4 YOUR_PUBLIC_IPV4 --server-ipv6 YOUR_PUBLIC_IPV6
# For a DNS name with A and AAAA records, use --export-clients YOUR_DOMAIN instead.

# This prints a private output directory; use THAT actual directory below.
sudo bash install.sh --subscription-init
sudo bash install.sh --subscription-publish \
  --source-dir /var/lib/singbox-vps-safe/client-subscriptions-EXAMPLE
sudo bash install.sh --subscription-urls
sudo bash install.sh --subscription-serve
~~~

IPv6 link URIs are enclosed in brackets (for example,
`vless://...@[2001:db8::10]:8443`), while sing-box and Clash JSON/YAML
`server` fields contain an unbracketed literal. When both public IPs are
supplied, each protocol appears as two separate client nodes; their keys
are generated locally and **not** duplicated as extra server inbounds.

Local subscription serving **also defaults to dual-stack loopback only**:
`127.0.0.1:8787` and `[::1]:8787`. For a truly IPv6-only test VPS,
initialize it with:

~~~bash
sudo bash install.sh --subscription-init --loopback-mode ipv6
# The local-only URL then begins with http://[::1]:8787/PRIVATE_TOKEN/
# Configure the owner-managed HTTPS reverse proxy to [::1]:8787.
~~~

You can also select `--loopback-mode ipv4` if the test VPS has IPv6
disabled. These are **loopback** addresses, not Internet listeners; no
firewall, web server, DNS or TLS certificate is automatically configured.
If already initialized, changing loopback mode requires an owner-managed
migration; token rotation does not change the stored listener mode.

The directory suffix EXAMPLE is not literal; use the path printed by the
exporter. All subscription credentials are saved privately. The default printed URL
is http://127.0.0.1:8787/PRIVATE_TOKEN/auto (also accessible from ::1
when loopback mode is dual), and works only locally.
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

### Post-install integrity inspection (read-only)

After the manual installation, run:

~~~bash
sudo bash install.sh --doctor
~~~

This checks root ownership, service account/group, private config and managed
TLS file modes, required systemd isolation settings, and runs the installed
native `sing-box check`. It does not print passwords or keys and never
changes SSH, firewall, DNS, kernel, other proxy services or files.

Versioned backups now include dedicated `managed-tls/tls-*.key` and
`tls-*.crt` files (private SHA256-verified snapshot schema v2), rather than
only program/config/systemd. Restoration checks archived TLS before stopping
the service and revalidates installed files after the copy.

**Compatibility notice:** backups created by the earlier schema-v1 implementation
did not contain TLS private keys and are intentionally refused by the new
restore routine. Preserve those older snapshots manually; create a fresh
schema-v2 backup **after** upgrading this project. Do not assume old snapshots
can reliably restore Hysteria2/TUIC/AnyTLS certificates.

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
