"""ionic_rdma detection from lsmod / rdma link samples."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando import rdma
from plugins.amd_pensando.readiness import AMD_RDMA_AVAILABLE, evaluate_amd_readiness
from tests.vendor.harness import amd_samples

FIXTURES = ROOT / "tests" / "fixtures" / "amd_pensando"


class IonicRdmaDetectionTests(unittest.TestCase):
    def test_detects_ionic_rdma_module(self):
        det = rdma.detect_ionic_rdma(
            rdma_link=(FIXTURES / "rdma_link.txt").read_text(encoding="utf-8"),
            lsmod=(FIXTURES / "lsmod.txt").read_text(encoding="utf-8"),
        )
        self.assertTrue(det.details["detected"])

    def test_require_rdma_status(self):
        result = evaluate_amd_readiness(
            {"amd_pensando": {"require_ionic": True, "require_rdma": True}},
            samples=amd_samples(),
        )
        self.assertEqual(result.details["readiness_status"], AMD_RDMA_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
