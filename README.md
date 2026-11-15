# singbox-vps-safe

**Experimental project — do not install on an existing production VPS.**

A private, safety-oriented derivative of [yonggekkk/sing-box-yg](https://github.com/yonggekkk/sing-box-yg). Based on upstream commit [b13f9435](https://github.com/yonggekkk/sing-box-yg/commit/b13f9435c4792fccf04d4fbc73e24a1948abff1b), 2026-10-22.

## Status

Phase 1 review branch contains:
- `upstream/sb.sh`: original installer archived as an **untrusted audit reference**, never the installation entrypoint;
- `scripts/install.sh`: a limited, opt-in installer for Debian/Ubuntu (amd64/arm64), using a user-provided config and a dedicated unit;
- `SECURITY.md`: threat model, known gaps, manual test gates;
- `.github/workflows/security-checks.yml`: static checks.

The old interactive `sb` menu, certificate automation, WARP, Argo, subscription publication, and upgrades are **not yet ported safely**. The current installer does **not** automatically create protocol configurations.

## Usage in a disposable test VPS only

Read [SECURITY.md](SECURITY.md) and inspect the full installer source before running commands.

```bash
bash scripts/install.sh --plan
sudo bash scripts/install.sh --install /path/to/reviewed-config.json
```

Never run `upstream/sb.sh` directly. Do not use `curl | bash` or run an unreviewed moving `main` branch as root.

## License and attribution

The original project and this derivative are licensed under GPL-3.0. See [LICENSE](LICENSE). Original work is credited to the upstream authors; modifications are tracked in this repository. The copied script is stored under `upstream/` for source review only.

## Non-goals

No modification of SSH, existing firewall, DNS, system routes, existing Hysteria2, WARP or unrelated services. No production deployment before disposable VPS testing.
