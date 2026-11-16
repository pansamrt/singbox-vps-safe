"""Local-only Smart subscription tests (never contact public network)."""
import http.client
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_subscriptions.py"
spec = importlib.util.spec_from_file_location("local_subscriptions", SCRIPT)
sub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sub)


class SubscriptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "private"
        self.source = Path(self.tmp.name) / "smart" / "subscribe"
        self.source.mkdir(parents=True)
        self.source.joinpath("v2rayn").write_text("vless://secret@example.org:443")
        self.source.joinpath("shadowrocket").write_text("BASE64PLACEHOLDER")
        self.source.joinpath("clash").write_text(
            'proxy-providers:\n  node:\n    url: "http://192.0.2.2:8787/old-uuid/proxies"\n'
        )
        self.source.joinpath("proxies").write_text("proxies:\n  - name: proxy\n")
        self.source.joinpath("sing-box").write_text('{"outbounds":[]}')
        sub.init(self.root, 8787, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_token_independent_of_node_secret(self):
        x = sub.settings(self.root)
        self.assertEqual(x["base"], "http://127.0.0.1:8787")
        self.assertRegex(x["token"], sub.TOKEN_RE)
        self.assertNotIn("secret", x["token"])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "access.json").stat().st_mode & 0o777, 0o600)

    def test_publish_only_known_files_and_rewrite_clash_provider(self):
        self.source.joinpath("unapproved.txt").write_text("DONT-PUBLISH")
        self.assertEqual(sub.publish(self.root, self.source), 5)
        cfg = sub.settings(self.root)
        folder = self.root / cfg["generation"]
        self.assertFalse((folder / "unapproved.txt").exists())
        self.assertEqual((folder / "v2rayn").stat().st_mode & 0o777, 0o600)
        clash = (folder / "clash").read_text()
        self.assertIn(f"{cfg['base']}/{cfg['token']}/proxies", clash)
        self.assertNotIn("old-uuid", clash)

    def test_rotate_revokes_and_regenerates_provider_url(self):
        sub.publish(self.root, self.source)
        old = sub.settings(self.root)
        sub.rotate(self.root, "https://sub.example.org")
        new = sub.settings(self.root)
        self.assertNotEqual(old["token"], new["token"])
        self.assertEqual(new["base"], "https://sub.example.org")
        new_clash = (self.root / new["generation"] / "clash").read_text()
        self.assertIn("https://sub.example.org/" + new["token"] + "/proxies", new_clash)
        self.assertNotIn(old["token"], new_clash)
        self.assertNotIn("http://192.0.2.2", new_clash)

    def test_reject_insecure_remote_and_credentials(self):
        for base in ("http://203.0.113.23:8787", "http://example.org",
                     "https://user:pass@example.org", "https://example.org/path",
                     "https://example.org?leak=true", "file:///tmp"):
            with self.subTest(base=base):
                with self.assertRaises(ValueError):
                    sub.valid_base(base, 8787)

    def test_refuse_symlinked_source_and_state(self):
        sub.publish(self.root, self.source)
        link = self.source / "throne"
        link.symlink_to(self.source / "v2rayn")
        with self.assertRaises(ValueError):
            sub.publish(self.root, self.source)
        link.unlink()
        redirect = Path(self.tmp.name) / "redirect"
        redirect.symlink_to(self.root)
        with self.assertRaises(ValueError):
            sub.settings(redirect)

    def test_agent_detection(self):
        available = {"v2rayn", "clash", "clash2", "sing-box", "shadowrocket"}
        self.assertEqual(sub.auto_file("clash-verge", False, available), "clash")
        self.assertEqual(sub.auto_file("Mihomo", True, available), "clash2")
        self.assertEqual(sub.auto_file("sing-box/1.14", False, available), "sing-box")
        self.assertEqual(sub.auto_file("Shadowrocket", False, available), "shadowrocket")
        self.assertEqual(sub.auto_file("", False, available), "v2rayn")

    def test_local_http_access_rejects_bad_tokens_and_paths(self):
        sub.publish(self.root, self.source)
        token = sub.settings(self.root)["token"]
        handler = type("TestBoundHandler", (sub.Handler,), {"root": self.root})
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            server.daemon_threads = True
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            def call(path, agent=""):
                c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
                c.request("GET", path, headers={"User-Agent": agent})
                response = c.getresponse()
                status, data = response.status, response.read()
                headers = dict(response.getheaders())
                c.close()
                return status, data, headers
            ok, data, head = call("/" + token + "/auto", "Clash-Verge")
            self.assertEqual(ok, 200)
            self.assertIn(b"proxy-providers:", data)
            self.assertEqual(head["Cache-Control"], "private, no-store")
            bad_paths = (
                "/" + "X" * 48 + "/clash", "/bad/clash",
                "/" + token + "/../../etc/passwd",
                "/" + token + "/%2e%2e%2fconfig",
                "/" + token + "/access.json",
                "/" + token + "/clash?token=bad",
            )
            for path in bad_paths:
                with self.subTest(path=path):
                    status, _, _ = call(path)
                    self.assertEqual(status, 404)
            sub.rotate(self.root)
            status, _, _ = call("/" + token + "/clash")
            self.assertEqual(status, 404)
            new = sub.settings(self.root)["token"]
            status, body, _ = call("/" + new + "/clash")
            self.assertEqual(status, 200)
            self.assertIn(new.encode(), body)
            server.shutdown()
            t.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
