"""Representors stay undiscovered unless samples prove them."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando.readiness import (
    AMD_REPRESENTORS_NOT_DISCOVERED,
    evaluate_amd_readiness,
)
from tests.vendor.harness import amd_samples


class AmdRepresentorDiscoveryRequiredTests(unittest.TestCase):
    def test_related_status_without_require_flag(self):
        result = evaluate_amd_readiness(samples=amd_samples())
        self.assertIn(AMD_REPRESENTORS_NOT_DISCOVERED, result.details["related_statuses"])

    def test_require_representors_blocks(self):
        result = evaluate_amd_readiness(
            {"amd_pensando": {"require_ionic": True, "require_representors": True}},
            samples=amd_samples(),
        )
        self.assertEqual(result.details["readiness_status"], AMD_REPRESENTORS_NOT_DISCOVERED)


if __name__ == "__main__":
    unittest.main()
