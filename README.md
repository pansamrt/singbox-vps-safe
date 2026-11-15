# singbox-vps-safe

**Experimental project — do not install on an existing production VPS.**

> **Deployment ownership:** This project is developed and tested in GitHub only.
> There is **no automatic deployment**, no agent access to the owner's VPS, and no
> remote installer execution by the project team. After a reviewed, immutable
> release is ready, the owner will choose and perform any installation manually.
> See [MANUAL_DEPLOYMENT_POLICY.md](MANUAL_DEPLOYMENT_POLICY.md).


A private, safety-oriented derivative of [yonggekkk/sing-box-yg](https://github.com/yonggekkk/sing-box-yg). Based on upstream commit [b13f9435](https://github.com/yonggekkk/sing-box-yg/commit/b13f9435c4792fccf04d4fbc73e24a1948abff1b), 2026-10-22.

## Current scope

- `upstream/sb.sh` — original installer archived **for review only**; never an installation entrypoint.
- `scripts/install.sh` — limited, explicitly invoked installer for Debian/Ubuntu amd64/arm64.
- `scripts/generate_config.py` — offline configuration generator for VLESS-Reality, Hysteria2, TUIC and AnyTLS.
- `scripts/sb.sh` — safe management interface: status, read-only plan, config generation, explicit first install.
- `SECURITY.md` — threat model and validation gates.
- `.github/workflows/security-checks.yml` — syntax, static security checks, unit tests and actual sing-box config checks.

**Not ported:** the original full `sb` feature set, automatic config migrations, certificate issuance, WARP, Argo, token publishing and port hopping. Generated configs contain only one inbound, not all four at once.

## Safe read-only inspection

```bash
bash scripts/sb.sh --status
bash scripts/sb.sh --plan
bash scripts/sb.sh --menu
```

`--plan` never downloads files or changes server settings. Avoid executing arbitrary scripts from GitHub main branches as root.

## Generate configs locally (review before use)

Python 3.10+ is required for the generator; it does not require root or network access.

Hysteria2 example:

```bash
python3 scripts/generate_config.py \
  --protocol hysteria2 --port 443 \
  --server-name vpn.example.org \
  --tls-cert /etc/singbox-vps-safe/fullchain.pem \
  --tls-key /etc/singbox-vps-safe/privkey.pem \
  --output hy2.json
```

TUIC or AnyTLS: replace `hysteria2` with `tuic` or `anytls` and select a free TCP/UDP port appropriate to the protocol. The listed TLS paths are **paths on your intended VPS**, not files the generator creates. Install a valid, trusted certificate/key in those locations before installing; do not use insecure client certificate verification.

Reality example — requires a locally available and trusted `sing-box` executable for official X25519 keypair generation:

```bash
python3 scripts/generate_config.py \
  --protocol vless-reality --port 8443 \
  --handshake-host www.example.org \
  --reality-binary /path/to/reviewed/sing-box \
  --output reality.json
```

The generator creates two **private** (0600) files: the server config (`hy2.json`) and client parameters (`hy2.json.client.json`). Never upload either to GitHub, print them in issue reports, or send them through unencrypted channels. Client params include credentials and must be protected as secrets.

## First install on a disposable VPS only

1. Ensure the selected config references valid local TLS certificates, and its port is not used by another service.
2. Run `bash scripts/install.sh --plan` and inspect the printed listening ports.
3. On a **fresh disposable VPS**, run `sudo bash scripts/install.sh --install /path/to/reviewed-config.json` and verify status/client connectivity.
4. Existing Hysteria2, SSH, firewall, DNS and other services must be unchanged. Do not use the existing Germany VPS as a first test target.

## Optional external VPS utilities (independent, no auto-install)

The `sb` menu includes an independent **Optional external VPS tools** submenu:

- [KejiLion](https://github.com/kejilion/sh): server management toolbox;
- [Actions-bbr-v3](https://github.com/byJoey/Actions-bbr-v3): custom kernel and TCP BBR tools.

These tools are **not installed, executed, or invoked as part of sing-box
installation**. The extension offers only source information, a read-only
local kernel status report, and explicitly downloaded, pinned, non-executable
source copies for manual review. BBRv3 kernel changes can break VPS boot and
SSH access. See [OPTIONAL_TOOLS.md](OPTIONAL_TOOLS.md) for safety/attribution.

```bash
bash scripts/sb.sh --extensions
bash scripts/sb.sh --extension-sources
bash scripts/sb.sh --extension-status
```

## Official sing-box update reminders and one-click manual updates

The safe `sb` menu checks the latest **stable** release using the official
[SagerNet/sing-box releases](https://github.com/SagerNet/sing-box/releases/latest)
API whenever the menu opens. It displays the installed version, current stable
version, release page and a release-note excerpt. Checking is **read-only**:
no update or download of a core is performed by this operation.

```bash
# Check available stable version, no root required and no changes
bash scripts/sb.sh --check-update

# After reviewing the official release notes, perform a manual core update
sudo bash scripts/sb.sh --update-core --confirm

# Or open the menu, select 9, and type UPDATE to approve
sudo bash scripts/sb.sh --menu
```

The updater is restricted to **the dedicated singbox-vps-safe service** and its
binary. It will not update the original `sing-box` service or other scripts.

Safety gates: root and explicit confirmation; known dedicated unit; active
service; official stable version; an exact architecture-matched official release;
SHA256 digest from GitHub's release metadata; staged binary version verification;
`sing-box check` against the current config; exclusive operation lock; a
versioned pre-update backup; a restart and follow-up health check. Restart failure
triggers an attempt to restore the previous snapshot.

**This is a convenience upgrade of the sing-box core only.** It does not
automatically update the install/management scripts, issue certificates, modify
firewall or DNS, or perform migrations across breaking core releases.

The updater uses GitHub over HTTPS for version information and downloads.
If the official digest is absent, download fails, the new binary breaks config,
or the dedicated service is not running, **it refuses to upgrade**. There are no
background checks, scheduled tasks, timers, auto-upgrades or remote VPS actions.

**Before real use:** Manual update and rollback still require controlled test-VM
failure-injection. For now, keep all PRs as drafts and deploy nowhere.

## Dedicated backup and rollback (experimental)

These commands operate **only** on `/opt/singbox-vps-safe/sing-box`,
`/etc/singbox-vps-safe/config.json`, and
`/etc/systemd/system/singbox-vps-safe.service`. They never edit unrelated
systemd units, existing sing-box instances, firewall rules or SSH settings.

```bash
sudo bash scripts/sb.sh --backup
sudo bash scripts/sb.sh --snapshots
sudo bash scripts/sb.sh --restore 20261115T120000Z-0123abcd --confirm
```

The snapshot ID in the third line is only an **example**. Choose an actual ID from
`--snapshots`. A snapshot contains sensitive configuration, so files and the
private snapshot directory are root-owned and permission-restricted.

Restore verifies SHA256 checksums, checks the chosen configuration using its
saved binary, creates an additional **pre-restore backup**, stops **only**
`singbox-vps-safe.service`, and updates its three owned files. If restart fails,
it attempts to restore the previous snapshot automatically.

**This recovery mechanism has automated unit tests only.** It still needs
failure-injection and connectivity tests on a disposable VPS. Do not use on
the existing live VPS until those gates are passed.

## GPL and attribution

This derivative remains under GPL-3.0; see `LICENSE`. Upstream authors are credited and upstream source is preserved for audit.

**Not production-ready.** See `SECURITY.md` for remaining issues and validation requirements.
