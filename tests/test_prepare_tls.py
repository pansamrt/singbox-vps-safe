"""Private TLS file staging and nonroot-owned service paths."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_tls


class TLSStageTests(unittest.TestCase):
    def test_rewrite_and_private_staging(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cert = root / "server.crt"
            key = root / "server.key"
            cert.write_text("-----BEGIN CERTIFICATE-----\nFAKECERT\n-----END CERTIFICATE-----\n")
            key.write_text("-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----\n")
            key.chmod(0o600)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"inbounds": [
                {"type": "hysteria2", "tls": {"enabled": True,
                                            "certificate_path": str(cert),
                                            "key_path": str(key)}}]}))
            work = root / "work"
            work.mkdir()
            n = prepare_tls.stage(cfg, work)
            self.assertEqual(n, 1)
            result = json.loads((work / "install-config.json").read_text())
            self.assertEqual(result["inbounds"][0]["tls"]["key_path"],
                             "/etc/singbox-vps-safe/managed-tls/tls-0.key")
            self.assertEqual((work / "managed-tls/tls-0.key").stat().st_mode & 0o777, 0o600)
            self.assertEqual((work / "managed-tls/tls-0.crt").read_bytes(), cert.read_bytes())

    def test_reality_no_cert(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = root / "config.json"
            cfg.write_text('{"inbounds":[{"type":"vless","tls":{"enabled":true}}]}')
            self.assertEqual(prepare_tls.stage(cfg, root), 0)
            self.assertFalse((root / "managed-tls").exists())
            self.assertTrue((root / "install-config.json").exists())

    def test_symlink_and_world_exposed_key_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            key = root / "key"
            key.write_text("test")
            cert = root / "cert"
            cert.write_text("cert")
            link = root / "link"
            link.symlink_to(key)
            cfg = root / "config.json"
            work = root / "work"
            work.mkdir()
            for candidate, mode in ((str(link), 0o600), (str(key), 0o644)):
                key.chmod(mode)
                cfg.write_text(json.dumps({"inbounds": [{"type": "tuic", "tls": {
                    "certificate_path": str(cert), "key_path": candidate}}]}))
                with self.assertRaises(ValueError):
                    prepare_tls.stage(cfg, work)
                self.assertFalse((work / "install-config.json").exists())


if __name__ == "__main__":
    unittest.main()
