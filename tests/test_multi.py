"""Offline multi-inbound and transient TCP/UDP port preflight tests."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_config
import combine_configs
import ports


class MultiInboundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def fragment(self, protocol: str, port: int) -> Path:
        kwargs = {}
        if protocol == "vless-reality":
            kwargs["handshake"] = "www.example.org"
        else:
            kwargs.update(name="example.org",
                          cert="/etc/singbox-vps-safe/fullchain.pem",
                          key="/etc/singbox-vps-safe/privkey.pem")
        with patch.object(generate_config, "reality_keypair",
                          return_value=("A" * 43, "B" * 43)):
            cfg, client = generate_config.build_config(
                protocol, port, "0.0.0.0",
                kwargs.get("name"), kwargs.get("cert"), kwargs.get("key"),
                kwargs.get("handshake")
            )
        path = self.base / f"{protocol}-{port}.json"
        generate_config.private_write(path, cfg)
        generate_config.private_write(path.with_name(path.name + ".client.json"), client)
        return path

    def test_combine_all_four_protocols_with_tcp_udp_443(self):
        files = [
            self.fragment("vless-reality", 443),
            self.fragment("hysteria2", 443),
            self.fragment("tuic", 8443),
            self.fragment("anytls", 8443)
        ]
        config, client_data = combine_configs.combine(files)
        self.assertEqual(len(config["inbounds"]), 4)
        self.assertEqual(len(client_data["clients"]), 4)
        self.assertEqual(len(ports.bindings(config)), 4)
        self.assertEqual([x[0] for x in ports.bindings(config)],
                         ["TCP", "UDP", "UDP", "TCP"])

    def test_duplicate_tcp_port_rejected(self):
        files = [self.fragment("vless-reality", 443),
                 self.fragment("anytls", 443)]
        with self.assertRaisesRegex(ValueError, "TCP listen port conflict"):
            combine_configs.combine(files)

    def test_duplicate_protocol_tag_rejected(self):
        files = [self.fragment("hysteria2", 8443),
                 self.fragment("hysteria2", 9443)]
        with self.assertRaisesRegex(ValueError, "Duplicate inbound tag"):
            combine_configs.combine(files)

    def test_client_metadata_must_match_server(self):
        path = self.fragment("tuic", 8443)
        sidecar = path.with_name(path.name + ".client.json")
        data = json.loads(sidecar.read_text())
        data["listen_port"] = 8444
        sidecar.write_text(json.dumps(data))
        sidecar.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "sidecar port mismatch"):
            combine_configs.combine([path])

    def test_world_readable_credential_files_rejected(self):
        path = self.fragment("anytls", 9443)
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            combine_configs.combine([path])

    def test_combine_cli_private_files_no_secrets_in_output(self):
        files = [self.fragment("hysteria2", 7443), self.fragment("anytls", 8443)]
        dest = self.base / "bundle.json"
        args = [sys.executable,
                str(Path(combine_configs.__file__)), "--output", str(dest),
                *(str(f) for f in files)]
        proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sidecar = self.base / "bundle.json.client.json"
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        secret = json.loads(sidecar.read_text())["clients"][0]["password"]
        self.assertNotIn(secret, proc.stdout)
        self.assertNotIn(secret, proc.stderr)
        repeat = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(repeat.returncode, 0)
        self.assertTrue(dest.exists())


class PortTests(unittest.TestCase):
    def test_ipv6_and_collision_policy(self):
        cfg = {"inbounds": [
            {"type": "vless", "listen": "::", "listen_port": 443},
            {"type": "hysteria2", "listen": "::", "listen_port": 443}
        ]}
        self.assertEqual(len(ports.bindings(cfg)), 2)
        cfg["inbounds"].append({"type": "anytls", "listen": "127.0.0.1", "listen_port": 443})
        with self.assertRaisesRegex(ValueError, "Duplicate TCP"):
            ports.bindings(cfg)

    def test_bool_and_missing_ports_rejected(self):
        cfg = {"inbounds": [{"type": "anytls", "listen": "0.0.0.0", "listen_port": True}]}
        with self.assertRaisesRegex(ValueError, "Invalid listen_port"):
            ports.bindings(cfg)

    def test_read_config_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"inbounds": [], "inbounds": []}')
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                ports.read_config(path)

    def test_read_config_requires_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"inbounds":[]}')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                ports.read_config(path)

    def test_occupied_port_detected_without_modifying_listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            cfg = {"inbounds": [
                {"type": "vless", "listen": "127.0.0.1", "listen_port": port}
            ]}
            with self.assertRaisesRegex(ValueError, "Cannot bind TCP"):
                ports.preflight(cfg)
            self.assertGreater(port, 0)


if __name__ == "__main__":
    unittest.main()
