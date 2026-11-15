"""Read-only upstream security audit regression tests."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_upstream as audit


class AuditTests(unittest.TestCase):
    def test_upstream_identity_and_known_risks(self):
        data = audit.UPSTREAM.read_bytes()
        self.assertEqual(audit.git_blob_sha1(data), audit.PINNED_BLOB_SHA1)
        result = audit.scan_source(data.decode("utf8"))
        self.assertEqual(set(result), audit.EXPECTED)
        for key, info in result.items():
            with self.subTest(key=key):
                self.assertGreater(info["count"], 0, f"{key} no longer found, re-audit!")
                self.assertIn(info["severity"], ("critical", "high", "medium"))
                self.assertTrue(info["locations"])

    def test_detects_changes_in_inline_shell_snippet(self):
        source = ("bash <(wget -qO- https://example.org/code.sh)\n"
                  "wget --no-check-certificate https://example.org/x\n"
                  "firewall-cmd --zone=public --set-target=ACCEPT\n")
        result = audit.scan_source(source)
        self.assertEqual(result["remote_shell_execution"]["count"], 1)
        self.assertEqual(result["tls_verification_disabled"]["count"], 1)
        self.assertEqual(result["global_firewalld_accept"]["count"], 1)

    def test_project_scripts_clean(self):
        self.assertEqual(audit.verify_project_scripts(ROOT), [])

    def test_strict_audit_exits_success_with_original_unsafe_source_held_for_review(self):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/audit_upstream.py"),
                               "--strict"], check=False,
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Unmodified upstream runtime authorization: BLOCKED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
