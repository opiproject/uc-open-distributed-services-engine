"""Read-only preflight_readiness tests (no hardware, no SSH)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "odse_preflight_readiness",
    ROOT / "tools" / "preflight_readiness.py",
)
pre = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = pre
_SPEC.loader.exec_module(pre)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class PreflightTests(unittest.TestCase):
    def test_nvidia_example_is_dry_run_only_until_dpu_mode_pinned(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        report = pre.audit(config, NVIDIA_EXAMPLE)
        self.assertEqual(report.readiness_status, pre.STATUS_DRY_RUN_ONLY)
        cats = {c["category"]: c for c in report.checks}
        self.assertTrue(cats["lab_config_validity"]["ok"])
        self.assertTrue(cats["management_interface_safety"]["ok"])
        self.assertTrue(cats["nvidia_bluefield_dpu_mode_pinning"]["ok"])
        self.assertTrue(cats["dpu_side_command_execution_availability"]["ok"])
        self.assertTrue(cats["dpu_side_ovs_ovs_doca_validation_support"]["ok"])
        self.assertTrue(cats["cleanup_coverage"]["ok"])
        self.assertTrue(cats["cleanup_coverage"]["details"]["dpu_side_cleanup"])
        self.assertFalse(
            any("cleanup does not yet target DPU-side OVS" in w for w in report.warnings)
        )

    def test_cli_markdown(self):
        rc = pre.main(["--config", str(NVIDIA_EXAMPLE), "--markdown"])
        self.assertEqual(rc, 0)

    def test_invalid_config_not_ready(self):
        report = pre.audit({"schema": "nope"}, Path("missing.yaml"))
        self.assertEqual(report.readiness_status, pre.STATUS_NOT_READY)
        self.assertTrue(report.blocking_issues)

    def test_json_shape(self):
        report = pre.audit(load_vm2vm_config(NVIDIA_EXAMPLE), NVIDIA_EXAMPLE)
        payload = json.loads(json.dumps(report, default=lambda o: o.__dict__))
        self.assertIn("readiness_status", payload)
        self.assertIn("blocking_issues", payload)
        self.assertIn("checked_files", payload)
        self.assertIn("checked_commands", payload)
