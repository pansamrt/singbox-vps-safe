"""Offline tests: version notices, immutable asset validation, checksum and update safety."""
from contextlib import nullcontext
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update
import state


def release_payload(version="1.14.5", arch="amd64", digest=None):
    name = f"sing-box-{version}-linux-{arch}.tar.gz"
    return {
        "tag_name": "v" + version, "prerelease": False, "draft": False,
        "body": "Security fixes and changes\n" * 100,
        "assets": [{
            "name": name, "size": 200,
            "digest": "sha256:" + (digest or "a" * 64),
            "browser_download_url": f"https://github.com/SagerNet/sing-box/releases/download/v{version}/{name}",
        }]
    }


class ReleaseTests(unittest.TestCase):
    def test_semver_comparison(self):
        self.assertTrue(update.version_tuple("1.15.0") > update.version_tuple("1.14.9"))
        self.assertEqual(update.version_tuple("v1.14.4"), (1, 14, 4))
        for bad in ("1.14.4-rc.1", "v1.14", "release/v1.14.4", "../../etc"):
            with self.assertRaises(ValueError):
                update.version_tuple(bad)

    def test_parse_official_asset_and_changelog(self):
        r = update.parse_release(release_payload(), "amd64")
        self.assertEqual(r["version"], "1.14.5")
        self.assertEqual(r["sha256"], "a" * 64)
        self.assertTrue(r["url"].startswith("https://github.com/SagerNet/"))
        self.assertLessEqual(len(r["notes"]), 1800)

    def test_refuse_prerelease_and_missing_digest(self):
        p = release_payload()
        p["prerelease"] = True
        with self.assertRaises(ValueError):
            update.parse_release(p, "amd64")
        p["prerelease"] = False
        p["assets"][0]["digest"] = None
        with self.assertRaises(ValueError):
            update.parse_release(p, "amd64")

    def test_refuse_unexpected_download_source(self):
        p = release_payload()
        p["assets"][0]["browser_download_url"] = "https://example.net/untrusted.tar.gz"
        with self.assertRaises(ValueError):
            update.parse_release(p, "amd64")

    def test_refuse_missing_or_duplicate_asset(self):
        p = release_payload()
        with self.assertRaises(ValueError):
            update.parse_release(p, "arm64")
        p["assets"].append(dict(p["assets"][0]))
        with self.assertRaises(ValueError):
            update.parse_release(p, "amd64")

    def test_release_metadata_size_limit(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b"x" * size
        with patch.object(update, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(ValueError):
                update.fetch_json()


class DownloadTests(unittest.TestCase):
    def fixture(self):
        blob = b"#!/bin/sh\necho sing-box version 1.14.5\n"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as t:
            info = tarfile.TarInfo("sing-box-1.14.5-linux-amd64/sing-box")
            info.size = len(blob)
            t.addfile(info, io.BytesIO(blob))
        data = archive.getvalue()
        info = release_payload()
        info["assets"][0]["size"] = len(data)
        info["assets"][0]["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
        return data, update.parse_release(info, "amd64")

    def test_download_verified_to_temp_only(self):
        data, release = self.fixture()
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(update, "urlopen", return_value=Response(data)):
                with patch.object(update, "architecture", return_value="amd64"):
                    path = update.download_verified(release, Path(d))
            self.assertEqual(path.read_bytes().splitlines()[0], b"#!/bin/sh")
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_digest_mismatch_refuses_before_binary(self):
        data, release = self.fixture()
        release["sha256"] = "0" * 64
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(update, "urlopen", return_value=Response(data)):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    update.download_verified(release, Path(d))
            self.assertFalse((Path(d) / "sing-box.new").exists())


class FakeCtl:
    def __init__(self):
        self.active = True
        self.start_fail = False
        self.events = []

    def is_active(self): return self.active
    def stop(self):
        self.events.append("stop")
        self.active = False
    def start(self):
        self.events.append("start")
        if self.start_fail:
            raise RuntimeError("simulated failing start")
        self.active = True
    def check_config(self, binary, config):
        self.events.append("check")


class UpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.layout = state.Layout(
            binary=root / "opt" / "sing-box", config=root / "etc" / "config.json",
            unit=root / "systemd" / "singbox-vps-safe.service", state=root / "snapshots"
        )
        for file in self.layout.paths().values():
            file.parent.mkdir(parents=True, exist_ok=True)
        self.layout.binary.write_text("#!/bin/sh\necho sing-box version 1.14.4\n")
        self.layout.binary.chmod(0o755)
        self.layout.config.write_text('{"inbounds": [], "outbounds": []}')
        self.layout.config.chmod(0o600)
        self.layout.unit.write_text(
            "[Service]\nExecStart=/opt/singbox-vps-safe/sing-box run -c "
            "/etc/singbox-vps-safe/config.json\n"
        )
        self.layout.unit.chmod(0o644)
        self.release = update.parse_release(release_payload(), "amd64")
        self.ctl = FakeCtl()

    def tearDown(self):
        self.tmp.cleanup()

    def fake_download(self, release, folder):
        path = folder / "sing-box.new"
        path.write_text("#!/bin/sh\necho sing-box version 1.14.5\n")
        path.chmod(0o755)
        return path

    def mocks(self):
        return [
            patch.object(update.os, "geteuid", return_value=0),
            patch.object(state, "operation_lock", return_value=nullcontext()),
            patch.object(state, "create_snapshot", return_value="20261115T120000Z-1234abcd"),
            patch.object(update, "download_verified", side_effect=self.fake_download),
        ]

    def test_manual_update_only_after_config_validation_and_backup(self):
        with self.mocks()[0], self.mocks()[1], self.mocks()[2] as snap, self.mocks()[3]:
            with patch.object(update.time, "sleep", return_value=None):
                result = update.update_core(self.layout, self.ctl, self.release)
        self.assertEqual(result, "20261115T120000Z-1234abcd")
        self.assertTrue(snap.called)
        self.assertEqual(self.ctl.events, ["check", "stop", "start"])
        self.assertEqual(update.installed_version(self.layout.binary), "1.14.5")

    def test_failed_restart_calls_rollback(self):
        self.ctl.start_fail = True
        with self.mocks()[0], self.mocks()[1], self.mocks()[2], self.mocks()[3]:
            with patch.object(state, "restore_snapshot", return_value="undo") as restore:
                with self.assertRaisesRegex(RuntimeError, "simulated failing start"):
                    update.update_core(self.layout, self.ctl, self.release)
        restore.assert_called_once()

    def test_existing_unknown_unit_refused_without_side_effect(self):
        self.layout.unit.write_text("[Service]\nExecStart=/usr/bin/other\n")
        with self.mocks()[0], patch.object(update, "download_verified") as dl:
            with self.assertRaisesRegex(ValueError, "dedicated"):
                update.update_core(self.layout, self.ctl, self.release)
        dl.assert_not_called()

    def test_no_downgrade(self):
        older = dict(self.release, version="1.14.3")
        with self.mocks()[0], patch.object(update, "download_verified") as dl:
            self.assertIsNone(update.update_core(self.layout, self.ctl, older))
        dl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
