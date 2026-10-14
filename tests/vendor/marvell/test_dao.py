"""Marvell DAO placeholders (no DAO binary, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon import dao_ovs, discovery
from tests.vendor.harness import marvell_samples, row_from_discovery


class MarvellDaoFixtureTests(unittest.TestCase):
    def test_dao_from_env_not_cli(self):
        ok = dao_ovs.check_dao_available(
            environ={"DAO_HOME": "/opt/dao"}, command_exists_fn=lambda _n: False
        )
        self.assertTrue(ok.details["available"])
        missing = dao_ovs.check_dao_available(
            environ={}, command_exists_fn=lambda _n: False
        )
        self.assertFalse(missing.details["available"])

    def test_apply_without_documented_cli_errors(self):
        refused = dao_ovs.start_dao_ovs_offload_placeholder(
            apply=True, dry_run=False, dao_available=True
        )
        self.assertEqual(refused.status, "error")
        self.assertFalse(refused.executed)

    def test_discovery_does_not_claim_dao_offload(self):
        result = discovery.discover(
            samples=marvell_samples(),
            command_exists_fn=lambda _n: False,
        )
        row = row_from_discovery("marvell-octeon", result)
        self.assertIsNone(row.ovs_hw_offload_available)
        tel = dao_ovs.collect_dao_telemetry_placeholder(dao_available=True)
        self.assertEqual(tel.telemetry, [])


if __name__ == "__main__":
    unittest.main()
