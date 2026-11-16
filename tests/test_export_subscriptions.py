"""Verify offline safe subscription export without exposing credentials."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_subscriptions as exp


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.reality = {"protocol": "vless-reality", "listen_port": 8443,
                        "uuid": "01234567-89ab-4def-8123-0123456789ab",
                        "flow": "xtls-rprx-vision",
                        "server_name": "www.example.org",
                        "reality_public_key": "A" * 43,
                        "reality_short_id": "a1b2c3d4e5f67890"}

    def test_reality_share_and_outbound(self):
        result = exp.build_outputs([self.reality], "203.0.113.22")
        self.assertIn(b"vless://01234567", result["v2rayn"])
        self.assertIn(b"security=reality", result["v2rayn"])
        self.assertIn(b"pbk=", result["v2rayn"])
        self.assertIn("clash", result)
        cfg = json.loads(result["sing-box"])
        self.assertEqual(cfg["outbounds"][0]["tls"]["reality"]["public_key"], "A" * 43)
        self.assertFalse(cfg["outbounds"][0]["tls"].get("insecure", False))
        self.assertEqual(cfg["route"]["final"], cfg["outbounds"][0]["tag"])
        clash = json.loads(result["clash"])
        self.assertEqual(clash["proxies"][0]["type"], "vless")
        self.assertNotIn("skip-cert-verify", clash["proxies"][0])

    def test_all_four_protocols_and_ipv6(self):
        base = {"server_name": "vpn.example.org", "listen_port": 443, "password": "secret-test"}
        clients = [self.reality, dict(base, protocol="hysteria2"),
                   dict(base, protocol="tuic", uuid=self.reality["uuid"]),
                   dict(base, protocol="anytls")]
        files = exp.build_outputs(clients, "2001:db8::1")
        cfg = json.loads(files["sing-box"])
        self.assertEqual(len(cfg["outbounds"]), 5)
        self.assertEqual([x["type"] for x in cfg["outbounds"]],
                         ["vless", "hysteria2", "tuic", "anytls", "direct"])
        self.assertIn(b"@[2001:db8::1]", files["v2rayn"])
        self.assertIn("proxies", files)
        self.assertTrue(all("insecure" not in x.get("tls", {}) for x in cfg["outbounds"]))

    def test_reject_invalid_servers(self):
        for server in ("http://host", "host:443", "/etc/passwd", "u@host", "",
                       "host?x=1", "example.org/#bad", "host name", "-bad.example"):
            with self.subTest(server=server):
                with self.assertRaises(ValueError):
                    exp.host_validate(server)

    def test_private_file_input_and_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            meta = root / "client.json"
            meta.write_text(json.dumps(self.reality))
            meta.chmod(0o600)
            c = exp.read_private_meta(meta)
            path = exp.write_outputs(root, exp.build_outputs(c, "vpn.example.org"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertTrue((path / "clash").is_file())
            for file in path.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            meta.chmod(0o644)
            with self.assertRaises(ValueError):
                exp.read_private_meta(meta)

    def test_symlink_meta_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root / "real.json"
            src.write_text(json.dumps(self.reality))
            src.chmod(0o600)
            alias = root / "alias.json"
            alias.symlink_to(src)
            with self.assertRaises(ValueError):
                exp.read_private_meta(alias)


if __name__ == "__main__":
    unittest.main()
