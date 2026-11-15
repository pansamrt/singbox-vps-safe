# Deployment policy — manual only / 手动部署原则

> **Owner decision (2026-11-15):** development, review, and tests take place in this
> private GitHub repository. No developer, assistant, GitHub Action, or agent should
> connect to or modify the owner's Germany VPS to deploy this project. The owner
> personally decides when and where to deploy after development and acceptance.

## Boundaries / 权限边界

1. **No VPS deployment:** No SSH/scp to live servers; no commands run on the owner's
   servers; no remote service restart; no firewall/iptables/nftables, DNS,
   routing, WARP, or existing proxy modifications.
2. **Version reminders vs installation:** The owner may run an on-demand
   read-only official release check or view an update notice on opening the
   `sb` menu. A version notice never authorizes a download, restart or
   installation. Only the owner can manually invoke and confirm an update
   of the dedicated singbox-vps-safe core after choosing to deploy this project.
3. **No GitHub-based auto-deployment:** Do not add repository secrets holding VPS
   credentials, SSH private keys, access tokens, host names, or addresses. GitHub
   Actions must be limited to source checks, test fixtures and short-lived CI
   environments. There must be no production deployment job, webhook, updater
   or remote command channel.
4. **Manual release only:** Release a documented, immutable tag/commit after the
   acceptance criteria below have passed. Publish the changes, integrity hashes,
   known limitations and rollback steps; do not claim completion solely because
   GitHub Actions are green.
5. **No remote code execution:** Never recommend `curl | bash`, a moving default
   branch as a privileged installer, or `--insecure`/certificate-check bypasses.
6. **Existing services are out of scope:** The project must never silently stop,
   disable, replace, or hijack the original `sing-box` unit, Hysteria2, SSH,
   WARP, firewall, system DNS, or any other service.

## Development completion criteria / 开发完成标准

- [ ] Security review of the original script and every ported dependency/feature.
- [x] Isolated installer baseline with pinned upstream download hashes.
- [x] Basic VLESS-Reality / Hysteria2 / TUIC / AnyTLS **single-inbound**
      configuration generator, static checks and unit tests.
- [x] Draft versioned backup / explicit restore routines with simulated test cases.
- [ ] Complete, safe interactive `sb` management UX and validated multi-inbound use.
- [ ] Certificate acquisition, storage, renewal, failure handling and expiry alerts.
- [x] Draft explicit owner-confirmed core updater with stable-release check,
      official artifact SHA256 verification, configuration preflight,
      pre-update snapshot and attempted rollback after failure.
- [ ] Integration-test the core updater on disposable VMs, including
      download failure, config incompatibility, service restart failure,
      interruption, and preserved SSH/firewall/other services.
- [ ] Client configuration export without accidental secret disclosure.
- [ ] End-to-end Debian 12/13, supported Ubuntu, amd64 and arm64 checks.
- [ ] Disposable test-VM failure injection, port collisions (IPv4/IPv6),
      reboot behavior, service sandbox restrictions and uninstall testing.
- [ ] Release package, reproducible provenance, checksums and reviewed
      manual installation/rollback guide.
- [ ] Draft PRs reviewed and merged; no production claim while any critical
      acceptance step is incomplete.

## Owner-controlled deployment handoff / 交付内容

When acceptance criteria are met, prepare a self-contained handoff:

- Exact immutable Git tag and commit SHA.
- Supported operating systems and architectures.
- System dependencies and ports **without making network changes automatically**.
- SHA256 hashes of release artifacts and verification instructions.
- Fully local/manual preflight, install, status, backup, update, restore, uninstall.
- TLS, DNS and firewall prerequisites clearly labeled as owner actions.
- Known limitations, test report, safe rollback and recovery instructions.

**Repository completion is not permission to deploy.** The owner will
manually decide if and when to install. Do not interpret silence or a CI green
status as approval to access a VPS.
