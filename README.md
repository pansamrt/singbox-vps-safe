# singbox-vps-safe

A privately maintained, safety-first sing-box VPS installer project.

> **Status: Under development — do not deploy to production VPS.**
> The `main` branch intentionally contains project metadata only. Installation code is reviewed on a separate development branch before release.

## Origin and license

Based on [yonggekkk/sing-box-yg](https://github.com/yonggekkk/sing-box-yg), upstream baseline commit [b13f9435c4792fccf04d4fbc73e24a1948abff1b](https://github.com/yonggekkk/sing-box-yg/commit/b13f9435c4792fccf04d4fbc73e24a1948abff1b) (2026-10-22). Derivative material is distributed under GPL-3.0. Original authorship, notices, and the complete license are preserved.

## Design constraints

- Never disable SSH, firewall, unrelated services, or other proxy instances as a side effect of installation.
- No remote-code execution through `curl | bash`, process substitution, or unaudited binary downloads.
- Pin and verify releases; validate configurations before restart.
- Keep versioned backups and support rollback.
- No automatic updates, no secrets in source control.
- Stage on a clean disposable Debian/Ubuntu VPS before any production migration.

No deployment has been performed on any VPS.
