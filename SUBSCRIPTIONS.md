# Local subscriptions / 本地订阅架构

Upstream baseline: [fscarmen/sing-box](https://github.com/fscarmen/sing-box)
at 7a666fc870d027790b6b831d219d8d99f13a73a4 (2026-11-15).

## What is local

The upstream script writes client templates under /etc/sing-box/subscribe.
Our publisher reads only an allowlist of these files, makes a private local
snapshot, generates a subscription token unrelated to any node credentials,
and serves static outputs from 127.0.0.1:8787.

The network subscription URLs are built locally:

- /TOKEN/auto: client User-Agent chooses a locally available format
- /TOKEN/auto2: alternate Clash/sing-box format when one is available
- /TOKEN/v2rayn, /TOKEN/clash, /TOKEN/clash2, /TOKEN/proxies
- /TOKEN/sing-box, /TOKEN/sing-box2, /TOKEN/shadowrocket, /TOKEN/throne

Each path requires the independently generated private token. Requests with
other names, query strings, encoded path elements, wrong tokens, symlinks or
files outside the allowlist return 404. This is not an externally hosted
subscription converter; no proxy credentials are sent to conversion APIs.

## Deployment boundary

Default server is loopback-only HTTP. Never expose 8787 directly to the
Internet; do not advertise http://PUBLIC_IP/TOKEN as a secure subscription.
To use another device, the **owner** must configure a valid HTTPS reverse
proxy to 127.0.0.1:8787, with no access logs containing subscription paths,
no external analytics, and ideally an additional IP ACL or network access
restriction where practical. Certificates and DNS are outside this project.

The public-base URL option **only changes the generated URL text** and Clash
provider URL references. It does not deploy a reverse proxy. Token rotation
revokes the previously advertised URL, and republishes Clash provider URLs to
the new token. Do not upload generated state or subscribe/ contents to GitHub.

Example HTTPS reverse-proxy behavior (conceptual, not automatically installed):

- TLS terminates at the owner's domain and proxies to 127.0.0.1:8787
- Explicitly disable access logs for the subscription route
- Preserve the original URL path and User-Agent header
- Send Cache-Control: private, no-store and never cache credential payloads
- Do not rewrite to third-party QR, API or analytics endpoints

## Security comparison with original upstream code

1. The upstream uses /UUID/ for a subscription URL; our access token is
   independent of node UUID. Revocation does not require changing node keys.
2. The upstream can publish http://IP:PORT/UUID/auto; ours defaults to
   loopback and requires HTTPS for a declared external URL.
3. The upstream includes api.qrserver.com URLs containing private subscription
   addresses; our code does not use any remote QR service.
4. The upstream downloads some templates from a moving external repository
   with --no-check-certificate; our publisher downloads nothing and only
   consumes existing files.
5. Upstream Nginx supports a regex alias route on arbitrary path capture and
   autoindex; our new server has a fixed allowlist and no directory listing.

These restrictions are on the *new publisher only*. They do not retroactively
secure the upstream installer. The archived upstream script is NOT a safe
installation entrypoint.

## Limitations

- Client files must already be generated locally by a trusted process.
  The publisher does not create VLESS, HY2, TUIC or other node credentials.
- Correctness of the upstream client format is a separate issue. Test each
  subscription on the actual target client before any production release.
- User-Agent detection is best-effort, and individual endpoints are preferred
  when clients use unusual agents.
- The upstream `subscribe/clash` and `subscribe/clash2` files may use embedded
  proxy-provider URLs. Only known YAML lines ending in /proxies are rewritten.
  Other embedded URLs/complex templates require manual inspection.
- Tokenized URLs are bearer credentials. HTTPS protects transit but not against
  a compromised client device, reverse proxy logging or shared screenshots.
- Old private snapshots are retained for manual recovery. Ensure the private
  state directory is never exported and plan secure pruning separately.
- The local HTTP service runs in the foreground; no systemd service is created.
- Concurrency and interruption recovery, complete upstream installer auditing,
  OS/architecture integration and manual HTTPS publication still need testing.
- Mainline version reminders, manual safe updates and independent extensions
  remain required but will be re-reviewed against the upstream layout.

## Release gates

- [x] Fixed GPL-3.0 upstream baseline copied for Smart provenance and audit
- [x] No third-party conversion/QR API calls in local publisher
- [x] Independent random bearer token; rotation and Clash provider rewrite
- [x] Local-only server; strict allowlist and no URL logging
- [x] GitHub Actions offline unit tests and real sing-box core + HTTPS subscription runtime integration on disposable Debian 12/13 containers (see tests/integration/README.md)
- [ ] Failure injection, token changes during publication, lock tests
- [ ] Disposable full-VM installation with genuinely upstream-generated subscription files (the existing container tests use pinned authentic templates with synthetic node values)
- [x] Real Nginx reverse proxy test with disposable private CA, strict TLS hostname/CA validation
- [ ] Public-CA domain HTTPS and real-client subscription imports
- [ ] Security audit and safe migration of the upstream installer
- [ ] Smart version updater and independent optional extensions rebase

## 2026-11-15 Debian runtime verification

The isolated CI matrix has now actually started official sing-box v1.14.4,
proxied loopback HTTP traffic, served Smart local subscriptions using verified upstream templates
via Nginx HTTPS, checked token rotation and verified strict TLS CA behavior
on Debian 12 and 13. See [tests/integration/README.md](tests/integration/README.md).

The archived original upstream one-click installer was **not** executed, as it has
systemwide effects and insecure remote-download operations that require
a separate audit before full virtual-machine testing. This is an important
remaining production-release blocker.

## Do not deploy

All implementation/testing here is in the private GitHub repository only.
The owner explicitly chose to deploy manually after development completion.
No Germany VPS, SSH, firewall, BBR or network service was accessed/changed.
