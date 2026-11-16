#!/usr/bin/env python3
"""Optional, independently managed VPS toolbox references (no execution).

GPL-3.0-only. This module never installs, executes, sources or pipes any
downloaded third-party code into a shell. All actions are local to the
invoking user's files. Download requires an explicit output path.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Immutable upstream commits and Git object blob SHA-1 values taken from
# the official GitHub repository trees on 2026-11-15.
# Git's blob hash verifies pinned content, not trustworthiness of that content.
TOOLS = {
    "kejilion": {
        "project": "kejilion/sh",
        "commit": "287306b593008d8592f6c2457d1ae833920f7373",
        "path": "kejilion.sh",
        "blob_sha1": "2a4ee3d67d536dbcc83d4a68e03326dd3250679b",
        "size": 1288326,
        "license": "Apache-2.0 (upstream README and LICENSE)",
        "risk": "High: root-level system management, firewall, network, Docker and disk changes.",
    },
    "bbr-v3": {
        "project": "byJoey/Actions-bbr-v3",
        "commit": "2d79899a06bef10e2f72ef0924abf6ebcb16d727",
        "path": "install.sh",
        "blob_sha1": "b71e927c238e6a5dedaa962a5642b30d05d230ca",
        "size": 67362,
        "license": "Upstream LICENSE contains only a short MIT label; verify terms before redistribution",
        "risk": "Critical: kernel replacement and reboot can permanently interrupt VPS access.",
    },
}


class NoRedirects(HTTPRedirectHandler):
    """Never accept a redirect away from the immutable approved raw URL."""

    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("Unexpected third-party download redirect; refusing")


def raw_url(tool: str) -> str:
    item = TOOLS[tool]
    owner, repository = item["project"].split("/")
    return (f"https://raw.githubusercontent.com/{owner}/{repository}/"
            f"{item['commit']}/{item['path']}")


def validate_blob(data: bytes, expected_git_blob: str, expected_size: int) -> str:
    if len(data) != expected_size:
        raise ValueError("Upstream blob length mismatch; file not saved")
    git_blob_sha1 = hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()
    if git_blob_sha1 != expected_git_blob:
        raise ValueError("Pinned Git blob hash mismatch; file not saved")
    return hashlib.sha256(data).hexdigest()


def save_private(destination: Path, data: bytes) -> None:
    # Save as non-executable 0600; caller has to review and independently decide
    # whether to run it, outside of singbox-vps-safe.
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Output parent must be an existing non-symlink directory")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    created = True
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def download_review_copy(tool: str, destination: Path) -> tuple[str, str]:
    spec = TOOLS[tool]
    url = raw_url(tool)
    request = Request(url, headers={"User-Agent": "singbox-vps-safe-review/1"})
    opener = build_opener(NoRedirects())
    with opener.open(request, timeout=18) as response:
        data = response.read(spec["size"] + 1)
    sha256 = validate_blob(data, spec["blob_sha1"], spec["size"])
    save_private(destination, data)
    return url, sha256


def read_only_sysctl(name: str) -> str:
    executable = shutil.which("sysctl")
    if not executable:
        return "unavailable"
    try:
        out = subprocess.run([executable, "-n", name], capture_output=True,
                             text=True, check=False, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return out.stdout.strip()[:180] if out.returncode == 0 else "unavailable"


def show_status() -> None:
    print("Local kernel:", platform.release())
    print("System architecture:", platform.machine())
    print("TCP congestion control:", read_only_sysctl("net.ipv4.tcp_congestion_control"))
    print("Available TCP controls:", read_only_sysctl("net.ipv4.tcp_available_congestion_control"))
    print("Queue discipline:", read_only_sysctl("net.core.default_qdisc"))
    print("KejiLion 'k' command in PATH:", shutil.which("k") or "not found")
    print("No software installation or network configuration changes were performed.")
    print("Note: TCP BBR is not a direct QUIC/UDP Hysteria2 acceleration switch.")


def show_sources() -> None:
    for name, info in TOOLS.items():
        print(f"\n{name}: https://github.com/{info['project']}")
        print("Pinned source commit:", info["commit"])
        print("Pinned script:", info["path"])
        print("License:", info["license"])
        print("Risk:", info["risk"])
        print("Source review URL:", raw_url(name))
    print("\nNOT INSTALLED: Third-party installers are never run by this tool.")
    print("Raw-script review downloads are optional and non-executable (mode 0600).")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("sources", help="show the fixed upstream sources and risks, offline")
    subs.add_parser("status", help="show read-only local kernel and tool status, offline")
    get = subs.add_parser("download", help="download pinned source code for manual audit; never execute it")
    get.add_argument("tool", choices=sorted(TOOLS))
    get.add_argument("output", help="new review-file destination; must not exist")
    args = parser.parse_args(argv)
    try:
        if args.action == "sources":
            show_sources()
        elif args.action == "status":
            show_status()
        elif args.action == "download":
            os.umask(0o077)
            url, sha256 = download_review_copy(args.tool, Path(args.output))
            print("Verified review copy saved (mode 0600, not executable):", args.output)
            print("Pinned source:", url)
            print("SHA256 of reviewed download:", sha256)
            print("No script was executed. Review source before any independent manual use.")
    except (OSError, ValueError) as exc:
        print("Refused:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
