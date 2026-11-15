# Phase 1 security baseline

**Status: experimental. Do not deploy to an existing production VPS.**

## Provenance

- Original project: https://github.com/yonggekkk/sing-box-yg
- Fixed source revision: `b13f9435c4792fccf04d4fbc73e24a1948abff1b` (2026-10-22)
- Original script preserved at `upstream/sb.sh` strictly as a review/reference copy.
- GPL-3.0 license preserved at `LICENSE`.
- Do not invoke `upstream/sb.sh` on your VPS. Its unsafe behaviors are intentionally **not** part of the new installer.

## What Phase 1 changes

The new `scripts/install.sh` is **not** a drop-in replacement for the original interactive menu. It supports a user-supplied sing-box JSON config and a separate dedicated `singbox-vps-safe` systemd unit.

- `--plan` is read-only; no package auto-install or network download.
- `--install` checks distro, architecture, required utilities, JSON configuration and basic ports; it refuses to overwrite an existing safe instance.
- The pinned core is sing-box 1.14.4, downloaded over HTTPS and checked against per-architecture SHA256 values obtained from the official GitHub release API on 2026-11-15.
- Stages the binary before installing; calls `sing-box check`; restores previous service/config/binary state on failures after mutation begins.
- Does **not** disable UFW/firewalld, flush iptables, manipulate DNS, restart WARP, stop Apache, modify SSH or install cron jobs.
- The dedicated service currently starts as root with a restricted capability bounding set. This is a temporary compatibility compromise, **not** a fully least-privilege service account.

## Known limitations / blockers

1. No VPS integration test has been performed. A successful syntax or CI test does not imply safe production operation.
2. The installer does not provision a configuration, cert, domain or subscription. Bring a reviewed `config.json` that passes `sing-box check`.
3. Port checking is a best-effort IPv4 bind test, not a comprehensive IPv6/systemd socket/UDP-sharing detector. Do not deploy until testing confirms all chosen ports.
4. Initial-install rollback is separate from the experimental snapshot/restore tool. The new root-only `scripts/state.py` supports versioned backups and manually confirmed restore of three owned files, but **no in-place upgrade command exists** and rollback needs disposable-VPS integration tests.
5. TLS secrets should be stored within the dedicated directory with appropriate permissions. The unit has `ProtectHome=true`, so references to `/root` will not work.
6. Proxy routing, TUN, reverse-proxy, TProxy, WARP, firewall configuration and existing shared service migration are out of scope.
7. Original functions such as managed certificates, Argo, subscription publication, GitLab tokens, Telegram alerts, port hopping and interactive menu are not yet safely ported.
8. Download checksums are pinned here; they must be updated by reviewed commits when changing release versions. SHA256 checks establish matching artifact bytes, not whether upstream itself is trustworthy.
9. Only Debian/Ubuntu x86_64/aarch64 are supported. Other systems fail safely.

## Manual validation gate (disposable VPS only)

1. Keep a separate out-of-band VPS console. Record existing firewall, SSH, services and routing **before** changes.
2. Review config and repository revision; run `bash scripts/install.sh --plan` without root.
3. Install missing dependencies manually if needed, then execute `sudo bash scripts/install.sh --install /secure/location/config.json`.
4. Validate `systemctl status singbox-vps-safe`, `journalctl -u singbox-vps-safe`, IPv4/IPv6 connectivity and proxy client behavior.
5. Recheck that SSH, iptables/nftables, DNS, WARP, unrelated systemd services and current Hysteria2 continue unchanged.
6. Test rollback by injecting a failure on a **disposable** VPS and confirming no unrelated resources were touched.
7. Do not merge or publish one-click production instructions until verification is documented.

## Next improvements

- Interactive `sb` management compatible with safe defaults.
- Validate versioned backup + explicit rollback on disposable VMs, then implement safe in-place upgrade.
- Service non-root feasibility by supported inbound type and certificate layout.
- Additional protocol presets and safe ACME workflow after separate dependency review.
- GitHub Actions static checks plus integration tests in disposable VMs.
\n## Snapshot and restore trust boundaries

- `scripts/state.py` requires root. It stores snapshots under `/var/lib/singbox-vps-safe/snapshots` with directory mode 0700, saved files 0600 (binary 0700).
- Every snapshot is checked against SHA256 manifest digests before use. This detects damage, but **does not authenticate a malicious root user** who can edit both payload and manifest.
- Recovery changes only the dedicated binary, JSON config and dedicated systemd unit. It does not change SSH, DNS, firewall, routing, other proxy services, cron or system package management.
- A pre-restore snapshot is created before replacing any files; failed start attempts to restore previous files and service state. This needs live VM failure-injection testing.
- The snapshot includes credentials. Never commit, export or share snapshot contents.
- In-place updates, ACME renewal, IPv6 conflict detection, and multi-inbound deployments remain out of scope.
