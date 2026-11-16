#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline export from locally generated client metadata to subscriptions.

No external APIs, templates, QR services, or cloud subscription converters.
Produces private local files suitable for scripts/local_subscriptions.py.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from urllib.parse import quote, urlencode

SUPPORTED = {"vless-reality", "hysteria2", "tuic", "anytls"}
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")


def host_validate(raw: str) -> tuple[str, str]:
    if not raw or any(ch in raw for ch in "/?#@:% \\\"'"):
        # Colons are legal only in a plain IPv6 literal, handled separately below.
        try:
            ip = ipaddress.ip_address(raw)
            if ip.version == 6:
                return raw, "[" + raw + "]"
        except ValueError:
            pass
        raise ValueError("Server must be a plain IP address or DNS hostname, without URL syntax")
    try:
        ip = ipaddress.ip_address(raw)
        return raw, raw if ip.version == 4 else "[" + raw + "]"
    except ValueError:
        if not NAME_PATTERN.fullmatch(raw) or raw.startswith(".") or raw.endswith("."):
            raise ValueError("Invalid server hostname")
        return raw.lower(), raw.lower()


def read_private_meta(path: Path) -> list[dict]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
        raise ValueError("Client metadata must be a private, regular file <=1 MiB")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("Client metadata must be JSON object")
    clients = doc.get("clients", [doc])
    if not isinstance(clients, list) or not 1 <= len(clients) <= 8:
        raise ValueError("Expect 1..8 client entries")
    for c in clients:
        if not isinstance(c, dict) or c.get("protocol") not in SUPPORTED:
            raise ValueError("Unsupported client protocol")
        if type(c.get("listen_port")) is not int or not 1 <= c["listen_port"] <= 65535:
            raise ValueError("Invalid client listening port")
    return clients


def outbound(c: dict, server: str, index: int) -> dict:
    kind = c["protocol"]
    name = "node-" + str(index) + "-" + kind
    tls = {"enabled": True, "server_name": c["server_name"]}
    if kind == "vless-reality":
        tls["utls"] = {"enabled": True, "fingerprint": "chrome"}
        tls["reality"] = {
            "enabled": True,
            "public_key": c["reality_public_key"],
            "short_id": c["reality_short_id"],
        }
        return {
            "type": "vless", "tag": name, "server": server, "server_port": c["listen_port"],
            "uuid": c["uuid"], "flow": "xtls-rprx-vision", "tls": tls
        }
    if kind == "hysteria2":
        return {"type": "hysteria2", "tag": name, "server": server,
                "server_port": c["listen_port"], "password": c["password"],
                "tls": tls}
    if kind == "tuic":
        return {"type": "tuic", "tag": name, "server": server,
                "server_port": c["listen_port"], "uuid": c["uuid"],
                "password": c["password"], "congestion_control": "cubic",
                "tls": dict(tls, alpn=["h3"])}
    return {"type": "anytls", "tag": name, "server": server,
            "server_port": c["listen_port"], "password": c["password"],
            "tls": tls}


def share_url(c: dict, host: str, index: int) -> str:
    kind = c["protocol"]
    port = c["listen_port"]
    tag = quote("safe-" + kind + "-" + str(index), safe="")
    if kind == "vless-reality":
        query = urlencode({
            "encryption": "none", "security": "reality", "type": "tcp",
            "flow": "xtls-rprx-vision", "sni": c["server_name"], "fp": "chrome",
            "pbk": c["reality_public_key"], "sid": c["reality_short_id"],
        })
        return "vless://" + quote(c["uuid"], safe="") + "@" + host + ":" + str(port) + "?" + query + "#" + tag
    if kind == "hysteria2":
        return "hysteria2://" + quote(c["password"], safe="") + "@" + host + ":" + str(port) + "?sni=" + quote(c["server_name"]) + "#" + tag
    if kind == "tuic":
        query = urlencode({"sni": c["server_name"], "alpn": "h3", "congestion_control": "cubic"})
        return "tuic://" + quote(c["uuid"], safe="") + ":" + quote(c["password"], safe="") + "@" + host + ":" + str(port) + "?" + query + "#" + tag
    return "anytls://" + quote(c["password"], safe="") + "@" + host + ":" + str(port) + "?sni=" + quote(c["server_name"]) + "#" + tag


