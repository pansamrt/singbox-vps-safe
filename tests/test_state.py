"""Tests run only against temporary test files; never call real systemctl."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import state


class FakeSystemd:
    def __init__(self, active=True):
        self.active = active
        self.fail_next_start = False
        self.events = []

    def is_active(self):
        return self.active

    def stop(self):
        self.events.append("stop")
        self.active = False

    def daemon_reload(self):
        self.events.append("reload")

    def start(self):
        self.events.append("start")
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("simulated start failure")
        self.active = True

    def check_config(self, binary, config):
        self.events.append("check")
        if not binary.is_file() or not config.is_file():
            raise RuntimeError("invalid snapshot")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.layout = state.Layout(
            binary=root / "opt" / "sing-box",
            config=root / "etc" / "config.json",
            unit=root / "systemd" / "singbox-vps-safe.service",
            state=root / "var" / "backups"
        )
        for name, path in self.layout.paths().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode() + b"-VERSION-ONE")
            path.chmod(state.MODES[name])
        self.ctl = FakeSystemd()

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_integrity_and_permissions(self):
        sid = state.create_snapshot(self.layout, self.ctl)
        src, manifest = state.load_snapshot(self.layout, sid)
        self.assertTrue(manifest["active"])
        self.assertEqual(manifest["schema"], 1)
        for name in state.EXPECTED_NAMES:
            self.assertEqual((src / name).read_bytes(), name.encode() + b"-VERSION-ONE")
            self.assertEqual((src / name).stat().st_mode & 0o077, 0)
        self.assertEqual((src / "manifest.json").stat().st_mode & 0o077, 0)
        self.assertEqual(src.stat().st_mode & 0o077, 0)

    def test_corrupt_snapshot_cannot_be_restored(self):
        sid = state.create_snapshot(self.layout, self.ctl)
        (self.layout.state / "snapshots" / sid / "config").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            state.restore_snapshot(self.layout, self.ctl, sid)
        self.assertNotIn("stop", self.ctl.events)

    def test_restore_with_automatic_pre_restore_backup(self):
        sid = state.create_snapshot(self.layout, self.ctl)
        self.layout.config.write_bytes(b"config-VERSION-TWO")
        self.layout.config.chmod(0o600)
        undo = state.restore_snapshot(self.layout, self.ctl, sid)
        self.assertEqual(self.layout.config.read_bytes(), b"config-VERSION-ONE")
        self.assertEqual(self.ctl.events.count("stop"), 1)
        self.assertTrue(self.ctl.active)
        undo_src, _ = state.load_snapshot(self.layout, undo)
        self.assertEqual((undo_src / "config").read_bytes(), b"config-VERSION-TWO")

    def test_start_failure_restores_previous_files(self):
        sid = state.create_snapshot(self.layout, self.ctl)
        self.layout.config.write_bytes(b"config-VERSION-TWO")
        self.layout.config.chmod(0o600)
        self.ctl.fail_next_start = True
        with self.assertRaises(RuntimeError):
            state.restore_snapshot(self.layout, self.ctl, sid)
        self.assertEqual(self.layout.config.read_bytes(), b"config-VERSION-TWO")
        self.assertTrue(self.ctl.active)
        self.assertEqual(self.ctl.events.count("start"), 2)

    def test_target_symlink_refused(self):
        sid = state.create_snapshot(self.layout, self.ctl)
        self.layout.unit.unlink()
        self.layout.unit.symlink_to(self.layout.config)
        with self.assertRaises(RuntimeError):
            state.restore_snapshot(self.layout, self.ctl, sid)
        self.assertTrue(self.layout.unit.is_symlink())
        self.assertTrue(self.ctl.active)

    def test_snapshot_path_traversal_refused(self):
        with self.assertRaises(ValueError):
            state.load_snapshot(self.layout, "../escape")

    def test_group_readable_config_refused(self):
        self.layout.config.chmod(0o644)
        with self.assertRaises(RuntimeError):
            state.create_snapshot(self.layout, self.ctl)


if __name__ == "__main__":
    unittest.main()
