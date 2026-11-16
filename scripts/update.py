#!/usr/bin/env python3
"""Read-only stable release notification and owner-confirmed core update.

GPL-3.0-only. This upgrades ONLY the dedicated singbox-vps-safe binary.
No auto-install, cron, timers, remote deployment, firewall, DNS or SSH changes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import state

API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
VERS_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA_RE = re.compile(r"^sha256:([a-fA-F0-9]{64})$")
MAX_API_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_BINARY_BYTES = 100 * 1024 * 1024
TIMEOUT_SECONDS = 12


def version_tuple(version: str) -> tuple[int, int, int]:
    match = VERS_RE.fullmatch(version.strip())
    if not match:
        raise ValueError("Unsupported release version: " + version)
    return tuple(int(x) for x in match.groups())


def architecture() -> str:
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "amd64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    raise ValueError("Unsupported architecture: " + arch)


def fetch_json(url: str = API) -> dict:
    req = Request(url, headers={"Accept": "application/vnd.github+json",
                                "User-Agent": "singbox-vps-safe-release-check/1"})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        data = response.read(MAX_API_BYTES + 1)
    if len(data) > MAX_API_BYTES:
        raise ValueError("Release metadata exceeds safe size limit")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Unexpected release API response")
    return result


def parse_release(payload: dict, arch: str) -> dict:
    """Allow only official stable semver and exact official release artifact."""
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("No stable version tag returned")
    version_tuple(tag)
    if payload.get("prerelease", False) or payload.get("draft", False):
        raise ValueError("Refusing prerelease or draft release")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Release assets missing")
    filename = f"sing-box-{tag[1:]}-linux-{arch}.tar.gz"
    candidates = [a for a in assets if isinstance(a, dict) and a.get("name") == filename]
    if len(candidates) != 1:
        raise ValueError("Expected a single official architecture-matched tarball")
    asset = candidates[0]
    digest = asset.get("digest", "")
    match = SHA_RE.fullmatch(digest if isinstance(digest, str) else "")
    if not match:
        raise ValueError("No valid official release SHA256 digest; refusing update")
    length = asset.get("size")
    if type(length) is not int or not 0 < length <= MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid release asset size")
    expected_url = (f"https://github.com/SagerNet/sing-box/releases/download/"
                    f"{tag}/{filename}")
    if asset.get("browser_download_url") != expected_url:
        raise ValueError("Unexpected release download address")
    notes = payload.get("body") if isinstance(payload.get("body"), str) else ""
    return {"tag": tag, "version": tag[1:], "sha256": match.group(1).lower(),
            "size": length, "url": expected_url,
            "notes": notes[:1800],
            "page": f"https://github.com/SagerNet/sing-box/releases/tag/{tag}"}


def installed_version(binary: Path) -> str | None:
    if not binary.exists():
        return None
    state.assert_regular(binary)
    p = subprocess.run([str(binary), "version"], check=True, capture_output=True,
                       text=True, timeout=15)
    match = re.search(r"^sing-box version (\d+\.\d+\.\d+)$", p.stdout, re.M)
    if not match:
        raise ValueError("Cannot determine installed sing-box version")
    return match.group(1)


def release_status(layout: state.Layout, release: dict) -> tuple[str | None, bool]:
    installed = installed_version(layout.binary)
    newer = installed is None or version_tuple(release["version"]) > version_tuple(installed)
    print(f"Installed sing-box: {installed or 'not installed'}")
    print(f"Official stable release: {release['version']}")
    print(f"Release page: {release['page']}")
    if installed is None:
        print("This is NOT an installer; update is unavailable without a dedicated installation.")
    elif newer:
        print("UPDATE AVAILABLE. Manual confirmation is required to install.")
    else:
        print("No newer stable release detected.")
    if release["notes"]:
        print("\nRelease notes excerpt (official):\n" + release["notes"])
    return installed, newer


def download_verified(release: dict, folder: Path) -> Path:
    req = Request(release["url"], headers={"User-Agent": "singbox-vps-safe-update/1"})
    archive = folder / "release.tar.gz"
    digest = hashlib.sha256()
    total = 0
    with urlopen(req, timeout=TIMEOUT_SECONDS) as response, archive.open("xb") as dst:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Release archive exceeds maximum size")
            dst.write(chunk)
            digest.update(chunk)
    if total != release["size"]:
        raise ValueError("Download size differs from official release metadata")
    if digest.hexdigest() != release["sha256"]:
        raise ValueError("SHA256 verification failed; no installation performed")
    expected_member = f"sing-box-{release['version']}-linux-{architecture()}/sing-box"
    target = folder / "sing-box.new"
    with tarfile.open(archive, mode="r:gz") as tar:
        matches = [item for item in tar if item.name == expected_member]
        if len(matches) != 1 or not matches[0].isfile() or not 0 < matches[0].size <= MAX_BINARY_BYTES:
            raise ValueError("Expected one regular sing-box binary in official release")
        source = tar.extractfile(matches[0])
        if source is None:
            raise ValueError("Could not read sing-box binary from archive")
        with source, target.open("xb") as dst:
            shutil.copyfileobj(source, dst, length=1024 * 1024)
    target.chmod(0o755)
    return target


def require_owned_unit(layout: state.Layout) -> None:
    for name, path in layout.paths().items():
        state.assert_regular(path, secret=name == "config")
        if path.parent.is_symlink():
            raise ValueError(f"Unexpected symlink parent: {path.parent}")
    contents = layout.unit.read_text(encoding="utf-8")
    required = ("ExecStart=/opt/singbox-vps-safe/sing-box run -c "
                "/etc/singbox-vps-safe/config.json")
    if required not in contents:
        raise ValueError("Unit is not the expected dedicated singbox-vps-safe unit")


def update_core(layout: state.Layout, ctl: state.Systemd, release: dict) -> str | None:
    if os.geteuid() != 0:
        raise PermissionError("Manual update requires root")
    require_owned_unit(layout)
    if not ctl.is_active():
        raise RuntimeError("Dedicated service is not active; refusing upgrade")
    old_version = installed_version(layout.binary)
    if old_version is None:
        raise RuntimeError("No dedicated sing-box binary is installed")
    if version_tuple(release["version"]) <= version_tuple(old_version):
        print("Already current; no change performed.")
        return None

    # All temporary files and the atomic replacement are on the same filesystem.
    with tempfile.TemporaryDirectory(prefix=".singbox-upgrade-", dir=layout.binary.parent) as work:
        stage = download_verified(release, Path(work))
        new_version = installed_version(stage)
        if new_version != release["version"]:
            raise ValueError("New binary does not report expected version")
        ctl.check_config(stage, layout.config)

        # Shared lock prevents overlapping updates, snapshots and restores.
        with state.operation_lock(layout):
            require_owned_unit(layout)
            current = installed_version(layout.binary)
            if current != old_version or not ctl.is_active():
                raise RuntimeError("Installed version/service changed during download; retry")

            backup_id = state.create_snapshot(layout, ctl, reason="before-core-update-" + release["version"])
            print("Pre-update backup:", backup_id)
            interrupted = False
            replaced = False
            try:
                ctl.stop()
                # os.replace() is atomic within the target filesystem.
                os.replace(stage, layout.binary)
                replaced = True
                ctl.start()
                time.sleep(2)
                if not ctl.is_active():
                    raise RuntimeError("New service stopped after startup")
            except BaseException:
                interrupted = True
                print("Update failed/interrupted. Attempting automatic rollback ...", file=sys.stderr)
                try:
                    state.restore_snapshot(layout, ctl, backup_id)
                except BaseException as rollback_err:
                    raise RuntimeError(
                        f"CRITICAL: update rollback failed; inspect dedicated service via VPS console; "
                        f"snapshot={backup_id}"
                    ) from rollback_err
                raise
            finally:
                if interrupted and not replaced:
                    print("No new binary was committed; previous service restored if possible.",
                          file=sys.stderr)
            print("Update successful:", old_version, "->", release["version"])
            print("Rollback snapshot:", backup_id)
            return backup_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="read-only official release check")
    group.add_argument("--update", action="store_true", help="explicit manual core update")
    p.add_argument("--confirm", action="store_true", help="required with --update")
    args = p.parse_args()
    if args.update and not args.confirm:
        p.error("--update requires --confirm; no unattended update")
    os.umask(0o077)
    try:
        release = parse_release(fetch_json(), architecture())
        layout = state.Layout()
        installed, newer = release_status(layout, release)
        if args.check:
            return 0
        if not installed or not newer:
            return 0
        ctl = state.Systemd()
        update_core(layout, ctl, release)
        return 0
    except (OSError, ValueError, RuntimeError, PermissionError, subprocess.SubprocessError,
            json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"Update check/action failed safely: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
