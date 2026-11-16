#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Smart network modes for owner-reviewed sing-box configs.

Dual mode uses a single :: wildcard inbound and requires both-family socket
preflight plus real core runtime tests. IPv6-only requires a concrete address:
the :: wildcard can accept IPv4-mapped traffic on Linux.
"""
from __future__ import annotations
import ipaddress

MODES = ("dual", "ipv4", "ipv6")


def resolve_listen(mode: str | None = None, address: str | None = None) -> tuple[str, str]:
    if address is not None:
        if not address or "%" in address:
            raise ValueError("Use a plain IP literal; scoped IPv6 addresses are not supported")
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Listen address must be a plain IPv4 or IPv6 literal") from exc
        if ip.version == 6 and ip.ipv4_mapped is not None:
            raise ValueError("IPv4-mapped IPv6 listen addresses are not supported")
        if mode is None:
            mode = "dual" if address == "::" else ("ipv4" if ip.version == 4 else "ipv6")
    else:
        mode = mode or "dual"
    if mode not in MODES:
        raise ValueError("network mode must be dual, ipv4 or ipv6")
    if mode == "dual":
        if address not in (None, "::"):
            raise ValueError("Dual stack requires listen ::; custom bind addresses belong to a single-family mode")
        return "dual", "::"
    if mode == "ipv4":
        if address is not None and ipaddress.ip_address(address).version != 4:
            raise ValueError("IPv4 mode requires an IPv4 listen address")
        return "ipv4", address or "0.0.0.0"
    if address is None or address == "::":
        raise ValueError("IPv6-only mode requires a concrete IPv6 address (--listen), never ::")
    ip = ipaddress.ip_address(address)
    if ip.version != 6 or ip.ipv4_mapped is not None:
        raise ValueError("IPv6-only mode requires a native IPv6 listen address")
    return "ipv6", address
