"""ionic interface detection from ethtool -i samples."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando import ionic
from plugins.amd_pensando.readiness import evaluate_amd_readiness
from tests.vendor.harness import amd_samples

FIXTURES = ROOT / "tests" / "fixtures" / "amd_pensando"


class IonicDetectionTests(unittest.TestCase):
    def test_lists_ionic_interface(self):
        raw = (FIXTURES / "ethtool_i.txt").read_text(encoding="utf-8")
        self.assertEqual(ionic.list_ionic_interfaces({"eth0": raw}), ["eth0"])
        self.assertTrue(ionic.is_ionic_ethtool(raw))

    def test_readiness_records_ionic_interfaces(self):
        result = evaluate_amd_readiness(samples=amd_samples())
        self.assertIn("eth0", result.details["checks"]["ionic_interfaces"])


if __name__ == "__main__":
    unittest.main()
