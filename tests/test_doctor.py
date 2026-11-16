"""Read-only installation integrity doctor's file and mode checks."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import os

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "doctor.py"
spec = importlib.util.spec_from_file_location("safe_doctor", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def test_detects_symlink_and_weak_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            regular = root / "config"
            regular.write_text('{"inbounds":[]}')
            regular.chmod(0o640)
            # File ownership depends on CI runner; never change real filesystem owners.
            regular.chmod(0o666)
            result = doctor.regular_with_mode(regular, 0o640)
            self.assertTrue(result is not None)
            link = root / "link"
            link.symlink_to(regular)
            self.assertIn("not a regular", doctor.regular_with_mode(link, 0o640))

    def test_uninstalled_is_read_only_and_safe(self):
        missing = Path("/tmp/this-sb-doctor-ci-unit-must-not-exist.service")
        with patch.object(doctor, "UNIT", missing), patch.object(sys, "argv", ["doctor.py"]):
            self.assertEqual(doctor.main(), 0)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
