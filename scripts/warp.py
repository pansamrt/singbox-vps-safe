#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Smart opt-in WARP WireGuard endpoint for selected sing-box proxy traffic.

No Cloudflare registration, downloads, host route, DNS, firewall, SSH,
kernel interface, system WARP installation, or third-party script execution.
A user-supplied PRIVATE WireGuard .conf is required. Never log its keys.
"""
from __future__ import annotations

import argparse
import base64
import configparser
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

import state
import update

TAG = "smart-warp"
MARKER = "warp-policy.json"
FAMILIES = {"ipv4": (4,), "ipv6": (6,), "dual": (4, 6)}
ALLOWED_INBOUND_TYPES = {"vless", "hysteria2", "tuic", "anytls", "socks", "mixed", "http"}
MAX_PROFILE = 16 * 1024
MAX_CONFIG = 512 * 1024
INBOUND_TAG = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")


def private_file(path: Path, max_size: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (info.st_mode & 0o077):
        raise ValueError("Input must be a private regular file (0600; no symlinks)")
    if info.st_uid != os.geteuid() or not 0 < info.st_size <= max_size:
        raise ValueError("Unexpected input owner or file length")
    with path.open("rb") as stream:
        content = stream.read(max_size + 1)
    if len(content) > max_size:
        raise ValueError("Input exceeds size limit")
    return content


def key_bytes(value: str, field: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid base64 for " + field) from exc
    if len(decoded) != 32:
        raise ValueError("WireGuard " + field + " must decode to 32 bytes")
    return value


def profile_endpoint(value: str) -> tuple[str, int]:
    if not value or value != value.strip():
        raise ValueError("WireGuard peer endpoint must be a numeric IP:port")
    if value.startswith("["):
        match = re.fullmatch(r"\[([0-9a-fA-F:]+)\]:(\d{1,5})", value)
        if not match:
            raise ValueError("IPv6 peer endpoint must use [IPv6]:port")
        host, port_str = match.groups()
    else:
        match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", value)
        if not match:
            raise ValueError("Peer must be a literal IPv4 or bracketed IPv6 address; DNS not auto-managed")
        host, port_str = match.groups()
    addr = ipaddress.ip_address(host)
    if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local:
        raise ValueError("Peer endpoint must be a routable numeric IP")
    port = int(port_str)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid WireGuard peer port")
    return str(addr), port


def read_profile(path: Path, family: str) -> dict:
    if family not in FAMILIES:
        raise ValueError("WARP family must be ipv4, ipv6 or dual")
    text = private_file(path, MAX_PROFILE).decode("utf8")
    parser = configparser.ConfigParser(interpolation=None, strict=True,
                                       delimiters=("=",), comment_prefixes=("#", ";"))
    parser.optionxform = str
    parser.read_string(text)
    if set(parser.sections()) != {"Interface", "Peer"}:
        raise ValueError("Expected exactly one [Interface] and one [Peer] section")
    interface, peer = parser["Interface"], parser["Peer"]
    if set(interface) - {"PrivateKey", "Address", "MTU"}:
        raise ValueError("Unsupported Interface options: Scripts/DNS/routes are not imported")
    if set(peer) - {"PublicKey", "Endpoint", "AllowedIPs", "Reserved", "PersistentKeepalive"}:
        raise ValueError("Unsupported Peer options")
    private = key_bytes(interface.get("PrivateKey", ""), "private key")
    public = key_bytes(peer.get("PublicKey", ""), "peer public key")
    addresses = []
    for item in interface.get("Address", "").split(","):
        if item.strip():
            address = ipaddress.ip_interface(item.strip())
            if address.ip.version in FAMILIES[family]:
                addresses.append(str(address))
    if not all(any(ipaddress.ip_interface(a).version == version for a in addresses)
               for version in FAMILIES[family]):
        raise ValueError("Profile does not provide an interface address for requested IP family")
    allowed = []
    for item in peer.get("AllowedIPs", "").split(","):
        if item.strip():
            network = ipaddress.ip_network(item.strip(), strict=False)
            if network.version in FAMILIES[family]:
                allowed.append(str(network))
    for version in FAMILIES[family]:
        if not any(ipaddress.ip_network(net).prefixlen == 0 and
                   ipaddress.ip_network(net).version == version for net in allowed):
            raise ValueError("Requested WARP IP family requires AllowedIPs 0/0 or ::/0")
    host, port = profile_endpoint(peer.get("Endpoint", ""))
    reserved = [0, 0, 0]
    if peer.get("Reserved"):
        parts = [x.strip() for x in peer["Reserved"].split(",")]
        if len(parts) != 3 or not all(x.isdecimal() and 0 <= int(x) <= 255 for x in parts):
            raise ValueError("Reserved must contain exactly three bytes")
        reserved = list(map(int, parts))
    mtu = int(interface.get("MTU", "1280"))
    if not 1200 <= mtu <= 1500:
        raise ValueError("Unsupported WireGuard MTU")
    keepalive = int(peer.get("PersistentKeepalive", "25"))
    if not 0 <= keepalive <= 3600:
        raise ValueError("Invalid keepalive interval")
    return {
        "type": "wireguard", "tag": TAG, "system": False, "mtu": mtu,
        "address": addresses, "private_key": private,
        "peers": [{"address": host, "port": port, "public_key": public,
                   "allowed_ips": allowed, "reserved": reserved,
                   "persistent_keepalive_interval": keepalive}],
    }


def canonical(config: dict) -> str:
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(config: dict) -> str:
    return hashlib.sha256(canonical(config).encode("utf8")).hexdigest()


def load_config(layout: state.Layout) -> dict:
    data = private_file(layout.config, MAX_CONFIG)
    result = json.loads(data)
    if not isinstance(result, dict) or not isinstance(result.get("inbounds"), list):
        raise ValueError("Invalid dedicated sing-box configuration")
    return result


def rule_for(tag: str, version: int) -> dict:
    return {"inbound": [tag], "ip_version": version, "action": "route", "outbound": TAG}


def add_warp(config: dict, endpoint: dict, inbound: str, family: str) -> dict:
    if family not in FAMILIES or not INBOUND_TAG.fullmatch(inbound):
        raise ValueError("Explicit inbound tag and IPv4/IPv6 selection required")
    import copy
    result = copy.deepcopy(config)
    inbounds = result.get("inbounds")
    if not isinstance(inbounds, list) or not any(
        isinstance(item, dict) and item.get("tag") == inbound and
        item.get("type") in ALLOWED_INBOUND_TYPES for item in inbounds
    ):
        raise ValueError("Inbound tag is not an approved existing Smart proxy inbound")
    if any(item.get("type") in {"tun", "tproxy", "redirect"} and item.get("tag") == inbound
           for item in inbounds if isinstance(item, dict)):
        raise ValueError("System-redirecting inbounds cannot use optional WARP")
    outbounds = result.get("outbounds")
    if not isinstance(outbounds, list) or not any(
        isinstance(item, dict) and item.get("type") == "direct" and item.get("tag") == "direct"
        for item in outbounds
    ):
        raise ValueError("Safe direct fallback outbound must exist")
    tags = {item.get("tag") for items in (result.get("inbounds", []),
             result.get("outbounds", []), result.get("endpoints", []))
            for item in items if isinstance(item, dict)}
    if TAG in tags:
        raise ValueError("The Smart WARP tag is already in use")
    endpoints = result.setdefault("endpoints", [])
    if not isinstance(endpoints, list):
        raise ValueError("Existing endpoints must be a list")
    endpoints.append(endpoint)
    route = result.get("route")
    if not isinstance(route, dict) or route.get("final", "direct") != "direct":
        raise ValueError("Smart WARP requires an unchanged direct final route")
    rules = route.setdefault("rules", [])
    if not isinstance(rules, list):
        raise ValueError("Existing routing rules must be a list")
    route["rules"] = [rule_for(inbound, v) for v in FAMILIES[family]] + rules
    return result


def remove_warp(config: dict, inbound: str, family: str, baseline: dict) -> dict:
    """Undo ONLY known Smart-added fields; reject any manual change."""
    import copy
    data = copy.deepcopy(config)
    expected = [rule_for(inbound, v) for v in FAMILIES[family]]
    route = data.get("route")
    if not isinstance(route, dict) or route.get("rules", [])[:len(expected)] != expected:
        raise ValueError("Smart WARP routing rules were modified; refusing unsafe removal")
    route["rules"] = route["rules"][len(expected):]
    if "rules" not in baseline["route"]:
        if route["rules"]:
            raise ValueError("Unexpected routing rules")
        del route["rules"]
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) < 1:
        raise ValueError("No Smart WARP endpoint to remove")
    if endpoints[-1].get("tag") != TAG or endpoints[-1].get("system") is not False:
        raise ValueError("Smart WARP endpoint was changed or reordered")
    endpoints.pop()
    if "endpoints" not in baseline:
        if endpoints:
            raise ValueError("Unexpected extra endpoints")
        del data["endpoints"]
    if data != baseline:
        raise ValueError("WARP removal cannot reproduce original configuration; no change made")
    return data


@contextmanager
def staged_config(layout: state.Layout, config: dict):
    target = layout.config
    if target.is_symlink() or not target.is_file() or target.parent.is_symlink():
        raise RuntimeError("Refusing unsafe dedicated configuration path")
    info = target.stat()
    if info.st_mode & 0o037 or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Installed config has unsafe permissions")
    fd, name = tempfile.mkstemp(prefix=".smart-warp-", suffix=".json", dir=target.parent)
    stage = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(stage, 0o640)
        os.chown(stage, info.st_uid, info.st_gid)
        yield stage
    finally:
        stage.unlink(missing_ok=True)


def marker_path(layout: state.Layout) -> Path:
    return layout.state / MARKER


def read_marker(layout: state.Layout) -> dict:
    record = json.loads(private_file(marker_path(layout), MAX_PROFILE))
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError("Invalid WARP marker")
    if record.get("family") not in FAMILIES or not INBOUND_TAG.fullmatch(str(record.get("inbound", ""))):
        raise ValueError("Invalid WARP marker selection")
    for name in ("before", "after"):
        if not re.fullmatch(r"[a-f0-9]{64}", str(record.get(name, ""))):
            raise ValueError("Invalid marker fingerprint")
    if not state.SNAPSHOT_ID.fullmatch(str(record.get("snapshot", ""))):
        raise ValueError("Invalid WARP backup identifier")
    return record


def save_marker(layout: state.Layout, record: dict):
    path = marker_path(layout)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            json.dump(record, f, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def apply_config(layout: state.Layout, ctl: state.Systemd, new: dict,
                 backup: str, check=True) -> None:
    with staged_config(layout, new) as path:
        if check:
            ctl.check_config(layout.binary, path)
        try:
            ctl.stop()
            os.replace(path, layout.config)
            ctl.start()
        except BaseException as exc:
            try:
                state.restore_snapshot(layout, ctl, backup)
            except BaseException as rollback_error:
                raise RuntimeError(
                    "WARP transition and automatic recovery both failed; use VPS console"
                ) from rollback_error
            raise RuntimeError("WARP transition rejected; previous service restored") from exc


def ensure_owned_install(layout: state.Layout, ctl: state.Systemd):
    update.require_owned_unit(layout)
    if not ctl.is_active():
        raise RuntimeError("Smart dedicated service must be active before changing WARP routing")
    state.assert_regular(layout.config, secret=True)
    if layout.config.stat().st_uid != 0:
        raise RuntimeError("Dedicated configuration must be root-owned")


def enable(layout: state.Layout, ctl: state.Systemd, profile: Path,
           inbound: str, family: str):
    ensure_owned_install(layout, ctl)
    if marker_path(layout).exists() or marker_path(layout).is_symlink():
        raise RuntimeError("Smart WARP is already recorded; refusing overwrite")
    baseline = load_config(layout)
    endpoint = read_profile(profile, family)
    proposed = add_warp(baseline, endpoint, inbound, family)
    with staged_config(layout, proposed) as candidate:
        ctl.check_config(layout.binary, candidate)
    backup = state.create_snapshot(layout, ctl, reason="before-warp-enable")
    apply_config(layout, ctl, proposed, backup)
    record = {"schema": 1, "inbound": inbound, "family": family,
              "snapshot": backup, "before": fingerprint(baseline),
              "after": fingerprint(proposed)}
    # Persist a non-secret marker only after the new service is active.
    try:
        save_marker(layout, record)
    except BaseException:
        state.restore_snapshot(layout, ctl, backup)
        raise
    return backup


def disable(layout: state.Layout, ctl: state.Systemd):
    ensure_owned_install(layout, ctl)
    marker = read_marker(layout)
    active = load_config(layout)
    if fingerprint(active) != marker["after"]:
        raise RuntimeError("WARP config changed since enable; refuse to overwrite manual edits")
    source, _ = state.load_snapshot(layout, marker["snapshot"])
    baseline = json.loads((source / "config").read_text(encoding="utf8"))
    if fingerprint(baseline) != marker["before"]:
        raise ValueError("Archived baseline fingerprint mismatch")
    restored = remove_warp(active, marker["inbound"], marker["family"], baseline)
    with staged_config(layout, restored) as candidate:
        ctl.check_config(layout.binary, candidate)
    backup = state.create_snapshot(layout, ctl, reason="before-warp-disable")
    apply_config(layout, ctl, restored, backup)
    marker_path(layout).unlink()
    return backup


def status(layout: state.Layout):
    marker = marker_path(layout)
    if not marker.exists() and not marker.is_symlink():
        print("Smart WARP: disabled / not configured; host routing unchanged.")
        return
    record = read_marker(layout)
    try:
        active = load_config(layout)
        same = fingerprint(active) == record["after"]
    except (OSError, ValueError):
        same = False
    print("Smart WARP:", "configuration active" if same else "CONFIGURATION CHANGED; manual review required")
    print("Selected inbound:", record["inbound"])
    print("Proxy destination family:", record["family"])
    print("Original snapshot ID:", record["snapshot"])
    print("No system WARP installation, default-route changes or VPS public IP allocation.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    action = p.add_subparsers(dest="action", required=True)
    action.add_parser("status", help="inspect dedicated WARP marker, no network/system changes")
    plan = action.add_parser("plan", help="validate local profile and config without changing service")
    enable_p = action.add_parser("enable", help="backup and explicitly attach WARP to one proxy inbound")
    for parser in (plan, enable_p):
        parser.add_argument("--profile", type=Path, required=True,
                            help="private 0600 WireGuard config supplied by owner, never sent anywhere")
        parser.add_argument("--inbound", required=True, help="existing Smart inbound tag")
        parser.add_argument("--family", choices=sorted(FAMILIES), required=True,
                            help="destination IP address family that may use WARP")
    enable_p.add_argument("--confirm", action="store_true")
    disable_p = action.add_parser("disable", help="remove Smart WARP rules only, with rollback")
    disable_p.add_argument("--confirm", action="store_true")
    args = p.parse_args(argv)
    if os.geteuid() != 0:
        p.error("WARP config/profile inspection and mutations require root")
    layout, ctl = state.Layout(), state.Systemd()
    os.umask(0o077)
    try:
        if args.action == "status":
            status(layout)
            return 0
        if args.action in ("enable", "disable") and not args.confirm:
            p.error("Enable/disable requires explicit --confirm")
        with state.operation_lock(layout):
            if args.action == "plan":
                ensure_owned_install(layout, ctl)
                base = load_config(layout)
                candidate = add_warp(base, read_profile(args.profile, args.family),
                                     args.inbound, args.family)
                with staged_config(layout, candidate) as path:
                    ctl.check_config(layout.binary, path)
                print("PASS: native sing-box WARP config check; no changes made.")
                print("Scope: inbound", args.inbound, ", family", args.family,
                      "; all other proxy and host traffic stays unchanged.")
            elif args.action == "enable":
                backup = enable(layout, ctl, args.profile, args.inbound, args.family)
                print("WARP enabled on selected Smart proxy inbound. Recovery snapshot:", backup)
            else:
                backup = disable(layout, ctl)
                print("WARP removed from Smart proxy routing. Recovery snapshot:", backup)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError,
            configparser.Error, subprocess.CalledProcessError) as exc:
        # NEVER echo a profile, private key, full config or external command output.
        print("WARP action refused or recovered; inspect service status privately.", file=sys.stderr)
        print("Reason type:", type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
