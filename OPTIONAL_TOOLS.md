# Optional external tools: KejiLion and Actions-bbr-v3

**Owner requirement (2026-11-15):** these are independent VPS utilities, not part of
the sing-box installation process. Neither project is installed or executed
automatically; no VPS is accessed or changed by project development.

## Source projects and pinned revisions

| Tool | Official repository | Fixed upstream commit | Exposure |
| --- | --- | --- | --- |
| KejiLion | https://github.com/kejilion/sh | `287306b593008d8592f6c2457d1ae833920f7373` | High: broad server management, services, firewall, Docker and networking |
| Actions-bbr-v3 | https://github.com/byJoey/Actions-bbr-v3 | `2d79899a06bef10e2f72ef0924abf6ebcb16d727` | Critical: custom kernel/boot, qdisc and sysctl changes |

The KejiLion repository says **Apache-2.0** and includes the license text.
The BBR project's `LICENSE` file (at the pinned revision) has only
`MIT 许可证`; it is **not** a complete standard MIT license grant. This
integration intentionally copies **none** of the upstream installer code.
Review actual terms before redistributing any copied material.

## What is integrated

Only an optional read-only source/status viewer and an explicit, pinned,
integrity-checked **source download for review**. These extensions have no
permissions to alter the sing-box service or host configuration.

```bash
# Inspect upstream repo identities and risks; offline
python3 scripts/extensions.py sources

# Read local Linux/kernel status, BBR and qdisc; offline; no changes
python3 scripts/extensions.py status

# Open the separate tools menu (does not install anything)
bash scripts/sb.sh --extensions

# Optional download ONLY for manual source inspection (never execute)
python3 scripts/extensions.py download kejilion ./kejilion-review.sh
python3 scripts/extensions.py download bbr-v3 ./bbr-v3-review.sh
```

The destination must be new. Download uses a pinned immutable commit and
verifies the source against the official Git blob SHA-1 from the GitHub tree.
It also prints the downloaded file's SHA256 for your record. The review copy is
written with permission **0600**, not executable, and never run by
`singbox-vps-safe`. Pinning and hashes establish provenance/integrity, not
the safety of the original installer.

There is no `curl | bash`, no download-then-run, no task scheduler, no cron
and no automatic kernel modifications.

## Important distinctions

- **BBRv3 is a kernel/network feature** and can require replacing the Linux
  kernel and rebooting. On cloud VPS systems, driver/boot incompatibility can
  make SSH inaccessible. Do not run its full installer without a separate
  system-level security review and a recoverable VPS console.
- **TCP BBR does not directly accelerate QUIC-over-UDP (Hysteria2)**.
  QUIC has its own congestion control. TCP-based inbound/outbound flows may
  respond differently, so this must be evaluated independently from sing-box.
- A VPS may already have a supported TCP BBR algorithm, making replacement
  with a custom Linux kernel unnecessary.
- The KejiLion tool is a separate **general system administration** project.
  Its scripts can change SSH, firewalls, network state and services depending
  on which features you select. Do not conflate its risk boundary with the
  narrowly scoped sing-box installer.
- This repository does not deploy or install either external project on the
  owner's VPS, and does not include an "execute third-party code" option.
  Future installation, if desired, is entirely independent, owner-driven,
  and requires a separate informed review.

## Acceptance before a release

- [x] Pinned upstream references and blob hashes.
- [x] Explicit optional menu, no automatic installation.
- [x] Read-only kernel/BBR status.
- [x] Reviewed-source download (non-executable, exclusive new destination).
- [ ] External upstream code audit, system-level test/restore plans and
      license review if integrating install or configuration operations later.
- [ ] Disposable test-VM verification of all selected extension workflows.
