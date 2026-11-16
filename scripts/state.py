#!/usr/bin/env python3
"""Owned-file snapshot and explicit restore for singbox-vps-safe only.

GPL-3.0-only. Does NOT touch firewall, SSH, DNS, other systemd units or ports.
Draft code: validate on disposable VPS before production use.
"""
import argparse
from contextlib import contextmanager
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import grp
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys

SNAPSHOT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
EXPECTED_NAMES = ("binary", "config", "unit")
MODES = {"binary": 0o755, "config": 0o640, "unit": 0o644}
SERVICE = "singbox-vps-safe.service"


@dataclass(frozen=True)
class Layout:
    binary: Path = Path("/opt/singbox-vps-safe/sing-box")
    config: Path = Path("/etc/singbox-vps-safe/config.json")
    unit: Path = Path("/etc/systemd/system/singbox-vps-safe.service")
    state: Path = Path("/var/lib/singbox-vps-safe")

    def paths(self) -> dict[str, Path]:
        return {key: getattr(self, key) for key in EXPECTED_NAMES}


class Systemd:
    def is_active(self) -> bool:
        return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0

    def stop(self) -> None:
        subprocess.run(["systemctl", "stop", SERVICE], check=True)

    def daemon_reload(self) -> None:
        subprocess.run(["systemctl", "daemon-reload"], check=True)

    def start(self) -> None:
        subprocess.run(["systemctl", "start", SERVICE], check=True)
        if not self.is_active():
            raise RuntimeError("Dedicated service not active after start")

    def check_config(self, binary: Path, config: Path) -> None:
        subprocess.run([str(binary), "check", "-c", str(config)], check=True, timeout=60)


def assert_regular(path: Path, secret: bool = False) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"Not a regular file (symlinks forbidden): {path}")
    if secret and (info.st_mode & 0o037):
        raise RuntimeError(f"Secret file is world-accessible or group-writable/executable: {path}")


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing directory symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeError(f"Unexpected snapshot directory owner/type: {path}")
    path.chmod(0o700)


