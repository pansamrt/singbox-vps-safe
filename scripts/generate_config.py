#!/usr/bin/env python3
"""Generate reviewable sing-box 1.14.x single-inbound configuration without network access.

GPL-3.0-only; independently written for the singbox-vps-safe project.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import uuid

from network_modes import resolve_listen

DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
KEY = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


def valid_domain(value: str) -> str:
    if not DOMAIN.fullmatch(value):
        raise ValueError("Use a real DNS hostname (without scheme, port or path)")
    return value.lower()


def valid_cert_path(value: str) -> str:
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts:
        raise ValueError("TLS certificate/key path must be absolute without '..'")
    if p.parts[:2] in (("/", "root"), ("/", "home"), ("/", "tmp")):
        raise ValueError("TLS paths under /root, /home or /tmp are not supported")
    if p.is_symlink():
        raise ValueError("TLS file must not be a symlink")
    return str(p)


def reality_keypair(binary: str) -> tuple[str, str]:
    """Never use shell=True or print private keys."""
    cmd = [binary, "generate", "reality-keypair"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Could not generate Reality keypair using the selected sing-box binary") from exc
    private = re.search(r"^PrivateKey:\s*(\S+)\s*$", p.stdout, re.M)
    public = re.search(r"^PublicKey:\s*(\S+)\s*$", p.stdout, re.M)
    if not (private and public and KEY.fullmatch(private.group(1)) and KEY.fullmatch(public.group(1))):
        raise ValueError("Unexpected Reality keypair output")
    return private.group(1), public.group(1)


def build_config(protocol: str, port: int, listen: str, name: str | None,
                 cert: str | None, key: str | None, handshake: str | None,
                 binary: str = "sing-box") -> tuple[dict, dict]:
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be 1..65535")
    try:
        ipaddress.ip_address(listen)
    except ValueError as exc:
        raise ValueError("listen must be an IPv4 or IPv6 address") from exc

    inbound = {"type": protocol, "tag": protocol + "-in", "listen": listen, "listen_port": port}
    client = {"protocol": protocol, "listen_port": port}
    if protocol == "vless-reality":
        if not handshake:
            raise ValueError("--handshake-host is required for Reality")
        host = valid_domain(handshake)
        private, public = reality_keypair(binary)
        user = str(uuid.uuid4())
        short_id = secrets.token_hex(8)
        inbound.update({
            "type": "vless",
            "users": [{"name": "user1", "uuid": user, "flow": "xtls-rprx-vision"}],
            "tls": {
                "enabled": True, "server_name": host,
                "reality": {
                    "enabled": True,
                    "handshake": {"server": host, "server_port": 443},
                    "private_key": private,
                    "short_id": [short_id],
                },
            },
        })
        client.update({"uuid": user, "flow": "xtls-rprx-vision",
                       "reality_public_key": public, "reality_short_id": short_id,
                       "server_name": host})
    elif protocol in ("hysteria2", "tuic", "anytls"):
        if not (name and cert and key):
            raise ValueError("TLS protocols require --server-name, --tls-cert and --tls-key")
        hostname = valid_domain(name)
        certificate_path = valid_cert_path(cert)
        key_path = valid_cert_path(key)
        if certificate_path == key_path:
            raise ValueError("TLS certificate and key must be separate files")
        tls = {"enabled": True, "server_name": hostname,
               "certificate_path": certificate_path, "key_path": key_path}
        if protocol in ("hysteria2", "tuic"):
            tls["alpn"] = ["h3"]
        client["server_name"] = hostname
        if protocol == "hysteria2":
            password = secrets.token_urlsafe(32)
            inbound.update({"users": [{"name": "user1", "password": password}], "tls": tls})
            client["password"] = password
        elif protocol == "tuic":
            password = secrets.token_urlsafe(32)
            user = str(uuid.uuid4())
            inbound.update({"users": [{"name": "user1", "uuid": user, "password": password}],
                            "congestion_control": "cubic", "zero_rtt_handshake": False, "tls": tls})
            client.update({"uuid": user, "password": password})
        else:
            password = secrets.token_urlsafe(32)
            inbound.update({"users": [{"name": "user1", "password": password}], "tls": tls})
            client["password"] = password
    else:
        raise ValueError("Unsupported protocol")

    config = {"log": {"level": "warn"}, "inbounds": [inbound],
              "outbounds": [{"type": "direct", "tag": "direct"}],
              "route": {"final": "direct"}}
    return config, client


def private_write(path: Path, data: dict) -> None:
    """Exclusive, mode 0600: never clobber another file or follow a symlink."""
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=["vless-reality", "hysteria2", "tuic", "anytls"],
                        required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--network-mode", choices=["dual", "ipv4", "ipv6"],
                        help="dual (default), ipv4, or ipv6; IPv6-only requires --listen")
    parser.add_argument("--listen", help="optional explicit IPv4/IPv6 listen address")
    parser.add_argument("--server-name", help="TLS certificate hostname")
    parser.add_argument("--tls-cert", help="absolute server certificate path")
    parser.add_argument("--tls-key", help="absolute server private key path")
    parser.add_argument("--handshake-host", help="Reality handshake DNS hostname")
    parser.add_argument("--reality-binary", default="sing-box",
                        help="trusted installed sing-box binary for local keypair generation")
    parser.add_argument("--output", required=True, help="new server config JSON file; must not exist")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.name in ("", ".", "..") or not output.parent.is_dir():
        parser.error("Output parent directory must exist")
    client_output = output.with_name(output.name + ".client.json")
    if output.exists() or output.is_symlink() or client_output.exists() or client_output.is_symlink():
        parser.error("Output and .client.json must not already exist")
    old_umask = os.umask(0o077)
    try:
        mode, listen = resolve_listen(args.network_mode, args.listen)
        config, client = build_config(args.protocol, args.port, listen, args.server_name,
                                      args.tls_cert, args.tls_key, args.handshake_host,
                                      args.reality_binary)
        client["network_mode"] = mode
        created_output = False
        try:
            private_write(output, config)
            created_output = True
            private_write(client_output, client)
        except BaseException:
            # Never unlink a path that might have appeared before our exclusive creation.
            if created_output:
                output.unlink(missing_ok=True)
            raise
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    finally:
        os.umask(old_umask)
    print(f"Generated private files: {output} and {client_output}")
    print("No secrets were printed. Review TLS files and run sing-box check before installing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
