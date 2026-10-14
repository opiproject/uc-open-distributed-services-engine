"""DAO evidence never invents counters or offloaded:yes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon.dao_telemetry import collect_dao_evidence
from tests.vendor.marvell.test_marvell_dao_readiness import _cfg, _ready_samples


class DaoEvidenceTests(unittest.TestCase):
    def test_missing_dao_leaves_nulls(self):
        result = collect_dao_evidence(_cfg(), samples=_ready_samples(dao_available=False))
        evidence = result.details["marvell_dao_evidence"]
        self.assertFalse(evidence["dao_available"])
        self.assertIsNone(evidence["dao_running"])
        self.assertIsNone(evidence["flow_offload_indicators"])
        self.assertEqual(result.details["offload_state"], None)
        self.assertEqual(evidence["confidence"], "unknown")

    def test_indicators_are_not_offloaded_yes(self):
        result = collect_dao_evidence(
            _cfg(),
            samples=_ready_samples(
                dao_running=True,
                flow_offload_indicators={"hint": "hw"},
            ),
        )
        evidence = result.details["marvell_dao_evidence"]
        self.assertEqual(evidence["confidence"], "low")
        self.assertIsNone(result.details["offload_state"])


if __name__ == "__main__":
    unittest.main()
