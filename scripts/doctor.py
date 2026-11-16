#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only installation integrity inspection. Never prints credentials.

Does not touch SSH, firewall, DNS, other services, or local subscription secrets.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

BINARY = Path("/opt/singbox-vps-safe/sing-box")
CONFIG = Path("/etc/singbox-vps-safe/config.json")
UNIT = Path("/etc/systemd/system/singbox-vps-safe.service")
TLS = CONFIG.parent / "managed-tls"
SERVICE = "singbox-vps-safe.service"
EXPECTED_EXEC = "ExecStart=/opt/singbox-vps-safe/sing-box run -c /etc/singbox-vps-safe/config.json"
TLS_RE = re.compile(r"^tls-[0-7]\.(crt|key)$")


def regular_with_mode(path: Path, max_mode: int, group_id: int | None = None) -> str | None:
    """Returns an error or None. Never follows symlinks."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return f"missing: {path}"
    if not stat.S_ISREG(info.st_mode):
        return f"not a regular file: {path}"
    if info.st_uid != 0:
        return f"not owned by root: {path}"
    if group_id is not None and info.st_gid != group_id:
        return f"wrong group: {path}"
    if info.st_mode & ~max_mode & 0o777:
        return f"permissions too broad: {path}"
    return None


def inspect_install() -> list[str]:
    errors: list[str] = []
    try:
        account = pwd.getpwnam("singbox-safe")
        group = grp.getgrnam("singbox-safe")
        if account.pw_uid == 0 or account.pw_gid != group.gr_gid:
            errors.append("Dedicated service account/group mismatch")
    except KeyError:
        errors.append("Dedicated service account/group missing")
        group = None

    for path, mode, gid in ((BINARY, 0o755, None), (UNIT, 0o644, None),
                            (CONFIG, 0o640, group.gr_gid if group else None)):
        error = regular_with_mode(path, mode, gid)
        if error:
            errors.append(error)

    for directory, mode, gid in (
        (BINARY.parent, 0o755, None),
        (CONFIG.parent, 0o750, group.gr_gid if group else None),
    ):
        try:
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or
                    info.st_mode & ~mode & 0o777 or
                    (gid is not None and info.st_gid != gid)):
                errors.append(f"unsafe managed directory: {directory}")
        except OSError:
            errors.append(f"missing managed directory: {directory}")

    if not regular_with_mode(UNIT, 0o644):
        try:
            unit = UNIT.read_text(encoding="utf8")
            for line in (EXPECTED_EXEC, "User=singbox-safe", "Group=singbox-safe",
                         "NoNewPrivileges=true", "ProtectSystem=strict"):
                if line not in unit.splitlines():
                    errors.append(f"Dedicated service unit missing required setting: {line.split('=')[0]}")
        except (OSError, UnicodeError):
            errors.append("Cannot inspect managed unit")

    if TLS.exists() or TLS.is_symlink():
        try:
            info = TLS.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or
                    (group is not None and info.st_gid != group.gr_gid) or
                    info.st_mode & ~0o750 & 0o777):
                errors.append("Unsafe managed TLS directory")
            else:
                for cert in TLS.iterdir():
                    if not TLS_RE.fullmatch(cert.name):
                        errors.append("Unknown file in managed TLS directory")
                        continue
                    error = regular_with_mode(cert, 0o640, group.gr_gid if group else None)
                    if error:
                        errors.append(error)
        except OSError:
            errors.append("Unable to inspect managed TLS directory")

    # The check reads the installed config, but never prints its secrets.
    if not regular_with_mode(CONFIG, 0o640, group.gr_gid if group else None):
        try:
            config = json.loads(CONFIG.read_text(encoding="utf8"))
            if not isinstance(config, dict) or not isinstance(config.get("inbounds"), list):
                errors.append("Invalid installed JSON config")
        except (OSError, ValueError, UnicodeError):
            errors.append("Cannot parse installed JSON config")

    return errors


def check_native() -> str | None:
    try:
        p = subprocess.run([str(BINARY), "check", "-c", str(CONFIG)],
                           capture_output=True, text=True, check=False, timeout=25)
        if p.returncode:
            return "Native sing-box configuration validation failed"
    except (OSError, subprocess.TimeoutExpired):
        return "Unable to execute native sing-box configuration check"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit nonzero on any integrity issue")
    args = parser.parse_args()
    if not UNIT.exists() and not UNIT.is_symlink():
        print("NOT INSTALLED: dedicated singbox-vps-safe unit absent; no changes made.")
        return 1 if args.strict else 0
    if os.geteuid() != 0:
        print("Root is required to inspect private installed configuration, but this check is read-only.",
              file=sys.stderr)
        return 2
    problems = inspect_install()
    if not problems:
        native = check_native()
        if native:
            problems.append(native)
    if problems:
        for issue in problems:
            print("FAIL:", issue, file=sys.stderr)
        return 1
    print("PASS: dedicated service file ownership, modes, unit constraints and native config")
    print("No secrets were printed. No system settings were changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
