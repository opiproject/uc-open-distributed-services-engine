"""tools/labctl.py `validate-offload` stage: intent-to-evidence linking
(Prompt J). Verifies the stage reads the prior configure-forwarding.json,
surfaces forwarding name/endpoints/VF PCI addresses/representors/bridge/
flows/offload-requested/offload-configured in its own output, and passes
`--forwarding-result` to ovs_offload_checker.py. No hardware; dry-run only.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location("odse_labctl_fwd_reporting", ROOT / "tools" / "labctl.py")
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"

CONFIGURE_FORWARDING_FIXTURE: Dict[str, Any] = {
    "apply": False,
    "dry_run": True,
    "intent": {
        "bridge": "br-int",
        "name": "odse-vm2vm-nvidia",
        "endpoints": [
            {
                "name": "vm1-endpoint",
                "vm": "odse-vm2vm-vm1",
                "vf_index": 0,
                "vf_pci": "0000:03:00.2",
                "representor": "pf0vf0",
                "mac": None,
                "ip_cidr": "198.51.100.11/24",
                "role": None,
            },
            {
                "name": "vm2-endpoint",
                "vm": "odse-vm2vm-vm2",
                "vf_index": 1,
                "vf_pci": "0000:03:00.3",
                "representor": "pf0vf1",
                "mac": None,
                "ip_cidr": "198.51.100.12/24",
                "role": None,
            },
        ],
    },
    "result": {
        "success": True,
        "vendor": "nvidia",
        "operation": "configure_forwarding",
        "message": "planned OVS bridge/representor forwarding (dry-run; no CLI executed)",
        "forwarding_entity": "NVIDIA BlueField/ConnectX E-Switch",
        "control_entity": "labctl -> nvidia backend -> OVS/OVS-DOCA -> E-Switch",
        "configured_endpoints": ["vm1-endpoint", "vm2-endpoint"],
        "configured_ports": ["pf0vf0", "pf0vf1"],
        "configured_flows": [],
        "offload_requested": True,
        "offload_configured": True,
        "offload_confirmed": False,
        "warnings": [],
        "errors": [],
        "details": {"bridge": "br-int", "datapath_type": "system"},
    },
    "stage": "configure-forwarding",
}


class _TempResultsRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_results_root = labctl.RESULTS_ROOT
        labctl.RESULTS_ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        labctl.RESULTS_ROOT = self._orig_results_root
        self._tmpdir.cleanup()

    def _run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = labctl.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _seed_configure_forwarding(self, run_id: str, data: Dict[str, Any] = None) -> Path:
        run_dir = labctl.run_dir_for("nvidia-bluefield", run_id)
        labctl.save_stage_json(run_dir, "configure-forwarding", data or CONFIGURE_FORWARDING_FIXTURE)
        return run_dir


class ValidateOffloadForwardingLinkTests(_TempResultsRootMixin):
    def test_forwarding_fields_surfaced_from_prior_stage(self):
        run_id = "fwd-link-1"
        self._seed_configure_forwarding(run_id)
        rc, out, _ = self._run_cli(
            ["vm2vm", "validate-offload", "--config", str(NVIDIA_EXAMPLE), "--run-id", run_id, "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        forwarding = envelope["result"]["forwarding"]

        self.assertEqual(forwarding["name"], "odse-vm2vm-nvidia")
        self.assertEqual(forwarding["bridge"], "br-int")
        self.assertEqual(forwarding["configured_ports"], ["pf0vf0", "pf0vf1"])
        self.assertEqual(forwarding["configured_flows"], [])
        self.assertTrue(forwarding["offload_requested"])
        self.assertTrue(forwarding["offload_configured"])
        self.assertEqual(forwarding["vf_pci_addresses"], ["0000:03:00.2", "0000:03:00.3"])
        self.assertEqual(forwarding["representors"], ["pf0vf0", "pf0vf1"])
        self.assertEqual(len(forwarding["endpoints"]), 2)

    def test_forwarding_result_path_passed_to_offload_checker(self):
        run_id = "fwd-link-2"
        run_dir = self._seed_configure_forwarding(run_id)
        captured: Dict[str, Any] = {}
        real_run_tool = labctl._run_tool

        def _spy(argv, *, reason, timeout=30.0):
            if "ovs_offload_checker.py" in argv[1]:
                captured["argv"] = argv
            return real_run_tool(argv, reason=reason, timeout=timeout)

        labctl._run_tool = _spy
        try:
            rc, _, _ = self._run_cli(
                ["vm2vm", "validate-offload", "--config", str(NVIDIA_EXAMPLE), "--run-id", run_id, "--json"]
            )
        finally:
            labctl._run_tool = real_run_tool
        self.assertEqual(rc, 0)
        argv = captured["argv"]

        expected_path = str(run_dir / "configure_forwarding.json")
        self.assertIn("--forwarding-result", argv)
        self.assertEqual(argv[argv.index("--forwarding-result") + 1], expected_path)
        self.assertIn("--bridge", argv)
        self.assertEqual(argv[argv.index("--bridge") + 1], "br-int")
        self.assertEqual(argv.count("--interface"), 2)
        self.assertIn("--location", argv)
        self.assertEqual(argv[argv.index("--location") + 1], "dpu")
        self.assertIn("--config", argv)

    def test_no_prior_configure_forwarding_stage_degrades_gracefully(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "validate-offload", "--config", str(NVIDIA_EXAMPLE), "--run-id", "no-fwd", "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        forwarding = envelope["result"]["forwarding"]
        self.assertIsNone(forwarding["name"])
        self.assertEqual(forwarding["endpoints"], [])
        self.assertEqual(forwarding["vf_pci_addresses"], [])
        self.assertEqual(forwarding["representors"], [])
        self.assertEqual(forwarding["configured_ports"], [])


if __name__ == "__main__":
    unittest.main()
