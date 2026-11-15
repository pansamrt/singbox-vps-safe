#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Reproducible, conservative audit of the pinned fscarmen installer source.

Read-only. An acknowledged issue never becomes permission to execute upstream.
Exit nonzero if upstream source changes, required risk classes vanish unexpectedly,
or known high-risk patterns appear in project-owned executable code.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "upstream/fscarmen/sing-box.sh"
PINNED_BLOB_SHA1 = "89ec86fbf37198c1ae3a865a63a1b83bd6178cae"

# These regexes are triage signals, not a complete shell parser or proof of exploit.
RULES = {
    "remote_shell_execution": {
        "severity": "critical",
        "pattern": r"bash\s*<\s*\(\s*wget|bash\s*<\s*\(\s*curl",
        "explanation": "Downloads and executes remote code as part of installer/tool workflows",
    },
    "tls_verification_disabled": {
        "severity": "critical",
        "pattern": r"--no-check-certificate|--insecure",
        "explanation": "TLS verification disabled on download/API paths",
    },
    "reality_secret_in_remote_url": {
        "severity": "critical",
        "pattern": r"https://realitykey\.cloudflare\.now\.cc/\?privateKey=",
        "explanation": "Potential remote transmission of Reality private key in an HTTP query URL",
    },
    "global_firewalld_accept": {
        "severity": "critical",
        "pattern": r"firewall-cmd.*--set-target=ACCEPT",
        "explanation": "Changes global firewalld public zone target to ACCEPT",
    },
    "firewall_nat_modification": {
        "severity": "high",
        "pattern": r"\b(?:ip6?tables\b.*\bPREROUTING\b|firewall-cmd\b.*(?:--add-forward-port|--add-masquerade))",
        "explanation": "Changes firewall NAT or forwarding state",
    },
    "ufw_system_file_edit": {
        "severity": "high",
        "pattern": r"/etc/ufw/before(?:6)?\.rules",
        "explanation": "Operates on shared UFW system rule files",
    },
    "public_http_subscriptions": {
        "severity": "high",
        "pattern": r'SUBSCRIBE_ADDRESS="http://',
        "explanation": "Builds unencrypted public-IP subscription URLs",
    },
    "nginx_regex_capture_alias": {
        "severity": "high",
        "pattern": r"location\s+~\s+\^/\$\{UUID_CONFIRM\}/\(\.\*\)",
        "explanation": "Subscription Nginx route accepts broad regex capture for alias",
    },
    "nginx_autoindex": {
        "severity": "high",
        "pattern": r"\bautoindex\s+on;",
        "explanation": "Enables directory listing in subscription route",
    },
    "nginx_root_worker": {
        "severity": "high",
        "pattern": r'NGINX_CONF="user\s+root;',
        "explanation": "Upstream generated Nginx worker user set to root",
    },
    "broad_uninstall": {
        "severity": "high",
        "pattern": r"\brm\s+-rf\s+\$\{WORK_DIR\}.*\$\{SINGBOX_DAEMON_FILE\}",
        "explanation": "Uninstall deletes shared configuration/service paths",
    },
    "remote_qr_url": {
        "severity": "medium",
        "pattern": r"https://api\.qrserver\.com/v1/create-qr-code/",
        "explanation": "Third-party QR generation URL embeds private subscription URL",
    },
}
# Independent markers of the expected upstream source, checked with SHA-1 pin.
# No hard-coded exact occurrence counts to avoid false confidence from comments.
EXPECTED = set(RULES)


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def scan_source(text: str) -> dict[str, dict]:
    lines = text.splitlines()
    found = {}
    for key, rule in RULES.items():
        rx = re.compile(rule["pattern"], re.IGNORECASE)
        matches = [{"line": i, "excerpt": line.strip()[:180]}
                   for i, line in enumerate(lines, start=1) if rx.search(line)]
        found[key] = {
            "severity": rule["severity"],
            "description": rule["explanation"],
            "count": len(matches),
            "locations": matches[:30],
        }
    return found


def verify_project_scripts(root: Path):
    """No known destructive or remote-root execution in first-party scripts."""
    forbidden = (
        re.compile(r"(?:curl|wget)[^\n]*\|\s*(?:bash|sh)\b"),
        re.compile(r"\bbash\s*<\s*\(\s*(?:curl|wget)\b"),
        re.compile(r"--no-check-certificate|--insecure"),
        re.compile(r"\biptables\b.*(?:-F\b|-P\b)"),
        re.compile(r"\bufw\s+disable\b"),
        re.compile(r"\bfirewall-cmd\b.*--set-target=ACCEPT"),
    )
    violations = []
    for path in (root / "scripts").rglob("*"):
        if path.is_file() and path.suffix in (".py", ".sh"):
            contents = path.read_text(encoding="utf8")
            for index, line in enumerate(contents.splitlines(), start=1):
                # Comments and references in string literals can be false
                # positives; fail closed and require explicit review.
                for rule in forbidden:
                    if rule.search(line):
                        violations.append(f"{path.relative_to(root)}:{index}: {rule.pattern}")
    return violations


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, help="Optional local JSON report location")
    p.add_argument("--strict", action="store_true",
                   help="Require fixed source and all known risk classes")
    args = p.parse_args()
    if not UPSTREAM.is_file():
        print("Missing archived upstream source", file=sys.stderr)
        return 2
    data = UPSTREAM.read_bytes()
    source_sha = git_blob_sha1(data)
    findings = scan_source(data.decode("utf8"))
    vanished = sorted(k for k in EXPECTED if not findings[k]["count"])
    violations = verify_project_scripts(ROOT)
    report = {
        "source": "fscarmen/sing-box",
        "commit": "7a666fc870d027790b6b831d219d8d99f13a73a4",
        "source_git_blob": source_sha,
        "source_pinned": source_sha == PINNED_BLOB_SHA1,
        "upstream_executable": False,
        "risk_findings": findings,
        "missing_expected_signatures": vanished,
        "project_script_violations": violations,
        "policy": "Do not execute unmodified upstream installer",
    }
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Source pin verified: {report['source_pinned']}")
    print("Unmodified upstream runtime authorization: BLOCKED")
    for name, entry in findings.items():
        print(f"  {entry['severity'].upper():8s} {name:32s}: {entry['count']:3d} occurrences")
    if vanished:
        print("Expected markers missing: " + ", ".join(vanished), file=sys.stderr)
    if violations:
        print("Potential project-owned hazards:\n" + "\n".join(violations),
              file=sys.stderr)
    if args.strict and (not report["source_pinned"] or vanished or violations):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
