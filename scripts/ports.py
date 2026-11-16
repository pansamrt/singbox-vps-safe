#!/usr/bin/env python3
"""Read-only TCP/UDP port preflight for reviewed sing-box server configurations.

GPL-3.0-only. Bind tests create transient sockets and close them immediately;
no firewall, iptables, SSH, DNS, service or routing changes are made.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import sys

TCP = {"vless", "anytls"}
UDP = {"hysteria2", "tuic"}
MAX_CONFIG_SIZE = 2 * 1024 * 1024


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_config(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Config must be a regular file, not a symlink")
    if info.st_mode & 0o077:
        raise ValueError("Config contains secrets: restrict access to mode 0600")
    if info.st_size > MAX_CONFIG_SIZE:
        raise ValueError("Configuration exceeds 2 MiB limit")
    with path.open(encoding="utf-8") as f:
        config = json.load(f, object_pairs_hook=reject_duplicate_keys)
    if not isinstance(config, dict) or not isinstance(config.get("inbounds"), list):
        raise ValueError("Expected a sing-box config object with inbounds")
    return config


def bindings(config: dict) -> list[tuple[str, str, int]]:
    seen = set()
    result = []
    for inbound in config.get("inbounds", []):
        if not isinstance(inbound, dict):
            raise ValueError("Invalid inbound object")
        kind = inbound.get("type")
        if kind in TCP:
            protocol = "TCP"
        elif kind in UDP:
            protocol = "UDP"
        else:
            raise ValueError(f"Unreviewed inbound type: {kind}")
        port = inbound.get("listen_port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid listen_port")
        listen = inbound.get("listen")
        try:
            ipaddress.ip_address(listen)
        except (TypeError, ValueError) as exc:
            raise ValueError("listen must be a literal IPv4 or IPv6 address") from exc
        # Conservative policy: do not configure duplicate same-transport ports
        # even across different IP addresses or protocol types.
        key = (protocol, port)
        if key in seen:
            raise ValueError(f"Duplicate {protocol} port {port}; fix config before install")
        seen.add(key)
        result.append((protocol, listen, port))
    if not result:
        raise ValueError("No inbound sockets specified")
    return result


def probe(binding: tuple[str, str, int]) -> None:
    protocol, listen, port = binding
    ip = ipaddress.ip_address(listen)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    sock_type = socket.SOCK_STREAM if protocol == "TCP" else socket.SOCK_DGRAM
    # An IPv6 wildcard is Smart's dual-stack mode, not IPv6-only.
    # Check IPv4 separately so a v6-only host setting cannot hide an occupied
    # IPv4 socket. Then demand an IPv6 wildcard socket with v4-mapped support.
    if listen == "::":
        with socket.socket(socket.AF_INET, sock_type) as v4:
            v4.bind(("0.0.0.0", port))
        with socket.socket(socket.AF_INET6, sock_type) as dual:
            dual.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            if dual.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) != 0:
                raise OSError("IPv6 wildcard cannot accept IPv4-mapped connections")
            dual.bind(("::", port))
        return
    with socket.socket(family, sock_type) as sock:
        # For concrete IPv6 addresses, assert IPv6-only transport binding.
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        # Do not set SO_REUSEPORT / SO_REUSEADDR: conservative conflict check.
        sock.bind((listen, port))


def preflight(config: dict) -> list[tuple[str, str, int]]:
    desired = bindings(config)
    for binding in desired:
        protocol, listen, port = binding
        try:
            probe(binding)
        except OSError as exc:
            raise ValueError(
                f"Cannot bind {protocol} {listen}:{port} ({exc}); "
                "IPv4/IPv6 port occupied, address unavailable, or insufficient privileges"
            ) from exc
    return desired


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    args = p.parse_args()
    try:
        for protocol, listen, port in preflight(read_config(Path(args.config))):
            print(f"Preflight OK: {protocol} {listen}:{port} (transient test only)")
        print("WARNING: This is best-effort; a port may become occupied before service start.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PORT PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
