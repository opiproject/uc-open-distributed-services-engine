"""Missing OPI endpoint is INTEL_OPI_ENDPOINT_REQUIRED."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.intel_ipu.readiness import INTEL_OPI_ENDPOINT_REQUIRED, evaluate_intel_readiness


class IntelMissingOpiTests(unittest.TestCase):
    def test_missing_opi_endpoint(self):
        result = evaluate_intel_readiness(
            {"intel": {"require_opi": True}, "opi": {"endpoint": None}},
            samples={"lspci": None},
        )
        self.assertEqual(result.details["readiness_status"], INTEL_OPI_ENDPOINT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
