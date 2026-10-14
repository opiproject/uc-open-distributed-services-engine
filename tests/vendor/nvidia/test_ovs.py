"""NVIDIA OVS helpers from fixtures (no live OVS)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult
from plugins.nvidia_bluefield import ovs as nvovs
from plugins.nvidia_bluefield.discovery import discover
from tests.vendor.harness import nvidia_samples, read_fixture, row_from_discovery


class NvidiaOvsFixtureTests(unittest.TestCase):
    def test_ovs_show_fixture_does_not_prove_offload(self):
        raw = read_fixture("nvidia_bluefield", "ovs_vsctl.txt")
        self.assertIn("br-int", raw)
        self.assertIn("pf0vf0", raw)
        result = discover(samples=nvidia_samples())
        row = row_from_discovery("nvidia-bluefield", result)
        self.assertTrue(row.ovs_available)
        self.assertIsNone(row.ovs_hw_offload_available)

    def test_doca_not_assumed_without_datapath_type(self):
        cap = nvovs.probe_ovs_capabilities(
            command_exists_fn=lambda _n: True,
            datapath_types=["system", "netdev"],
        )
        self.assertFalse(cap.details["doca_datapath"])
        doca = nvovs.create_ovs_doca_bridge(
            "br-int", dry_run=True, datapath_types=["system"]
        )
        self.assertEqual(doca.status, "skipped")

    def test_hw_offload_dry_run_not_executed(self):
        result = nvovs.enable_ovs_hw_offload(
            dry_run=True,
            command_exists_fn=lambda _n: True,
            runner=lambda argv, **_kw: CommandResult(
                argv=list(argv), dry_run=True, executed=False
            ),
        )
        self.assertFalse(result.executed)


if __name__ == "__main__":
    unittest.main()
