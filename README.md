# singbox-vps-safe

**Status: development-only, no VPS deployment or production release.**

## New upstream baseline (2026-11-15)

**Based on [fscarmen/sing-box](https://github.com/fscarmen/sing-box).**
The old **yonggekkk/sing-box-yg baseline is cancelled and superseded**.

Fixed upstream commit: [7a666fc870d027790b6b831d219d8d99f13a73a4](https://github.com/fscarmen/sing-box/commit/7a666fc870d027790b6b831d219d8d99f13a73a4), upstream script v1.3.30.

A verbatim copy of the upstream installer is preserved under
[upstream/fscarmen/sing-box.sh](upstream/fscarmen/sing-box.sh) for audit and
future adaptation. It is **not yet safe or approved to execute**: the upstream
installer includes downloads without TLS verification and remote script
execution. Do not use the archived source as a root installer until reviewed.

## Priority: generate local subscription URLs safely

Fscarmen already creates client subscription files under
/etc/sing-box/subscribe/. The new, independent Python publisher uses these
existing **local** files without contacting third-party subscription
conversion services or QR generators. It:

- Copies only known formats: V2rayN, Clash/Mihomo, sing-box, Shadowrocket, Throne.
- Creates a random subscription bearer token **separate** from node UUIDs.
- Rewrites Clash proxy-provider URLs to the new subscription token path.
- Provides fixed-format URLs plus client-sensitive auto and auto2 endpoints.
- Supports explicit token rotation, including rewriting internal Clash URLs.
- Binds the HTTP service to 127.0.0.1 only. Public subscription access requires
  your own manual HTTPS reverse proxy and valid certificate.
- Does not change SSH, firewall, DNS, Nginx, systemd or existing proxy services.

### Manual commands (disposable test environment only)

Requires Python 3.10+ and an existing fscarmen subscription folder:

~~~bash
python3 scripts/local_subscriptions.py init
python3 scripts/local_subscriptions.py publish
python3 scripts/local_subscriptions.py urls
python3 scripts/local_subscriptions.py serve
python3 scripts/local_subscriptions.py rotate
~~~

Set a public HTTPS domain *only when* you separately configure its TLS
reverse proxy to the local server on 127.0.0.1:8787:

~~~bash
# When first initializing:
python3 scripts/local_subscriptions.py init --public-base-url https://sub.example.org

# Or rotate an existing access token and change the public base URL:
python3 scripts/local_subscriptions.py rotate --public-base-url https://sub.example.org
~~~

**No reverse proxy, TLS, DNS or firewall changes are performed by those
commands.** A declared HTTPS URL will not work until you manually configure
the corresponding HTTPS service.

Example endpoint shapes (placeholders are not real tokens):

- https://sub.example.org/PRIVATE_TOKEN/auto — select based on User-Agent
- https://sub.example.org/PRIVATE_TOKEN/clash — Clash
- https://sub.example.org/PRIVATE_TOKEN/proxies — Clash proxy provider
- https://sub.example.org/PRIVATE_TOKEN/sing-box — sing-box
- https://sub.example.org/PRIVATE_TOKEN/v2rayn — V2rayN
- https://sub.example.org/PRIVATE_TOKEN/shadowrocket — Shadowrocket

The token grants access to proxy credentials. Treat URL output as a secret;
avoid access logs, analytics, sharing screenshots, third-party QR APIs, and
committing credentials to GitHub. Unencrypted public HTTP is not supported.

See [SUBSCRIPTIONS.md](SUBSCRIPTIONS.md) for limitations and acceptance tests.

## Existing project requirements retained for later safe rebase

- Read-only sing-box version reminder and manually confirmed, verified core
  update with pre-update snapshot and failure rollback. Never unattended.
- KejiLion and Actions-bbr-v3 remain independent, opt-in VPS extensions.
  Neither is a dependency of sing-box installation; neither auto-runs.
- Previous yonggekkk-based draft PRs are obsolete and will not be merged.
- No VPS deployment, remote SSH actions, automatic upgrade, or unattended work.
  The owner chooses when and how to deploy after development completes.

**This branch provides a new baseline and an initial local subscription
implementation. It is not yet a complete, audited fscarmen installer.**

## GPL-3.0 and attribution

Upstream: https://github.com/fscarmen/sing-box
Fixed commit: 7a666fc870d027790b6b831d219d8d99f13a73a4.

Original contributors retain their authorship. Derived code and our new
local publisher use GPL-3.0. See [LICENSE](LICENSE).
