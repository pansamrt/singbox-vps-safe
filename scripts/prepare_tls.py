#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Copy reviewed TLS input files into owner-controlled private staging.

Used only after native sing-box check succeeds and before system mutations.
Never changes existing certificate sources or system CA/ACME settings.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

MAX_CERT = 2 * 1024 * 1024
MAX_CONFIG = 2 * 1024 * 1024
TARGET = "/etc/singbox-vps-safe/managed-tls"


def file_bytes(path: Path, secret=False) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("TLS files must be absolute regular paths without symlinks")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CERT:
        raise ValueError("Invalid TLS file type or size")
    if secret and info.st_mode & 0o007:
        raise ValueError("TLS private key must not be world-accessible")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as inp:
        if not stat.S_ISREG(os.fstat(inp.fileno()).st_mode):
            raise ValueError("TLS file changed during read")
        data = inp.read(MAX_CERT + 1)
    if not data or len(data) > MAX_CERT:
        raise ValueError("Invalid TLS file size")
    return data


def write_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def no_duplicate_keys(pairs):
    out = {}
    for name, value in pairs:
        if name in out:
            raise ValueError("Duplicate JSON key: " + name)
        out[name] = value
    return out


def stage(config_path: Path, workspace: Path) -> int:
    info = config_path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG:
        raise ValueError("Invalid config")
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("Private staging workspace must exist")
    config = json.loads(config_path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list) or not 1 <= len(inbounds) <= 8:
        raise ValueError("Need 1..8 inbounds")
    stage_dir = workspace / "managed-tls"
    pairs = []
    for index, inbound in enumerate(inbounds):
        if not isinstance(inbound, dict):
            raise ValueError("Invalid inbound")
        tls = inbound.get("tls", {})
        if not isinstance(tls, dict):
            raise ValueError("Invalid TLS section")
        cert = tls.get("certificate_path")
        key = tls.get("key_path")
        if cert is None and key is None:
            continue
        if not isinstance(cert, str) or not isinstance(key, str):
            raise ValueError("Both certificate_path and key_path required")
        if cert == key:
            raise ValueError("TLS certificate and private key cannot be the same file")
        cert_content = file_bytes(Path(cert))
        key_content = file_bytes(Path(key), secret=True)
        pairs.append((index, cert_content, key_content))
        tls["certificate_path"] = TARGET + "/tls-" + str(index) + ".crt"
        tls["key_path"] = TARGET + "/tls-" + str(index) + ".key"
    if pairs:
        stage_dir.mkdir(mode=0o700)
        for index, cert, key in pairs:
            write_exclusive(stage_dir / ("tls-" + str(index) + ".crt"), cert)
            write_exclusive(stage_dir / ("tls-" + str(index) + ".key"), key)
    rewritten = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode()
    write_exclusive(workspace / "install-config.json", rewritten)
    return len(pairs)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    args = p.parse_args(argv)
    os.umask(0o077)
    try:
        count = stage(args.config, args.workspace)
        print("Validated and privately staged TLS certificate/key pairs:", count)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("ERROR: TLS preparation refused:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
