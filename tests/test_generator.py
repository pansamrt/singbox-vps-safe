"""Unit tests for the non-destructive protocol configuration generator."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import generate_config as generator


class ConfigGeneratorTests(unittest.TestCase):
    def test_valid_tls_protocols(self):
        for protocol in ("hysteria2", "tuic", "anytls"):
            with self.subTest(protocol=protocol):
                cfg, client = generator.build_config(
                    protocol, 8443, "0.0.0.0", "example.org",
                    "/etc/singbox-vps-safe/fullchain.pem",
                    "/etc/singbox-vps-safe/privkey.pem", None
                )
                inbound = cfg["inbounds"][0]
                self.assertEqual(inbound["type"], protocol)
                self.assertEqual(inbound["listen_port"], 8443)
                self.assertTrue(inbound["tls"]["enabled"])
                self.assertEqual(inbound["tls"]["server_name"], "example.org")
                self.assertNotIn("insecure", inbound["tls"])
                self.assertTrue(client.get("password"))
                self.assertEqual(cfg["route"]["final"], "direct")
                if protocol == "tuic":
                    self.assertFalse(inbound["zero_rtt_handshake"])
                    self.assertEqual(client["uuid"], inbound["users"][0]["uuid"])

    def test_vless_reality_generation(self):
        with patch.object(generator, "reality_keypair", return_value=("A" * 43, "B" * 43)):
            cfg, client = generator.build_config(
                "vless-reality", 443, "0.0.0.0", None, None, None, "www.example.org"
            )
        inbound = cfg["inbounds"][0]
        self.assertEqual(inbound["type"], "vless")
        self.assertEqual(inbound["users"][0]["flow"], "xtls-rprx-vision")
        self.assertEqual(inbound["tls"]["reality"]["private_key"], "A" * 43)
        self.assertEqual(client["reality_public_key"], "B" * 43)
        self.assertEqual(len(client["reality_short_id"]), 16)

    def test_invalid_arguments_rejected(self):
        for port in (-1, 0, 65536):
            with self.assertRaises(ValueError):
                generator.build_config("anytls", port, "0.0.0.0", "example.org",
                                       "/etc/cert.pem", "/etc/key.pem", None)
        with self.assertRaises(ValueError):
            generator.build_config("hysteria2", 443, "0.0.0.0", "bad://host",
                                   "/etc/cert.pem", "/etc/key.pem", None)
        with self.assertRaises(ValueError):
            generator.build_config("tuic", 443, "0.0.0.0", "example.org",
                                   "/root/cert.pem", "/etc/key.pem", None)
        with self.assertRaises(ValueError):
            generator.build_config("anytls", 443, "0.0.0.0", "example.org",
                                   "/etc/../tmp/a", "/etc/key.pem", None)

    def test_files_private_no_secrets_in_console(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "server.json"
            p = subprocess.run([sys.executable, str(SCRIPT_DIR / "generate_config.py"),
                                "--protocol", "hysteria2", "--port", "45555",
                                "--server-name", "example.org",
                                "--tls-cert", "/etc/singbox-vps-safe/fullchain.pem",
                                "--tls-key", "/etc/singbox-vps-safe/privkey.pem",
                                "--output", str(target)],
                               text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            metadata = target.with_name("server.json.client.json")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(metadata.stat().st_mode & 0o777, 0o600)
            secret = json.loads(metadata.read_text())["password"]
            self.assertNotIn(secret, p.stdout)
            self.assertNotIn(secret, p.stderr)
            self.assertTrue(secret)
            # Running a second time MUST refuse to overwrite existing secrets.
            p2 = subprocess.run([sys.executable, str(SCRIPT_DIR / "generate_config.py"),
                                 "--protocol", "hysteria2", "--port", "45555",
                                 "--server-name", "example.org",
                                 "--tls-cert", "/etc/singbox-vps-safe/fullchain.pem",
                                 "--tls-key", "/etc/singbox-vps-safe/privkey.pem",
                                 "--output", str(target)], text=True, capture_output=True)
            self.assertNotEqual(p2.returncode, 0)
            self.assertTrue(target.exists())

    def test_output_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            intended = Path(d) / "intended.json"
            intended.write_text("PROTECT")
            link = Path(d) / "symlink.json"
            link.symlink_to(intended)
            p = subprocess.run([sys.executable, str(SCRIPT_DIR / "generate_config.py"),
                                "--protocol", "anytls", "--port", "8080",
                                "--server-name", "example.org",
                                "--tls-cert", "/etc/cert.pem",
                                "--tls-key", "/etc/key.pem", "--output", str(link)],
                               text=True, capture_output=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(intended.read_text(), "PROTECT")


if __name__ == "__main__":
    unittest.main()
