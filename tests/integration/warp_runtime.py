#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check Smart WARP endpoint syntax and unaffected direct traffic on real sing-box.

Uses synthetic WireGuard keys, a documentation-only peer IP and NO real Cloudflare
registration, network egress or tunnel. Offline running preserves direct fallback.
"""
from __future__ import annotations
import base64
import http.server
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import warp

CORE = Path("/usr/local/bin/sing-box")


def require(ok, desc):
    if not ok:
        raise AssertionError(desc)
    print("PASS:", desc, flush=True)


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Echo(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        message = b"SMART_WARP_DIRECT_FALLBACK"
        self.send_response(200)
        self.send_header("Content-Length", str(len(message)))
        self.end_headers()
        self.wfile.write(message)
    def log_message(self, *args):
        pass


def read_exact(sock, count):
    result = b""
    while len(result) < count:
        part = sock.recv(count - len(result))
        if not part:
            raise EOFError("SOCKS5 returned incomplete reply")
        result += part
    return result


def socks_request(proxy_port, dest_port):
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=4) as sock:
        sock.settimeout(6)
        sock.sendall(b"\x05\x01\x00")
        require(read_exact(sock, 2) == b"\x05\x00", "SOCKS5 negotiation succeeded")
        sock.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + dest_port.to_bytes(2, "big"))
        status = read_exact(sock, 4)
        require(status[:2] == b"\x05\x00", "Selected IPv4 destination uses direct fallback")
        if status[3] == 1:
            read_exact(sock, 6)
        elif status[3] == 4:
            read_exact(sock, 18)
        elif status[3] == 3:
            n = read_exact(sock, 1)[0]
            read_exact(sock, n + 2)
        else:
            raise RuntimeError("Unknown SOCKS5 reply address family")
        sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        data = b""
        while True:
            part = sock.recv(4096)
            if not part:
                break
            data += part
        require(b"SMART_WARP_DIRECT_FALLBACK" in data,
                "Native sing-box still carries unrelated direct proxy traffic")


def main():
    require(CORE.is_file(), "Real official sing-box core available")
    version = subprocess.run([str(CORE), "version"], capture_output=True, text=True, check=True)
    require("sing-box version 1.14.4" in version.stdout, "Official v1.14.4 version pinned")
    with tempfile.TemporaryDirectory(prefix="smart-warp-test-") as d:
        home = Path(d)
        profile = home / "private-warp.conf"
        key = base64.b64encode(bytes(range(32))).decode()
        peer = base64.b64encode(bytes(range(32, 64))).decode()
        profile.write_text(
            f"[Interface]\nPrivateKey = {key}\n"
            "Address = 172.16.0.2/32, 2606:4700:110:abcd::2/128\n"
            f"[Peer]\nPublicKey = {peer}\n"
            "Endpoint = 162.159.193.1:2408\nAllowedIPs = 0.0.0.0/0, ::/0\n",
            encoding="utf8")
        profile.chmod(0o600)
        p = port()
        base = {
            "log": {"level": "warn"},
            "inbounds": [{"type": "socks", "tag": "test-proxy",
                          "listen": "127.0.0.1", "listen_port": p}],
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {"final": "direct"}
        }
        for mode in ("ipv4", "ipv6", "dual"):
            endpoint = warp.read_profile(profile, mode)
            config = warp.add_warp(base, endpoint, "test-proxy", mode)
            candidate = home / ("candidate-" + mode + ".json")
            candidate.write_text(json.dumps(config), encoding="utf8")
            candidate.chmod(0o600)
            check = subprocess.run([str(CORE), "check", "-c", str(candidate)],
                                   capture_output=True, text=True, timeout=15)
            if check.returncode:
                # Never print generated config or secrets.
                raise RuntimeError("Official sing-box WireGuard endpoint check failed for " +
                                   mode + "; stderr: " + check.stderr[-400:])
            require(True, "Native WireGuard endpoint and " + mode + " routing syntax validated")
        test_config = home / "candidate-ipv6.json"
        echo = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        echo.daemon_threads = True
        worker = threading.Thread(target=echo.serve_forever, daemon=True)
        worker.start()
        logfile = home / "sing-box.log"
        with logfile.open("wb") as out:
            proc = subprocess.Popen([str(CORE), "run", "-c", str(test_config)],
                                    stdout=out, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError("Core exited; inspect private test logs")
                    try:
                        socks_request(p, echo.server_port)
                        break
                    except (OSError, TimeoutError):
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Native core did not serve direct fallback")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=6)
                echo.shutdown()
                echo.server_close()
                worker.join(timeout=4)
    print("SMART WARP OFFLINE CORE INTEGRATION: PASS")
    print("NOTE: no real Cloudflare tunnel/credentials or public egress was tested.")


if __name__ == "__main__":
    main()
