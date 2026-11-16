"""Smart dual-stack network mode, preflight, and subscription regression tests."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import network_modes
import ports
import export_subscriptions as subscriptions


def free_port(family=socket.AF_INET, kind=socket.SOCK_STREAM):
    with socket.socket(family, kind) as s:
        s.bind(("127.0.0.1" if family == socket.AF_INET else "::1", 0))
        return s.getsockname()[1]


class NetworkModeTests(unittest.TestCase):
    def test_default_dual_and_legacy_explicit_addresses(self):
        self.assertEqual(network_modes.resolve_listen(), ("dual", "::"))
        self.assertEqual(network_modes.resolve_listen("dual"), ("dual", "::"))
        self.assertEqual(network_modes.resolve_listen(address="0.0.0.0"), ("ipv4", "0.0.0.0"))
        self.assertEqual(network_modes.resolve_listen(address="::"), ("dual", "::"))
        self.assertEqual(network_modes.resolve_listen(address="::1"), ("ipv6", "::1"))
        self.assertEqual(network_modes.resolve_listen("ipv4"), ("ipv4", "0.0.0.0"))
        self.assertEqual(network_modes.resolve_listen("ipv6", "::1"), ("ipv6", "::1"))

    def test_ipv6_only_requires_concrete_native_address(self):
        for mode, address in (
            ("ipv6", None), ("ipv6", "::"), ("ipv6", "0.0.0.0"),
            ("dual", "0.0.0.0"), ("dual", "::1"),
            ("ipv4", "::"), ("ipv6", "::ffff:192.0.2.1"),
            ("ipv6", "fe80::1%eth0"), ("dual", "invalid"), ("bad", None)
        ):
            with self.subTest(mode=mode, address=address):
                with self.assertRaises(ValueError):
                    network_modes.resolve_listen(mode, address)

    def test_generator_default_dual_and_ipv6_only(self):
        with tempfile.TemporaryDirectory() as folder:
            for args, expected, mode in (
                ([], "::", "dual"),
                (["--network-mode", "ipv4"], "0.0.0.0", "ipv4"),
                (["--network-mode", "ipv6", "--listen", "::1"], "::1", "ipv6"),
            ):
                destination = Path(folder) / ("generated-" + mode + ".json")
                command = [
                    sys.executable, str(SCRIPTS / "generate_config.py"),
                    "--protocol", "hysteria2", "--port", "20545",
                    "--server-name", "vpn.example.org", "--tls-cert", "/etc/cert.pem",
                    "--tls-key", "/etc/key.pem", "--output", str(destination),
                ] + args
                proc = subprocess.run(command, capture_output=True, text=True, timeout=12)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(destination.read_text())["inbounds"][0]["listen"], expected)
                sidecar = json.loads(Path(str(destination) + ".client.json").read_text())
                self.assertEqual(sidecar["network_mode"], mode)
            error = subprocess.run([
                sys.executable, str(SCRIPTS / "generate_config.py"),
                "--protocol", "hysteria2", "--port", "20546",
                "--server-name", "vpn.example.org", "--tls-cert", "/etc/cert.pem",
                "--tls-key", "/etc/key.pem",
                "--network-mode", "ipv6", "--output", str(Path(folder) / "bad.json")
            ], capture_output=True, text=True)
            self.assertNotEqual(error.returncode, 0)

    def test_port_preflight_detects_ipv4_and_ipv6_conflicts(self):
        if not socket.has_ipv6:
            self.skipTest("Linux IPv6 socket unavailable")
        for protocol, socktype in (("TCP", socket.SOCK_STREAM), ("UDP", socket.SOCK_DGRAM)):
            with self.subTest(protocol=protocol):
                port = free_port(kind=socktype)
                ports.probe((protocol, "::", port))
                with socket.socket(socket.AF_INET, socktype) as busy4:
                    busy4.bind(("0.0.0.0", port))
                    with self.assertRaises(OSError):
                        ports.probe((protocol, "::", port))
                with socket.socket(socket.AF_INET6, socktype) as busy6:
                    busy6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    busy6.bind(("::1", port))
                    with self.assertRaises(OSError):
                        ports.probe((protocol, "::", port))
                ports.probe((protocol, "::", port))


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.client = {
            "protocol": "vless-reality", "listen_port": 8443,
            "uuid": "01234567-89ab-4def-8123-0123456789ab",
            "server_name": "www.example.org", "reality_public_key": "A" * 43,
            "reality_short_id": "abcdef1234567890", "network_mode": "dual"
        }

    def test_dual_ip_creates_two_unique_nodes_and_correct_uri(self):
        data = subscriptions.build_outputs([self.client], "198.51.100.5", "2001:db8::25")
        config = json.loads(data["sing-box"])
        nodes = config["outbounds"][:-1]
        self.assertEqual(len(nodes), 2)
        self.assertEqual([x["server"] for x in nodes], ["198.51.100.5", "2001:db8::25"])
        self.assertNotEqual(nodes[0]["tag"], nodes[1]["tag"])
        links = data["v2rayn"].decode()
        self.assertIn("@198.51.100.5:8443", links)
        self.assertIn("@[2001:db8::25]:8443", links)
        proxies = json.loads(data["clash"])["proxies"]
        self.assertEqual([p["server"] for p in proxies], ["198.51.100.5", "2001:db8::25"])

    def test_dns_hostname_single_endpoint_is_supported(self):
        out = subscriptions.build_outputs([self.client], "example.org")
        self.assertEqual(json.loads(out["sing-box"])["outbounds"][0]["server"], "example.org")

    def test_ipv6_only_endpoint_and_invalid_extra_address(self):
        data = subscriptions.build_outputs([self.client], "2001:db8::9")
        self.assertIn("@[2001:db8::9]:8443", data["v2rayn"].decode())
        with self.assertRaises(ValueError):
            subscriptions.build_outputs([self.client], "2001:db8::9", "2001:db8::9")

    def test_cli_dual_address_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            meta = root / "client.json"
            meta.write_text(json.dumps(self.client))
            meta.chmod(0o600)
            def call(*args):
                return subprocess.run(
                    [sys.executable, str(SCRIPTS / "export_subscriptions.py"),
                     "--client-meta", str(meta), "--output-parent", str(root), *args],
                    capture_output=True, text=True, timeout=8
                )
            good = call("--server-ipv4", "198.51.100.5", "--server-ipv6", "2001:db8::25")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertNotIn("01234567", good.stdout)
            bad = call("--server-ipv4", "2001:db8::25")
            self.assertNotEqual(bad.returncode, 0)
            bad = call("--server", "vpn.example.org", "--server-ipv6", "2001:db8::25")
            self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
