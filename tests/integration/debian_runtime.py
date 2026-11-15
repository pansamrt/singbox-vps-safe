#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""End-to-end smoke test on a disposable Debian container.

Starts actual pinned sing-box core and the local subscription HTTP server,
using fscarmen's pinned *upstream templates* processed into example files.
Never executes upstream install.sh or touches a VPS/system firewall.
The Docker runtime has no external network, capabilities, or host system mounts.
"""
from __future__ import annotations

import base64
import ssl
import shutil
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import local_subscriptions as subs

TEMPLATES = Path("/upstream-templates")
BINARY = Path("/usr/local/bin/sing-box")
EXPECTED_VERSION = "1.14.4"


def check(condition: bool, desc: str):
    if not condition:
        raise AssertionError(desc)
    print("PASS:", desc)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, proc: subprocess.Popen, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("service exited unexpectedly: " + str(proc.returncode))
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return
        except OSError:
            time.sleep(0.12)
    raise RuntimeError("service did not open loopback socket")


class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "15")
        self.end_headers()
        self.wfile.write(b"LOCAL TEST ONLY")
    def log_message(self, *args):
        pass


def socks5_echo(port: int, dest_port: int):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall(b"\x05\x01\x00")
        check(sock.recv(2) == b"\x05\x00", "Real sing-box SOCKS5 inbound negotiation")
        sock.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + dest_port.to_bytes(2, "big"))
        header = sock.recv(4)
        check(len(header) == 4 and header[1] == 0, "Real sing-box routed local CONNECT")
        # Consume SOCKS bound address, whether IPv4, IPv6 or domain.
        atyp = header[3]
        if atyp == 1:
            n = 6
        elif atyp == 4:
            n = 18
        elif atyp == 3:
            n = sock.recv(1)[0] + 2
        else:
            raise RuntimeError("Invalid SOCKS5 reply")
        buf = b""
        while len(buf) < n:
            piece = sock.recv(n - len(buf))
            if not piece:
                raise RuntimeError("Incomplete SOCKS5 response")
            buf += piece
        sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        reply = b""
        while True:
            piece = sock.recv(2048)
            if not piece:
                break
            reply += piece
        check(b"LOCAL TEST ONLY" in reply, "Real sing-box proxy loopback HTTP traffic")


def get(port: int, path: str, agent: str = "curl/8", method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(method, path, headers={"User-Agent": agent})
        r = conn.getresponse()
        code, body = r.status, r.read()
        headers = dict(r.getheaders())
        return code, body, headers
    finally:
        conn.close()


def fixture_files(folder: Path, port: int):
    folder.mkdir()
    example_uuid = str(uuid.uuid4())
    url = "http://192.0.2.1:8080/" + example_uuid + "/proxies"
    original_clash = (TEMPLATES / "clash").read_text(encoding="utf8")
    # Mirror fscarmen export_list template replacements on real upstream templates.
    clash = original_clash.replace("PROXY_PROVIDERS_URL", url).replace("NODE_NAME", "LOCAL-SAFE")
    check("url: " + url in clash, "Pinned fscarmen Clash template substitution")
    (folder / "clash").write_text(clash, encoding="utf8")
    # fscarmen's clash2 template is consumed, but no provider URL is assumed.
    clash2 = (TEMPLATES / "clash2").read_text(encoding="utf8")
    (folder / "clash2").write_text(clash2, encoding="utf8")
    proxy_yaml = ("proxies:\n  - name: local-safe\n    type: socks5\n"
                  "    server: 127.0.0.1\n    port: " + str(port) + "\n")
    (folder / "proxies").write_text(proxy_yaml, encoding="utf8")
    sing = (TEMPLATES / "sing-box").read_text(encoding="utf8")
    outbound = json.dumps({"type": "socks", "tag": "local-safe", "server": "127.0.0.1", "server_port": port})
    sing = sing.replace('"<OUTBOUND_REPLACE>",', outbound + ",").replace('"<NODE_REPLACE>"', '"local-safe"')
    check("<OUTBOUND_REPLACE>" not in sing, "Pinned fscarmen sing-box template placeholder expansion")
    json.loads(sing)
    (folder / "sing-box").write_text(sing, encoding="utf8")
    (folder / "v2rayn").write_text("vless://" + example_uuid + "@127.0.0.1:443#local-safe\n")
    (folder / "shadowrocket").write_text(base64.b64encode(
        ("vless://" + example_uuid + "@127.0.0.1:443#local-safe").encode()).decode())
    (folder / "throne").write_text("vless://" + example_uuid + "@127.0.0.1:443#local-safe\n")
    return example_uuid


def make_https_proxy(home: Path, backend_port: int, https_port: int):
    """Disposable nginx + locally signed test CA; real certificate validation."""
    ca_key, ca, key, csr, cert = (home / x for x in
                                  ("ca.key", "ca.crt", "server.key", "server.csr", "server.crt"))
    hostname = "subscriptions.example.invalid"
    ext = home / "server.ext"
    ext.write_text("subjectAltName=DNS:" + hostname + "\nextendedKeyUsage=serverAuth\n")
    commands = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
         "-days", "1", "-keyout", str(ca_key), "-out", str(ca),
         "-subj", "/CN=Disposable CI Testing CA",
         "-addext", "basicConstraints=critical,CA:TRUE"],
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(csr),
         "-subj", "/CN=" + hostname],
        ["openssl", "x509", "-req", "-in", str(csr),
         "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial",
         "-out", str(cert), "-days", "1", "-sha256", "-extfile", str(ext)],
    ]
    for args in commands:
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=15)
    config = home / "nginx-sub.conf"
    config.write_text(f"""
