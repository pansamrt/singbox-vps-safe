"""Security tests for opt-in Smart WARP WireGuard endpoint routing."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import warp
import state

PRIVATE_KEY = base64.b64encode(bytes(range(32))).decode()
PUBLIC_KEY = base64.b64encode(bytes(range(32, 64))).decode()


def profile(family="dual", extra=""):
    address = ("172.16.0.2/32, 2606:4700:110:abcd::2/128"
               if family == "dual" else "172.16.0.2/32")
    return (f"[Interface]\nPrivateKey = {PRIVATE_KEY}\nAddress = {address}\nMTU=1280\n"
            f"[Peer]\nPublicKey = {PUBLIC_KEY}\nEndpoint = 162.159.193.1:2408\n"
            f"AllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n"
            f"Reserved = 0,0,0\n{extra}")


def base_config():
    return {
        "log": {"level": "warn"},
        "inbounds": [{"type": "vless", "tag": "vless-reality-in", "listen": "::",
                      "listen_port": 8443}],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {"final": "direct"},
    }


class FakeService:
    def __init__(self, failed_starts=0):
        self.active = True
        self.failed_starts = failed_starts
        self.stop_calls = 0
        self.start_calls = 0
        self.checked = []

    def is_active(self):
        return self.active

    def stop(self):
        self.stop_calls += 1
        self.active = False

    def daemon_reload(self):
        pass

    def start(self):
        self.start_calls += 1
        if self.failed_starts:
            self.failed_starts -= 1
            raise RuntimeError("Synthetic service startup failure")
        self.active = True

    def check_config(self, binary, config):
        data = json.loads(Path(config).read_text(encoding="utf8"))
        assert isinstance(data.get("inbounds"), list)
        self.checked.append(data)


class WarpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = self.root / "warp.conf"
        self.profile.write_text(profile(), encoding="utf8")
        self.profile.chmod(0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_parses_ipv4_ipv6_and_never_system_interface(self):
        for family in ("ipv4", "ipv6", "dual"):
            with self.subTest(family=family):
                endpoint = warp.read_profile(self.profile, family)
                self.assertEqual(endpoint["type"], "wireguard")
                self.assertIs(endpoint["system"], False)
                self.assertEqual(len(endpoint["peers"]), 1)
                self.assertEqual(len(endpoint["address"]), len(warp.FAMILIES[family]))
                self.assertEqual(endpoint["peers"][0]["address"], "162.159.193.1")
                self.assertEqual(endpoint["peers"][0]["port"], 2408)
                self.assertEqual(endpoint["private_key"], PRIVATE_KEY)

    def test_profile_rejects_unsafe_scripts_symlinks_and_permissions(self):
        protected = self.root / "keep"
        protected.write_text("DO NOT WRITE")
        link = self.root / "linked.conf"
        link.symlink_to(self.profile)
        with self.assertRaises(ValueError):
            warp.read_profile(link, "dual")
        with self.assertRaises(ValueError):
            self.profile.chmod(0o644)
            warp.read_profile(self.profile, "dual")
        self.profile.chmod(0o600)
        for extra in ("PreUp = touch /tmp/malicious\n", "DNS = 1.1.1.1\n",
                      "PostDown=iptables -F\n"):
            # Insert script options in [Interface]; never execute or import them.
            unsafe = self.root / "unsafe.conf"
            unsafe.write_text(profile().replace("MTU=1280\n", "MTU=1280\n" + extra))
            unsafe.chmod(0o600)
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    warp.read_profile(unsafe, "dual")
            unsafe.unlink()
        self.assertEqual(protected.read_text(), "DO NOT WRITE")

    def test_reject_ip_family_without_valid_tunnel_address_or_routes(self):
        self.profile.write_text(profile("ipv4"))
        with self.assertRaises(ValueError):
            warp.read_profile(self.profile, "ipv6")
        self.profile.write_text(profile().replace("::/0", "2001:db8::/32"))
        with self.assertRaises(ValueError):
            warp.read_profile(self.profile, "dual")
        self.profile.write_text(profile().replace("162.159.193.1:2408", "warp.example.com:2408"))
        with self.assertRaises(ValueError):
            warp.read_profile(self.profile, "dual")
        self.profile.write_text(profile().replace("162.159.193.1:2408", "127.0.0.1:2408"))
        with self.assertRaises(ValueError):
            warp.read_profile(self.profile, "dual")

    def test_route_scoped_to_explicit_inbound_and_family(self):
        source = base_config()
        for family, versions in (("ipv4", [4]), ("ipv6", [6]), ("dual", [4, 6])):
            with self.subTest(family=family):
                endpoint = warp.read_profile(self.profile, family)
                proposed = warp.add_warp(source, endpoint, "vless-reality-in", family)
                self.assertEqual(proposed["route"]["final"], "direct")
                self.assertEqual(proposed["route"]["rules"],
                                 [warp.rule_for("vless-reality-in", v) for v in versions])
                self.assertEqual(proposed["endpoints"][0]["tag"], warp.TAG)
                self.assertIs(proposed["endpoints"][0]["system"], False)
                self.assertEqual(warp.remove_warp(proposed, "vless-reality-in", family,
                                                  source), source)
                self.assertNotIn("endpoints", source)

    def test_reject_host_tun_inbound_and_conflicting_tag(self):
        endpoint = warp.read_profile(self.profile, "dual")
        for config, tag in (
            ({**base_config(), "inbounds": [{"type": "tun", "tag": "host-tun"}]}, "host-tun"),
            ({**base_config(), "endpoints": [{"type": "wireguard", "tag": warp.TAG}]}, "vless-reality-in"),
            ({**base_config(), "route": {"final": "another-out"}}, "vless-reality-in"),
        ):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError):
                    warp.add_warp(config, endpoint, tag, "dual")

    def test_stateful_enable_disable_and_rollback_with_synthetic_service(self):
        layout = state.Layout(binary=self.root / "sing-box",
                              config=self.root / "config.json",
                              unit=self.root / "unit.service",
                              state=self.root / "state")
        layout.binary.write_bytes(b"fake-stub-binary")
        layout.binary.chmod(0o755)
        layout.unit.write_text("[Service]\nUser=singbox-safe\n")
        layout.unit.chmod(0o644)
        original = base_config()
        layout.config.write_text(json.dumps(original), encoding="utf8")
        layout.config.chmod(0o640)
        ctl = FakeService()
        with patch.object(warp, "ensure_owned_install"):
            with patch.object(warp.os, "chown"):
                backup = warp.enable(layout, ctl, self.profile, "vless-reality-in", "dual")
                self.assertTrue(state.SNAPSHOT_ID.fullmatch(backup))
                self.assertTrue(ctl.active)
                self.assertEqual(ctl.stop_calls, 1)
                self.assertEqual(ctl.start_calls, 1)
                self.assertEqual(warp.read_marker(layout)["snapshot"], backup)
                self.assertIn("endpoints", warp.load_config(layout))
                disable_backup = warp.disable(layout, ctl)
                self.assertTrue(state.SNAPSHOT_ID.fullmatch(disable_backup))
                self.assertTrue(ctl.active)
                self.assertEqual(warp.load_config(layout), original)
                self.assertFalse(warp.marker_path(layout).exists())

    def test_manual_edit_refuses_disable_without_stopping_service(self):
        layout = state.Layout(binary=self.root / "sing-box",
                              config=self.root / "config.json",
                              unit=self.root / "unit.service",
                              state=self.root / "state")
        layout.binary.write_bytes(b"fake-stub-binary")
        layout.binary.chmod(0o755)
        layout.unit.write_text("[Service]\nUser=singbox-safe\n")
        layout.unit.chmod(0o644)
        layout.config.write_text(json.dumps(base_config()))
        layout.config.chmod(0o640)
        ctl = FakeService()
        with patch.object(warp, "ensure_owned_install"), patch.object(warp.os, "chown"):
            warp.enable(layout, ctl, self.profile, "vless-reality-in", "dual")
            current = warp.load_config(layout)
            current["log"]["level"] = "error"
            layout.config.write_text(json.dumps(current))
            initial_stops = ctl.stop_calls
            with self.assertRaisesRegex(RuntimeError, "changed since enable"):
                warp.disable(layout, ctl)
            self.assertEqual(ctl.stop_calls, initial_stops)
            self.assertTrue(ctl.active)
            self.assertTrue(warp.marker_path(layout).exists())

    def test_startup_failure_restores_old_config_and_no_marker(self):
        layout = state.Layout(binary=self.root / "sing-box",
                              config=self.root / "config.json",
                              unit=self.root / "unit.service",
                              state=self.root / "state")
        layout.binary.write_bytes(b"fake-stub-binary")
        layout.binary.chmod(0o755)
        layout.unit.write_text("[Service]\nUser=singbox-safe\n")
        layout.unit.chmod(0o644)
        original = base_config()
        layout.config.write_text(json.dumps(original))
        layout.config.chmod(0o640)
        ctl = FakeService(failed_starts=1)
        with patch.object(warp, "ensure_owned_install"):
            with patch.object(warp.os, "chown"):
                with self.assertRaises(RuntimeError):
                    warp.enable(layout, ctl, self.profile, "vless-reality-in", "ipv4")
        self.assertEqual(warp.load_config(layout), original)
        self.assertTrue(ctl.active)
        self.assertFalse(warp.marker_path(layout).exists())


if __name__ == "__main__":
    unittest.main()
