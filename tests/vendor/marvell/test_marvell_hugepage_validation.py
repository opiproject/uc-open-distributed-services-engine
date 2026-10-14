"""Hugepage parsing and MARVELL_HUGEPAGES_MISSING (meminfo samples only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon.readiness import (
    MARVELL_HUGEPAGES_MISSING,
    evaluate_marvell_readiness,
    parse_hugepages,
)
from tests.vendor.marvell.test_marvell_dao_readiness import _cfg, _ready_samples


class MarvellHugepageTests(unittest.TestCase):
    def test_parse_hugepages(self):
        parsed = parse_hugepages("HugePages_Total: 512\nHugePages_Free: 256\n")
        self.assertEqual(parsed["HugePages_Total"], 512)
        self.assertEqual(parsed["HugePages_Free"], 256)

    def test_zero_hugepages_missing(self):
        result = evaluate_marvell_readiness(
            _cfg(),
            samples=_ready_samples(meminfo="HugePages_Total: 0\nHugePages_Free: 0\n"),
        )
        self.assertEqual(result.details["readiness_status"], MARVELL_HUGEPAGES_MISSING)

    def test_unknown_meminfo_missing(self):
        samples = _ready_samples()
        samples.pop("meminfo")
        result = evaluate_marvell_readiness(_cfg(), samples=samples)
        self.assertEqual(result.details["readiness_status"], MARVELL_HUGEPAGES_MISSING)


if __name__ == "__main__":
    unittest.main()
