#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Real IPv4+IPv6 Smart integration; no external network or real credentials.

Actual official sing-box binary is used as both server and test client.
IPv4/IPv6 SOCKS TCP and Hysteria2 QUIC/UDP transport data through loopback.
"""
from __future__ import annotations
import http.server
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path("/usr/local/bin/sing-box")
sys.path.insert(0, str(ROOT / "scripts"))
import network_modes
import ports
import local_subscriptions as subs


def assert_true(value, message):
    if not value:
        raise AssertionError(message)
    print("PASS:", message, flush=True)


def port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_checked(command):
    p = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise RuntimeError("Command failed: " + str(command[:2]) + ": " + p.stderr[-1400:])
    return p


def check_config(path: Path):
    run_checked([str(BINARY), "check", "-c", str(path)])


class LocalHTTP(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def do_GET(self):
        data = b"SMART_DUAL_STACK_LOCAL_ECHO"
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *args):
        pass


def socks_echo(proxy_host, proxy_port, target_port):
    with socket.create_connection((proxy_host, proxy_port), timeout=5) as s:
        s.settimeout(12)
        s.sendall(b"\x05\x01\x00")
        assert_true(s.recv(2) == b"\x05\x00",
                    f"SOCKS handshake via {proxy_host}")
        s.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + target_port.to_bytes(2, "big"))
        header = recv_exact(s, 4)
        assert_true(header[:2] == b"\x05\x00",
                    f"SOCKS CONNECT via {proxy_host}")
        if header[3] == 1:
            recv_exact(s, 6)
        elif header[3] == 4:
            recv_exact(s, 18)
        elif header[3] == 3:
            size = recv_exact(s, 1)[0]
            recv_exact(s, size + 2)
        else:
            raise AssertionError("Unexpected SOCKS5 reply address family")
        s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        response = bytearray()
        while True:
            part = s.recv(8192)
            if not part:
                break
            response.extend(part)
        assert_true(b"SMART_DUAL_STACK_LOCAL_ECHO" in response,
                    f"Live proxied HTTP bytes received via {proxy_host}")


def recv_exact(sock, size):
    data = b""
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise EOFError("Unexpected end of SOCKS reply")
        data += part
    return data


def wait_tcp(port_num, proc, timeout=12, host="127.0.0.1"):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if proc.poll() is not None:
            raise RuntimeError("sing-box exited before TCP listener became active")
        try:
            with socket.create_connection((host, port_num), 0.3):
                return
        except OSError:
            time.sleep(0.12)
    raise TimeoutError("TCP listener not ready")


class Core:
    def __init__(self, config: Path, log: Path):
        self.config = config
        self.log = log
        self.file = None
        self.proc = None
    def __enter__(self):
        check_config(self.config)
        self.file = self.log.open("w", encoding="utf8")
        self.proc = subprocess.Popen([str(BINARY), "run", "-c", str(self.config)],
                                     stdout=self.file, stderr=subprocess.STDOUT)
        return self
    def __exit__(self, exc_type, exc, tb):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=4)
        if self.file:
            self.file.close()
        if exc_type:
            print("--- sing-box diagnostics from", self.log.name, "---", flush=True)
            print(self.log.read_text(encoding="utf8")[-3500:], flush=True)


def save_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf8")
    path.chmod(0o600)


def generate_test_ca(home: Path):
    ca, cakey = home / "ca.crt", home / "ca.key"
    key, csr, cert = home / "server.key", home / "server.csr", home / "server.crt"
    ext = home / "server.ext"
    ext.write_text("subjectAltName=DNS:smart-ci.invalid\nextendedKeyUsage=serverAuth\n"
                   "keyUsage=digitalSignature,keyEncipherment\n", encoding="utf8")
    cmds = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(cakey), "-out", str(ca), "-subj", "/CN=Smart Offline CA",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(csr), "-subj", "/CN=smart-ci.invalid"],
        ["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca),
         "-CAkey", str(cakey), "-CAcreateserial", "-out", str(cert),
         "-days", "1", "-sha256", "-extfile", str(ext)],
    ]
    for command in cmds:
        run_checked(command)
    return ca, cert, key


def http_get(host, port_num, path, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port_num, timeout=1)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()
        except OSError:
            time.sleep(0.15)
    raise TimeoutError("Local subscription HTTP listener not ready: " + host)


def test_local_subscriber_families(home: Path):
    source = home / "generated-client-subscriptions"
    source.mkdir()
    (source / "v2rayn").write_text("vless://SMART_OFFLINE_TEST_NODE", encoding="utf8")
    for mode in ("dual", "ipv6"):
        root = home / ("subscription-state-" + mode)
        local_port = port()
        subs.init(root, local_port, None, mode)
        subs.publish(root, source)
        settings = subs.settings(root)
        if mode == "ipv6":
            assert_true(settings["base"] == "http://[::1]:" + str(local_port),
                        "IPv6-only subscription URL uses bracketed local IPv6 address")
        process = subprocess.Popen(
            [sys.executable, "-B", str(ROOT / "scripts/local_subscriptions.py"),
             "--state-dir", str(root), "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        try:
            prefix = "/" + settings["token"] + "/v2rayn"
            status, payload = http_get("::1", local_port, prefix)
            assert_true(status == 200 and b"SMART_OFFLINE_TEST_NODE" in payload,
                        mode + " local subscription reached over native IPv6")
            if mode == "dual":
                status, payload = http_get("127.0.0.1", local_port, prefix)
                assert_true(status == 200 and b"SMART_OFFLINE_TEST_NODE" in payload,
                            "Dual local subscription also reached over IPv4")
            else:
                try:
                    with socket.create_connection(("127.0.0.1", local_port), timeout=0.4):
                        raise AssertionError("IPv6-only local subscription leaked onto IPv4")
                except OSError:
                    assert_true(True, "IPv6-only subscription refuses IPv4")
            invalid, _ = http_get("::1", local_port, "/bad/v2rayn")
            assert_true(invalid == 404, "Invalid subscription bearer token rejected over IPv6")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stderr:
                process.stderr.close()


def main():
    assert_true(BINARY.is_file(), "Official sing-box native binary is present")
    v = run_checked([str(BINARY), "version"])
    assert_true("sing-box version 1.14.4" in v.stdout,
                "Pinned official sing-box v1.14.4")
    assert_true(network_modes.resolve_listen() == ("dual", "::"),
                "Smart default is :: dual listener")
    with socket.socket(socket.AF_INET6) as v6:
        v6.bind(("::1", 0))
        assert_true(v6.getsockname()[0] == "::1", "Real IPv6 loopback socket available")
    with tempfile.TemporaryDirectory(prefix="smart-dual-") as td:
        home = Path(td)
        echo = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalHTTP)
        echo.daemon_threads = True
        thread = threading.Thread(target=echo.serve_forever, daemon=True)
        thread.start()
        target = echo.server_port
        try:
            tcp_port = port()
            server_tcp = home / "tcp-server.json"
            config_tcp = {
                "log": {"level": "warn"},
                "inbounds": [{"type": "socks", "tag": "real-tcp-test",
                              "listen": "::", "listen_port": tcp_port}],
                "outbounds": [{"type": "direct", "tag": "direct"}],
                "route": {"final": "direct"},
            }
            save_json(server_tcp, config_tcp)
            with Core(server_tcp, home / "tcp-server.log") as active:
                wait_tcp(tcp_port, active.proc)
                socks_echo("127.0.0.1", tcp_port, target)
                socks_echo("::1", tcp_port, target)
                assert_true(True, "Real native dual-stack TCP listener accepts IPv4 and IPv6")
            # A concrete IPv6 address must not inadvertently accept IPv4.
            # Likewise the IPv4-only wildcard must not be reachable via ::1.
            for mode, listening, permitted, forbidden in (
                ("ipv4", "0.0.0.0", "127.0.0.1", "::1"),
                ("ipv6", "::1", "::1", "127.0.0.1"),
            ):
                single_port = port()
                single_config = home / ("tcp-" + mode + ".json")
                save_json(single_config, {
                    "log": {"level": "warn"},
                    "inbounds": [{"type": "socks", "tag": "single-family-test",
                                  "listen": listening, "listen_port": single_port}],
                    "outbounds": [{"type": "direct", "tag": "direct"}],
                    "route": {"final": "direct"}
                })
                with Core(single_config, home / ("tcp-" + mode + ".log")) as single:
                    wait_tcp(single_port, single.proc, host=permitted)
                    socks_echo(permitted, single_port, target)
                    try:
                        with socket.create_connection((forbidden, single_port), timeout=0.6):
                            raise AssertionError(mode + " listener accepted wrong IP family")
                    except OSError:
                        pass
                    assert_true(True, mode + " listener rejects the other IP family")
            udp_port = port(socket.SOCK_DGRAM)
            ports.probe(("UDP", "::", udp_port))
            ca, cert, key = generate_test_ca(home)
            server_udp = home / "hy2-server.json"
            hy2 = {
                "log": {"level": "warn"},
                "inbounds": [{
                    "type": "hysteria2", "tag": "test-hy2", "listen": "::",
                    "listen_port": udp_port,
                    "users": [{"name": "test", "password": "offline-test-not-a-real-secret"}],
                    "tls": {"enabled": True, "server_name": "smart-ci.invalid",
                            "certificate_path": str(cert), "key_path": str(key),
                            "alpn": ["h3"]},
                }],
                "outbounds": [{"type": "direct", "tag": "direct"}],
                "route": {"final": "direct"},
            }
            save_json(server_udp, hy2)
            with Core(server_udp, home / "hy2-server.log") as active_udp:
                time.sleep(0.65)
                if active_udp.proc.poll() is not None:
                    raise RuntimeError("HY2 UDP server exited early")
                for family, address in (("IPv4", "127.0.0.1"), ("IPv6", "::1")):
                    client_port = port()
                    client = home / ("hy2-client-" + family + ".json")
                    client_conf = {
                        "log": {"level": "warn"},
                        "inbounds": [{"type": "socks", "tag": "local-socks",
                                      "listen": "127.0.0.1", "listen_port": client_port}],
                        "outbounds": [{
                            "type": "hysteria2", "tag": "udp-quic",
                            "server": address, "server_port": udp_port,
                            "password": "offline-test-not-a-real-secret",
                            "tls": {"enabled": True, "server_name": "smart-ci.invalid",
                                    "certificate_path": str(ca), "alpn": ["h3"]},
                        }],
                        "route": {"final": "udp-quic"},
                    }
                    save_json(client, client_conf)
                    with Core(client, home / ("hy2-client-" + family + ".log")) as active_client:
                        wait_tcp(client_port, active_client.proc)
                        socks_echo("127.0.0.1", client_port, target)
                        assert_true(True, f"Actual Hysteria2 QUIC/UDP + verified TLS over {family}")
            test_local_subscriber_families(home)
        finally:
            echo.shutdown()
            echo.server_close()
            thread.join(timeout=3)
    print("SMART DUAL STACK INTEGRATION: PASS; no external network or VPS", flush=True)


if __name__ == "__main__":
    main()
