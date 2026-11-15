# Debian runtime and HTTPS subscription integration

**2026-11-15: PASS on disposable Debian 12 and Debian 13 amd64 Docker containers.**
Evidence: https://github.com/pansamrt/singbox-vps-safe/actions/runs/43733183655

## Real components exercised

- Official native sing-box v1.14.4, binary downloaded and verified against
  SHA256 699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968.
- fscarmen/client_template official Clash, Clash2 and sing-box templates pinned
  to upstream commit 8b97614b782307d00a74de55d93a26c0d3a2286c.
  Each downloaded template is checked against its Git blob SHA-1. Synthetic
  test credentials and node values are substituted; no live credentials used.
- Real Debian-distribution Nginx as HTTPS reverse proxy, with short-lived
  local test CA and strict certificate verification.
- Actual local_subscriptions.py publishing, live HTTP serving, token rotation
  and secure failure behavior.

## Passed integration checks

- [x] Native sing-box version check, configuration validation and process start.
- [x] Real SOCKS5 handshake, CONNECT and HTTP traffic forwarding on loopback.
- [x] Real fscarmen Clash template placeholder expansion; sing-box JSON parsing.
- [x] V2rayN, Clash/Mihomo, Shadowrocket and sing-box subscription endpoints.
- [x] Independently generated token, restrictive directory/file permissions.
- [x] Clash provider URLs repointed to private HTTPS subscription addresses.
- [x] Client-sensitive auto and auto2 endpoint selection.
- [x] HTTP no-store, invalid token, unknown endpoint, path traversal protection.
- [x] Token rotation revokes old HTTP and HTTPS URLs, updates provider references.
- [x] Failed source symlink publish does not replace live published generation.
- [x] Real HTTPS Nginx reverse proxy, TLS CA trust and hostname verification.
- [x] Untrusted certificates are rejected (no insecure TLS bypass).
- [x] Processes terminate and temporary state is discarded.

## Isolation

GitHub Actions builds temporary Debian 12 and Debian 13 containers. The test
runtime uses NO external network, ALL Linux capabilities dropped, non-root
UID/GID 65534, no-new-privileges, read-only filesystem and read-only mounts
for the repository, pinned sing-box core and upstream templates. Temporary
files live in an ephemeral tmpfs. No owner VPS was accessed or modified.

## Critical remaining work

**A complete fscarmen one-click installer has NOT been run.**
The archived upstream script has unaudited system/firewall modifications,
remote shell execution and some downloads that bypass TLS verification.
Executing that installer directly, even on a GitHub runner, would defeat the
project's safety goals. Its complete installation/uninstallation flow needs
prior review, a safely adapted version, and isolation in a fully disposable
VM with independent console recovery.

- [ ] Safely adapt and test actual fscarmen full installer in a disposable VM.
- [ ] Test real subscription outputs from a completed fscarmen installation;
      this stage used genuine upstream templates with simulated node values.
- [ ] Verify end-to-end protocol handshakes for VLESS-Reality, Hysteria2,
      TUIC and AnyTLS, including UDP and IPv6.
- [ ] Test full systemd lifecycle, reboot, uninstall and failed update recovery.
- [ ] Test externally trusted certificate issuance/renewal with manual DNS;
      current TLS tests use only a disposable test CA.
- [ ] Extend to ARM64 where supported.

Result: genuine core runtime + local subscriptions + TLS proxy PASS on Debian
12 and 13; **full installer acceptance not yet complete**. No production release.
