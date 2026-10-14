"""Missing DAO is MARVELL_DAO_MISSING (no live DAO scan required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon.readiness import MARVELL_DAO_MISSING, evaluate_marvell_readiness
from tests.vendor.marvell.test_marvell_dao_readiness import _cfg, _ready_samples


class MarvellMissingDaoTests(unittest.TestCase):
    def test_dao_missing(self):
        result = evaluate_marvell_readiness(
            _cfg(),
            samples=_ready_samples(dao_available=False),
        )
        self.assertEqual(result.details["readiness_status"], MARVELL_DAO_MISSING)


if __name__ == "__main__":
    unittest.main()
