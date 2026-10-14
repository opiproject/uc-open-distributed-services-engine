"""Host vs DPU sections in ovs_offload_checker reports."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "tools" / "offload-checker" / "ovs_offload_checker.py"
_SPEC = importlib.util.spec_from_file_location("odse_ovs_offload_checker_sections", CHECKER_PATH)
checker = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)


def _report(**kwargs):
    defaults = dict(
        interfaces=[],
        pf=None,
        vendor=None,
        flow=None,
        bridge="br-int",
        more=False,
        datapath=None,
        duration=0.0,
        timeout=5,
        plugins=[],
    )
    defaults.update(kwargs)
    return checker.build_report(**defaults)


class OffloadReportHostVsDpuTests(unittest.TestCase):
    def test_host_location_keeps_ovs_in_host_evidence(self):
        report = _report(location="host", samples={"ovs_vsctl_show": "Bridge br-int\n"})
        self.assertEqual(report["location"], "host")
        self.assertIsNone(report["dpu_evidence"])
        self.assertIn("ovs_vsctl_show", report["host_evidence"])
        self.assertIn("host_evidence", report)
        self.assertIn("vm_evidence", report)
        self.assertIn("offload_confidence", report)

    def test_dpu_location_separates_host_and_dpu(self):
        report = _report(
            location="dpu",
            samples={"ovs_vsctl_show": "Bridge br-int\n    Port pf0vf0\n"},
        )
        self.assertEqual(report["location"], "dpu")
        self.assertIsNotNone(report["dpu_evidence"])
        self.assertIn("ovs_vsctl_show", report["dpu_evidence"])
        self.assertNotIn("ovs_vsctl_show", report["host_evidence"])
        self.assertIn("not DPU offload proof", report["host_evidence"]["note"])

    def test_control_entity_selects_dpu_without_explicit_location(self):
        report = _report(
            forwarding_result={
                "control_entity": "RemoteCommandRunner -> DPU ovs-vsctl",
                "forwarding_entity": "NVIDIA BlueField DPU OVS/OVS-DOCA",
            },
        )
        self.assertEqual(report["location"], "dpu")
        self.assertTrue(report["ovs_vsctl_show"].get("planned"))

    def test_dpu_planned_report_never_claims_offloaded_yes(self):
        report = _report(location="dpu")
        self.assertEqual(report["assessment"]["status"], "unknown")
        raw = str(report["dpu_evidence"]["dpctl_dump_flows"])
        self.assertNotIn("offloaded:yes", raw)


if __name__ == "__main__":
    unittest.main()
