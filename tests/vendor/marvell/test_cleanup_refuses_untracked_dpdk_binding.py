"""Cleanup refuses DPDK unbind when bindings are not lab_owned."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon.cleanup import UNTRACKED_DPDK_BINDING_REFUSED, plan_dao_cleanup


class UntrackedDpdkBindingTests(unittest.TestCase):
    def test_refuses_untracked_binding(self):
        result = plan_dao_cleanup({}, run_state={}, apply=False, dry_run=True)
        self.assertIn(UNTRACKED_DPDK_BINDING_REFUSED, result.details["refused"])
        self.assertEqual(result.message, UNTRACKED_DPDK_BINDING_REFUSED)
        self.assertFalse(result.executed)


if __name__ == "__main__":
    unittest.main()
