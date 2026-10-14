"""Marvell DAO cleanup is plan-only and lab-owned."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon.cleanup import plan_dao_cleanup


class DaoCleanupPlanTests(unittest.TestCase):
    def test_plan_is_dry_run_and_not_executed(self):
        result = plan_dao_cleanup(
            {"ovs": {"bridge": "br-int", "lab_owned": True}, "dao": {"stop_on_cleanup": True}},
            run_state={"lab_owned_dpdk_bindings": ["0000:05:00.2"]},
            apply=False,
            dry_run=True,
        )
        self.assertFalse(result.executed)
        self.assertTrue(result.destructive)
        self.assertTrue(result.details["lab_owned_bridge"])
        self.assertIn("0000:05:00.2", result.details["tracked_bindings"])
        self.assertTrue(all(not c.executed for c in result.commands))


if __name__ == "__main__":
    unittest.main()
