"""Offline checks for isolated, non-executing optional external source reviews."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extensions


class ExtensionTests(unittest.TestCase):
    def test_pinned_commits_and_no_moving_main(self):
        for tool, data in extensions.TOOLS.items():
            with self.subTest(tool=tool):
                url = extensions.raw_url(tool)
                self.assertTrue(url.startswith("https://raw.githubusercontent.com/"))
                self.assertIn("/" + data["commit"] + "/", url)
                self.assertNotIn("/main/", url)
                self.assertEqual(len(data["commit"]), 40)
                self.assertEqual(len(data["blob_sha1"]), 40)

    def test_git_blob_sha1_validation(self):
        data = b"#!/usr/bin/env bash\necho not-run\n"
        expected = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        actual = extensions.validate_blob(data, expected, len(data))
        self.assertEqual(actual, hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            extensions.validate_blob(data, expected, 999)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            extensions.validate_blob(data, "0" * 40, len(data))

    def test_non_executable_and_non_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / "review.sh"
            extensions.save_private(dst, b"echo harmless\n")
            self.assertEqual(dst.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                extensions.save_private(dst, b"new data")
            self.assertEqual(dst.read_bytes(), b"echo harmless\n")

    def test_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            protected = Path(d) / "keep"
            protected.write_bytes(b"preserve")
            link = Path(d) / "script.sh"
            link.symlink_to(protected)
            with self.assertRaises(FileExistsError):
                extensions.save_private(link, b"new")
            self.assertEqual(protected.read_bytes(), b"preserve")

    def test_download_does_not_execute(self):
        data = b"#!/usr/bin/env bash\necho should-never-run\n"
        digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        spec = dict(extensions.TOOLS["bbr-v3"])
        spec["size"] = len(data)
        spec["blob_sha1"] = digest

        class Response(BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()

        class FakeOpener:
            def open(self, request, timeout):
                return Response(data)

        with tempfile.TemporaryDirectory() as d:
            with patch.dict(extensions.TOOLS, {"bbr-v3": spec}):
                with patch.object(extensions, "build_opener", return_value=FakeOpener()):
                    with patch.object(subprocess, "run") as run:
                        url, sha = extensions.download_review_copy("bbr-v3", Path(d) / "review.sh")
                        run.assert_not_called()
            self.assertEqual(sha, hashlib.sha256(data).hexdigest())
            self.assertTrue(url.endswith("/install.sh"))
            self.assertEqual((Path(d) / "review.sh").stat().st_mode & 0o111, 0)

    def test_no_root_or_network_needed_to_show_source(self):
        with patch.object(extensions, "build_opener") as opener:
            extensions.show_sources()
            opener.assert_not_called()

    def test_redirect_is_rejected(self):
        handler = extensions.NoRedirects()
        with self.assertRaisesRegex(ValueError, "redirect"):
            handler.redirect_request(None, None, 302, "redirect", {}, "https://evil.example/test")


if __name__ == "__main__":
    unittest.main()