worker_processes 1;
pid {home}/nginx.pid;
error_log {home}/nginx-error.log warn;
events {{ worker_connections 128; }}
http {{
    access_log off;
    proxy_temp_path /tmp/nginx-proxy;
    client_body_temp_path /tmp/nginx-client;
    fastcgi_temp_path /tmp/nginx-fastcgi;
    uwsgi_temp_path /tmp/nginx-uwsgi;
    scgi_temp_path /tmp/nginx-scgi;
    server {{
        listen 127.0.0.1:{https_port} ssl;
        server_name {hostname};
        ssl_certificate {cert};
        ssl_certificate_key {key};
        location / {{
            proxy_pass http://127.0.0.1:{backend_port};
            proxy_set_header Host $host;
            proxy_set_header User-Agent $http_user_agent;
            proxy_buffering off;
        }}
    }}
}}
""")
    nginx = shutil.which("nginx") or "/usr/sbin/nginx"
    validation = subprocess.run([nginx, "-t", "-c", str(config)],
                                capture_output=True, text=True, timeout=8)
    if validation.returncode:
        raise RuntimeError("Nginx test failed: " + validation.stderr[-800:])
    process = subprocess.Popen([nginx, "-c", str(config), "-g", "daemon off;"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    wait_port(https_port, process)
    return process, ca


def https_get(port: int, ca: Path, path: str, agent: str):
    context = ssl.create_default_context(cafile=str(ca))
    conn = http.client.HTTPSConnection("subscriptions.example.invalid", port,
                                     context=context, timeout=4)
    try:
        conn.request("GET", path, headers={"User-Agent": agent})
        r = conn.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        conn.close()


def integration():
    check(BINARY.exists() and BINARY.is_file(), "Pinned official sing-box core present")
    version = subprocess.run([str(BINARY), "version"], capture_output=True, text=True, check=True, timeout=10)
    check("sing-box version " + EXPECTED_VERSION in version.stdout, "Official core reports pinned version")
    check((TEMPLATES / "clash").is_file() and (TEMPLATES / "sing-box").is_file(),
          "Actual pinned fscarmen client templates mounted")

    with tempfile.TemporaryDirectory(prefix="sb-runtime-") as scratch:
        home = Path(scratch)
        config_path = home / "server.json"
        proxy_port = free_port()
        echo_port = free_port()
        sub_port = free_port()
        tls_port = free_port()
        config = {
            "log": {"level": "warn"},
            "inbounds": [{"type": "socks", "tag": "runtime-socks",
                          "listen": "127.0.0.1", "listen_port": proxy_port}],
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {"final": "direct"}
        }
        config_path.write_text(json.dumps(config), encoding="utf8")
        subprocess.run([str(BINARY), "check", "-c", str(config_path)],
                       capture_output=True, text=True, check=True, timeout=15)
        check(True, "Real official sing-box check validates live runtime config")
        echo = ThreadingHTTPServer(("127.0.0.1", echo_port), EchoHandler)
        echo.daemon_threads = True
        t = threading.Thread(target=echo.serve_forever, daemon=True)
        t.start()
        core = subprocess.Popen([str(BINARY), "run", "-c", str(config_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        server = None
        nginx_proc = None
        try:
            wait_port(proxy_port, core)
            socks5_echo(proxy_port, echo_port)

            source = home / "fscarmen"  # locally produced fscarmen-compatible files
            node_uuid = fixture_files(source, proxy_port)
            state = home / "private"
            with subs.exclusive_writer(state):
                subs.init(state, sub_port, f"https://subscriptions.example.invalid:{tls_port}")
                count = subs.publish(state, source)
            check(count == 7, "Seven client-format subscription files published locally")
            info = subs.settings(state)
            check(node_uuid != info["token"], "Subscription token separate from node credentials")
            check(state.stat().st_mode & 0o777 == 0o700,
                  "Secret subscription state dir is owner-only")

            server = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts/local_subscriptions.py"),
                 "--state-dir", str(state), "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            wait_port(sub_port, server)
            nginx_proc, trust_root = make_https_proxy(home, sub_port, tls_port)
            check(True, "Real nginx HTTPS reverse proxy initialized with disposable test CA")
            token = info["token"]
            prefix = "/" + token + "/"
            for agent, path, marker in (
                ("Clash-Verge", "auto", b"proxy-providers:"),
                ("sing-box/1.14", "auto", b'"outbounds"'),
                ("Shadowrocket/2", "auto", None),
                ("curl/8", "auto", b"vless://"),
                ("Mihomo", "auto2", b"proxy-groups:"),
            ):
                status, body, headers = get(sub_port, prefix + path, agent)
                check(status == 200 and (marker is None or marker in body),
                      "Local subscription response for " + agent)
                check(headers.get("Cache-Control") == "private, no-store",
                      "Sensitive subscription never cached by HTTP service")

            tls_code, tls_data, tls_headers = https_get(tls_port, trust_root, prefix + "auto", "Clash-Verge")
            check(tls_code == 200 and b"proxy-providers:" in tls_data,
                  "Trusted TLS nginx proxy forwards client-specific subscription")
            check(tls_headers.get("Cache-Control") == "private, no-store",
                  "TLS response retains cache restrictions")
            try:
                unknown = http.client.HTTPSConnection("subscriptions.example.invalid",
                         tls_port, context=ssl.create_default_context(), timeout=3)
                try:
                    unknown.request("GET", prefix + "auto")
                    unknown.getresponse()
                    raise AssertionError("Untrusted TLS certificate was accepted")
                finally:
                    unknown.close()
            except ssl.SSLCertVerificationError:
                check(True, "TLS client refuses untrusted subscription certificate")
            rejected, _, _ = https_get(tls_port, trust_root, "/wrong/clash", "Clash")
            check(rejected == 404, "HTTPS reverse proxy rejects invalid bearer token")
            ok, body, _ = get(sub_port, prefix + "clash")
            provider = f"https://subscriptions.example.invalid:{tls_port}/" + token + "/proxies"
            check(ok == 200 and provider.encode() in body,
                  "Real fscarmen Clash template now uses independent tokenized HTTPS provider URL")
            check(node_uuid.encode() not in body,
                  "Node UUID is not reused in Clash provider address")
            ok, body, _ = get(sub_port, prefix + "proxies")
            check(ok == 200 and b"local-safe" in body, "Provider endpoint serves locally generated node list")
            ok, body, headers = get(sub_port, prefix + "sing-box")
            check(ok == 200 and isinstance(json.loads(body), dict),
                  "Published sing-box client JSON parses")
            status, body, _ = get(sub_port, prefix + "v2rayn", method="HEAD")
            check(status == 200 and body == b"", "HEAD works without exposing body")

            for forbidden in (
                "/wrong/clash",
                "/" + ("0" * len(token)) + "/clash",
                prefix + "..%2Faccess.json",
                prefix + "access.json",
                prefix + "clash?token=leak",
                prefix + "../access.json",
            ):
                status, _, _ = get(sub_port, forbidden)
                check(status == 404, "Unauthorized or traversal path rejected")

            with subs.exclusive_writer(state):
                subs.rotate(state)
            fresh = subs.settings(state)["token"]
            check(fresh != token, "Token rotation issued new bearer secret")
            status, _, _ = get(sub_port, prefix + "clash")
            check(status == 404, "Old subscription URL revoked")
            status, _, _ = https_get(tls_port, trust_root, prefix + "clash", "Clash-Verge")
            check(status == 404, "Old URL also revoked through real TLS reverse proxy")
            status, body, _ = get(sub_port, "/" + fresh + "/clash")
            check(status == 200 and fresh.encode() in body and token.encode() not in body,
                  "New Clash provider correctly references rotated subscription token")

            # Failed publication must not break the last live generation.
            (source / "throne").unlink()
            (source / "throne").symlink_to(source / "v2rayn")
            previous = subs.settings(state)["generation"]
            try:
                with subs.exclusive_writer(state):
                    subs.publish(state, source)
                raise AssertionError("Symlinked source unexpectedly accepted")
            except ValueError:
                check(subs.settings(state)["generation"] == previous,
                      "Failed publish keeps previous live generation")
            status, _, _ = get(sub_port, "/" + fresh + "/clash")
            check(status == 200, "Existing subscriptions survive failed publication")
        finally:
            if nginx_proc:
                nginx_proc.terminate()
                try:
                    nginx_proc.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    nginx_proc.kill()
                    nginx_proc.wait(timeout=5)
                if nginx_proc.stderr:
                    nginx_proc.stderr.close()
            if server:
                server.terminate()
                try:
                    server.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
                if server.stderr:
                    server.stderr.close()
            core.terminate()
            try:
                core.wait(timeout=7)
            except subprocess.TimeoutExpired:
                core.kill()
                core.wait(timeout=5)
            if core.stderr:
                core.stderr.close()
            echo.shutdown()
            echo.server_close()
            t.join(timeout=3)
            check(core.poll() is not None, "Real sing-box runtime exited and cleaned up")
    print("RESULT: PASS - disposable Debian container runtime and local subscriptions")
    print("SCOPE: genuine sing-box runtime and upstream templates; not the full upstream installer")


if __name__ == "__main__":
    try:
        integration()
    except Exception as exc:
        print("FAIL:", type(exc).__name__, str(exc), file=sys.stderr)
        raise
