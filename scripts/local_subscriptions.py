#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Smart local subscription publisher. No external conversion or QR APIs.

Only binds IPv4/IPv6 loopback. Remote clients require an owner-configured HTTPS reverse
proxy. No firewall, SSH, nginx, services, or sing-box settings are modified.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager, ExitStack
import fcntl
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import shutil
import stat
import sys
from urllib.parse import urlsplit
import uuid

ROOT = Path("/var/lib/singbox-vps-safe/local-subscriptions")
SOURCE = Path("/etc/sing-box/subscribe")
ALLOWED = frozenset({"v2rayn", "throne", "shadowrocket", "clash",
                     "clash2", "proxies", "sing-box", "sing-box-pc",
                     "sing-box-phone", "sing-box2", "neko"})
NAMES = ALLOWED | {"auto", "auto2"}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,128}$")
PATH_RE = re.compile(r"^/([A-Za-z0-9_-]{40,128})/([A-Za-z0-9-]+)$")
GEN_RE = re.compile(r"^gen-[a-f0-9]{32}$")
MAX_FILE = 2 * 1024 * 1024


def state_dir(path: Path, create=False):
    if path.is_symlink():
        raise ValueError("State symlink refused")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError("Invalid state directory/owner")
    if info.st_mode & 0o077:
        raise ValueError("State directory must have 0700 permissions")
    return path