def clash_proxy(c: dict, server: str, index: int) -> dict | None:
    kind = c["protocol"]
    name = "node-" + str(index) + "-" + kind
    base = {"name": name, "server": server, "port": c["listen_port"]}
    if kind == "vless-reality":
        return dict(base, **{
            "type": "vless", "uuid": c["uuid"], "network": "tcp",
            "tls": True, "udp": True, "flow": "xtls-rprx-vision",
            "servername": c["server_name"], "client-fingerprint": "chrome",
            "reality-opts": {"public-key": c["reality_public_key"], "short-id": c["reality_short_id"]},
        })
    if kind == "hysteria2":
        return dict(base, **{"type": "hysteria2", "password": c["password"],
                             "sni": c["server_name"], "skip-cert-verify": False, "alpn": ["h3"]})
    if kind == "tuic":
        return dict(base, **{"type": "tuic", "uuid": c["uuid"], "password": c["password"],
                             "sni": c["server_name"], "alpn": ["h3"],
                             "congestion-controller": "cubic", "skip-cert-verify": False})
    # AnyTLS Mihomo support is not assumed until client compatibility tests.
    return None


def build_outputs(clients: list[dict], server: str) -> dict[str, bytes]:
    host, url_host = host_validate(server)
    outs = [outbound(c, host, i + 1) for i, c in enumerate(clients)]
    links = [share_url(c, url_host, i + 1) for i, c in enumerate(clients)]
    proxies = [p for i, c in enumerate(clients) if (p := clash_proxy(c, host, i + 1))]
    singbox = {
        "log": {"level": "warn"},
        "outbounds": outs + [{"type": "direct", "tag": "direct"}],
        "route": {"final": outs[0]["tag"]},
    }
    files = {
        "sing-box": (json.dumps(singbox, indent=2) + "\n").encode(),
        "v2rayn": ("\n".join(links) + "\n").encode(),
        "shadowrocket": base64.b64encode(("\n".join(links) + "\n").encode()),
        "throne": ("\n".join(links) + "\n").encode(),
    }
    if proxies:
        taglist = [p["name"] for p in proxies]
        clash = {
            "mixed-port": 7890, "mode": "rule",
            "proxies": proxies,
            "proxy-groups": [{"name": "AUTO", "type": "select", "proxies": taglist + ["DIRECT"]}],
            "rules": ["MATCH,AUTO"],
        }
        files["clash"] = (json.dumps(clash, indent=2) + "\n").encode()
        files["clash2"] = files["clash"]
        files["proxies"] = (json.dumps({"proxies": proxies}, indent=2) + "\n").encode()
    return files


def write_outputs(parent: Path, files: dict[str, bytes]) -> Path:
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("Output parent must be a real existing directory")
    dest = parent / ("client-subscriptions-" + os.urandom(8).hex())
    dest.mkdir(mode=0o700)
    try:
        for name, content in files.items():
            path = dest / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
        return dest
    except BaseException:
        shutil.rmtree(dest)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--client-meta", type=Path,
                   default=Path("/var/lib/singbox-vps-safe/client.json"))
    p.add_argument("--server", required=True, help="public host or IP of your VPS (no scheme)")
    p.add_argument("--output-parent", type=Path, default=Path("/var/lib/singbox-vps-safe"))
    args = p.parse_args(argv)
    os.umask(0o077)
    try:
        outputs = build_outputs(read_private_meta(args.client_meta), args.server)
        result = write_outputs(args.output_parent, outputs)
        print("Private subscription source directory:", result)
        print("Formats:", ", ".join(sorted(outputs)))
        print("For publication, call: python3 scripts/local_subscriptions.py publish --source-dir " + str(result))
        print("Secrets were written to local private files only, not stdout.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("ERROR: cannot export subscriptions:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