def copy_no_follow(source: Path, target: Path, mode: int) -> str:
    """Read a regular source via O_NOFOLLOW; never follow a final path symlink."""
    assert_regular(source)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    in_fd = os.open(source, flags)
    digest = hashlib.sha256()
    created_target = False
    try:
        with os.fdopen(in_fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise RuntimeError("Source changed to non-regular file")
            out_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_NOFOLLOW", 0), 0o600)
            created_target = True
            with os.fdopen(out_fd, "wb") as out:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                out.flush()
                os.fsync(out.fileno())
    except BaseException:
        if created_target:
            target.unlink(missing_ok=True)
        raise
    target.chmod(mode)
    return digest.hexdigest()


def hash_regular(path: Path) -> str:
    assert_regular(path, secret=True)
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_root(layout: Layout) -> Path:
    ensure_private_directory(layout.state)
    root = layout.state / "snapshots"
    ensure_private_directory(root)
    return root


@contextmanager
def operation_lock(layout: Layout):
    """Exclusive lock for manual updates and snapshot/restore operations."""
    ensure_private_directory(layout.state)
    lock_path = layout.state / "operation.lock"
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError("Unsafe operation lock permissions/owner")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another backup, restore or core update is running") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def create_snapshot(layout: Layout, ctl: Systemd, reason: str = "manual") -> str:
    paths = layout.paths()
    for name, path in paths.items():
        assert_regular(path, secret=(name == "config"))
    root = snapshot_root(layout)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap_id = stamp + "-" + secrets.token_hex(4)
    stage = root / (".partial-" + snap_id)
    stage.mkdir(mode=0o700)
    try:
        files = {}
        for name, source in paths.items():
            digest = copy_no_follow(source, stage / name, 0o700 if name == "binary" else 0o600)
            files[name] = {"sha256": digest, "mode": MODES[name]}
        manifest = {"schema": 1, "id": snap_id, "reason": reason,
                    "active": ctl.is_active(), "files": files}
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        manifest_path.chmod(0o600)
        stage.rename(root / snap_id)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return snap_id


def load_snapshot(layout: Layout, snap_id: str) -> tuple[Path, dict]:
    if not SNAPSHOT_ID.fullmatch(snap_id):
        raise ValueError("Invalid snapshot id")
    root = snapshot_root(layout)
    source = root / snap_id
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Snapshot does not exist or is a symlink")
    assert_regular(source / "manifest.json", secret=True)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("id") != snap_id:
        raise ValueError("Invalid snapshot manifest")
    if type(manifest.get("active")) is not bool:
        raise ValueError("Invalid service state")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(EXPECTED_NAMES):
        raise ValueError("Unexpected snapshot file set")
    for name in EXPECTED_NAMES:
        item = files[name]
        if not isinstance(item, dict) or item.get("mode") != MODES[name]:
            raise ValueError("Unexpected file mode")
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("Invalid file digest")
        if hash_regular(source / name) != item["sha256"]:
            raise ValueError(f"Snapshot corruption detected for {name}")
    return source, manifest


def apply_files(layout: Layout, source: Path) -> None:
    # All modifications are limited to three dedicated files and their containing directories.
    paths = layout.paths()
    staged: list[tuple[Path, Path]] = []
    try:
        for name, target in paths.items():
            if target.is_symlink():
                raise RuntimeError(f"Refusing target symlink: {target}")
            if target.exists():
                assert_regular(target)
            if not target.parent.is_dir() or target.parent.is_symlink():
                raise RuntimeError(f"Unexpected target directory: {target.parent}")
            tmp = target.with_name("." + target.name + ".restore-" + secrets.token_hex(8))
            copy_no_follow(source / name, tmp, MODES[name])
            if name == "config" and target == Path("/etc/singbox-vps-safe/config.json"):
                # Preserve non-root service read access after rollback.
                os.chown(tmp, 0, grp.getgrnam("singbox-safe").gr_gid)
            staged.append((tmp, target))
        for tmp, target in staged:
            os.replace(tmp, target)
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def restore_snapshot(layout: Layout, ctl: Systemd, snap_id: str) -> str:
    source, manifest = load_snapshot(layout, snap_id)
    # Validate BEFORE touching the running service.
    ctl.check_config(source / "binary", source / "config")
    # A reversible pre-restore point is required and must complete first.
    old_id = create_snapshot(layout, ctl, reason="before-restore-" + snap_id)
    old_source, old_manifest = load_snapshot(layout, old_id)
    try:
        if ctl.is_active():
            ctl.stop()
        apply_files(layout, source)
        ctl.daemon_reload()
        if manifest["active"]:
            ctl.start()
    except BaseException:
        # Best effort restoration of the exact previous dedicated files.
        try:
            if ctl.is_active():
                ctl.stop()
            apply_files(layout, old_source)
            ctl.daemon_reload()
            if old_manifest["active"]:
                ctl.start()
        except BaseException as exc:
            raise RuntimeError("RESTORE AND AUTOMATIC RECOVERY FAILED; use VPS console and inspect dedicated service") from exc
        raise
    return old_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    cmd = p.add_mutually_exclusive_group(required=True)
    cmd.add_argument("--backup", action="store_true")
    cmd.add_argument("--list", action="store_true")
    cmd.add_argument("--restore", metavar="SNAPSHOT_ID")
    p.add_argument("--confirm", action="store_true",
                   help="required for --restore; stop and restore dedicated service only")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("State operations require root")
    os.umask(0o077)
    layout, ctl = Layout(), Systemd()
    try:
        if args.restore and not args.confirm:
            p.error("--restore requires --confirm")
        with operation_lock(layout):
            if args.backup:
                print("Snapshot created:", create_snapshot(layout, ctl))
            elif args.list:
                root = snapshot_root(layout)
                for item in sorted(root.iterdir()):
                    if SNAPSHOT_ID.fullmatch(item.name) and item.is_dir() and not item.is_symlink():
                        print(item.name)
            elif args.restore:
                undo_id = restore_snapshot(layout, ctl, args.restore)
                print("Restored dedicated service snapshot. Pre-restore backup:", undo_id)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