@contextmanager
def exclusive_writer(root: Path):
    """Prevent simultaneous init/publish/rotate operations."""
    state_dir(root, create=True)
    lock = root / ".writer.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("Unsafe subscription writer lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another subscription write operation is in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def private_json(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_FILE:
        raise ValueError("Unsafe private configuration")
    with path.open(encoding="utf8") as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    return result


def atomic_json(path: Path, obj: dict):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Unsafe destination")
    tmp = path.with_name("." + path.name + "-" + secrets.token_hex(8))
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as out:
            json.dump(obj, out, ensure_ascii=False, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def valid_base(value: str | None, port: int):
    if not 1 <= port <= 65535:
        raise ValueError("Invalid listen port")
    if value is None:
        return "http://127.0.0.1:" + str(port)
    p = urlsplit(value)
    if (not p.hostname or p.username or p.password or p.query or p.fragment or
            p.path not in ("", "/") or any(ch.isspace() for ch in value) or
            "\\" in value or "%" in p.netloc or "@" in p.netloc):
        raise ValueError("Public base URL must be a valid URL without credentials, query or path")
    # Accessing .port deliberately validates port syntax.
    if p.port is not None and not 1 <= p.port <= 65535:
        raise ValueError("Invalid URL port")
    if p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost", "::1"):
        if p.port != port:
            raise ValueError("Loopback URL must match local listen port")
    elif p.scheme != "https" or p.hostname in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Public subscription URLs require HTTPS")
    return value.rstrip("/")


def settings(root: Path):
    state_dir(root)
    data = private_json(root / "access.json")
    if data.get("schema") != 1 or not TOKEN_RE.fullmatch(str(data.get("token", ""))):
        raise ValueError("Invalid token")
    port = data.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    if data.get("base") != valid_base(data.get("base"), port):
        raise ValueError("Invalid URL")
    if data.get("loopback_mode", "ipv4") not in ("ipv4", "ipv6", "dual"):
        raise ValueError("Invalid local subscription loopback mode")
    gen = data.get("generation")
    if gen is not None and (not isinstance(gen, str) or not GEN_RE.fullmatch(gen)):
        raise ValueError("Invalid generation")
    return data


def init(root: Path, port: int, public_url: str | None,
         loopback_mode: str = "dual"):
    if loopback_mode not in ("ipv4", "ipv6", "dual"):
        raise ValueError("Invalid loopback mode")
    local_url = ("http://[::1]:" + str(port)) if loopback_mode == "ipv6" else None
    chosen_base = public_url if public_url is not None else local_url
    checked_base = valid_base(chosen_base, port)
    state_dir(root, create=True)
    cfg = root / "access.json"
    if cfg.exists() or cfg.is_symlink():
        raise ValueError("Already initialized: use rotate")
    atomic_json(cfg, {"schema": 1, "token": secrets.token_urlsafe(36),
                      "port": port, "base": checked_base,
                      "loopback_mode": loopback_mode, "generation": None})


def rotate(root: Path, public_url: str | None = None):
    """Rotate token and, if requested, change HTTPS public origin atomically."""
    cfg = settings(root)
    old_prefix = cfg["base"] + "/" + cfg["token"] + "/proxies"
    if public_url is not None:
        cfg["base"] = valid_base(public_url, cfg["port"])
    cfg["token"] = secrets.token_urlsafe(36)
    old_gen = cfg["generation"]
    if old_gen is None:
        atomic_json(root / "access.json", cfg)
        return
    original = root / old_gen
    if original.is_symlink() or not original.is_dir():
        raise ValueError("Invalid existing generation")
    new_gen = "gen-" + uuid.uuid4().hex
    dest = root / new_gen
    dest.mkdir(mode=0o700)
    try:
        for name in sorted(ALLOWED):
            path = original / name
            if not path.exists() and not path.is_symlink():
                continue
            payload = read_file(path)
            if name in ("clash", "clash2"):
                payload = payload.replace(old_prefix.encode(),
                    (cfg["base"] + "/" + cfg["token"] + "/proxies").encode())
            fd = os.open(dest / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(payload)
        cfg["generation"] = new_gen
        atomic_json(root / "access.json", cfg)
    except BaseException:
        shutil.rmtree(dest)
        raise


def read_file(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
        raise ValueError("Source is not a safe regular file under 2 MiB")
    with path.open("rb") as f:
        data = f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("Too much input")
    return data


def fix_clash_provider(payload: bytes, url: str):
    text = payload.decode("utf8")
    # Replace only YAML URL entries ending in /proxies, not remote rule sets.
    regex = re.compile(r'(?m)^(\s*url:\s*)(["\x27]?)(https?://[^\s"\x27]+/proxies)(["\x27]?)\s*$')
    def sub(match):
        if match.group(2) != match.group(4):
            raise ValueError("Malformed YAML provider quotation")
        return match.group(1) + match.group(2) + url + match.group(4)
    return regex.sub(sub, text).encode("utf8")


def publish(root: Path, source: Path):
    cfg = settings(root)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Invalid Smart subscription source directory")
    generation = "gen-" + uuid.uuid4().hex
    folder = root / generation
    folder.mkdir(mode=0o700)
    count = 0
    try:
        for name in sorted(ALLOWED):
            entry = source / name
            if not entry.exists() and not entry.is_symlink():
                continue
            data = read_file(entry)
            if name in ("clash", "clash2"):
                data = fix_clash_provider(data, cfg["base"] + "/" + cfg["token"] + "/proxies")
            out = folder / name
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            count += 1
        if not count:
            raise ValueError("No Smart subscription files found")
        cfg["generation"] = generation
        atomic_json(root / "access.json", cfg)
    except BaseException:
        shutil.rmtree(folder)
        raise
    return count


def auto_file(agent: str, alternate: bool, available: set[str]):
    agent = agent.lower()
    if "shadowrocket" in agent:
        choices = ("shadowrocket", "v2rayn")
    elif "clash" in agent or "mihomo" in agent:
        choices = ("clash2" if alternate else "clash", "clash", "proxies")
    elif any(k in agent for k in ("sing-box", "singbox", "sfi", "sfa", "sfm")):
        choices = ("sing-box2" if alternate else "sing-box", "sing-box", "sing-box-phone")
    elif "throne" in agent:
        choices = ("throne", "v2rayn")
    else:
        choices = ("v2rayn", "shadowrocket", "sing-box")
    return next((name for name in choices if name in available), None)


class Handler(BaseHTTPRequestHandler):
    root = ROOT
    server_version = "LocalSubscription"
    sys_version = ""

    def log_message(self, fmt, *args):
        # URLs contain bearer tokens. Never print the request path in logs.
        return

    def do_HEAD(self):
        self.respond(False)

    def do_GET(self):
        self.respond(True)

    def respond(self, body: bool):
        try:
            cfg = settings(self.root)
            parsed = urlsplit(self.path)
            match = PATH_RE.fullmatch(parsed.path)
            if parsed.query or parsed.fragment or "%" in parsed.path or not match:
                self.send_error(404); return
            if not hmac.compare_digest(match.group(1), cfg["token"]):
                self.send_error(404); return
            name = match.group(2)
            if name not in NAMES or cfg["generation"] is None:
                self.send_error(404); return
            folder = self.root / cfg["generation"]
            if folder.is_symlink() or not folder.is_dir():
                self.send_error(404); return
            available = {x for x in ALLOWED if (folder / x).is_file() and not (folder / x).is_symlink()}
            if name in ("auto", "auto2"):
                name = auto_file(self.headers.get("User-Agent", ""), name == "auto2", available)
            if name not in available:
                self.send_error(404); return
            payload = read_file(folder / name)
            mime = "application/json" if name.startswith("sing-box") else (
                "text/yaml" if name in ("clash", "clash2", "proxies") else "text/plain"
            )
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.end_headers()
            if body:
                self.wfile.write(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            self.send_error(404)


def urls(root: Path):
    cfg = settings(root)
    prefix = cfg["base"] + "/" + cfg["token"]
    print("PRIVATE URLs - whoever has a URL can access the node credentials:")
    for fmt in ("auto", "auto2", "v2rayn", "clash", "clash2", "proxies",
                "sing-box", "shadowrocket", "throne"):
        print(fmt + ": " + prefix + "/" + fmt)
    if cfg["base"].startswith("http://"):
        print("Loopback addresses work only on the VPS. For remote clients use your own HTTPS reverse proxy.")


class IPv6LoopbackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def serve_loopbacks(root: Path):
    record = settings(root)
    port = record["port"]
    mode = record.get("loopback_mode", "ipv4")
    handler = type("BoundHandler", (Handler,), {"root": root})
    with ExitStack() as stack:
        servers = []
        if mode in ("ipv4", "dual"):
            servers.append(stack.enter_context(ThreadingHTTPServer(("127.0.0.1", port), handler)))
        if mode in ("ipv6", "dual"):
            servers.append(stack.enter_context(IPv6LoopbackServer(("::1", port), handler)))
        for server in servers:
            server.daemon_threads = True
        workers = []
        for server in servers[:-1]:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            workers.append((server, thread))
        print("Local subscription service only on: " +
              ", ".join(("127.0.0.1" if srv.address_family == socket.AF_INET else "::1")
                        + ":" + str(port) for srv in servers), flush=True)
        try:
            servers[-1].serve_forever()
        finally:
            for server, thread in workers:
                server.shutdown()
                thread.join(timeout=5)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, default=ROOT)
    sub = p.add_subparsers(dest="action", required=True)
    init_cmd = sub.add_parser("init")
    init_cmd.add_argument("--port", type=int, default=8787)
    init_cmd.add_argument("--loopback-mode", choices=["dual", "ipv4", "ipv6"], default="dual",
                          help="dual loopback default, or one IP family only")
    init_cmd.add_argument("--public-base-url")
    pub = sub.add_parser("publish")
    pub.add_argument("--source-dir", type=Path, default=SOURCE)
    rotating = sub.add_parser("rotate")
    rotating.add_argument("--public-base-url", help="optional HTTPS URL; rotates token as well")
    for action in ("urls", "serve"):
        sub.add_parser(action)
    args = p.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "init":
            with exclusive_writer(args.state_dir):
                init(args.state_dir, args.port, args.public_base_url, args.loopback_mode)
            print("Initialized. Run 'urls' privately to view subscription addresses.")
        elif args.action == "publish":
            with exclusive_writer(args.state_dir):
                count = publish(args.state_dir, args.source_dir)
            print("Local subscription files published:", count)
        elif args.action == "rotate":
            with exclusive_writer(args.state_dir):
                rotate(args.state_dir, args.public_base_url)
            print("Subscription URL rotated; previous URL revoked.")
        elif args.action == "urls":
            urls(args.state_dir)
        else:
            serve_loopbacks(args.state_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
