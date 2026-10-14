"""End-to-end CLI tests for `ovs_offload_checker.py --forwarding-result`
(Prompt J intent-to-evidence linking). Runs the real `main()` entry point
against a temp configure-forwarding.json and a temp --output-dir; no
hardware and no OVS/tc/ethtool required (missing tools are recorded as
errors in the JSON, never fatal).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "tools" / "offload-checker" / "ovs_offload_checker.py"

_SPEC = importlib.util.spec_from_file_location("odse_ovs_offload_checker_e2e", CHECKER_PATH)
checker = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)


CONFIGURE_FORWARDING_FIXTURE: Dict[str, Any] = {
    "intent": {
        "name": "odse-vm2vm-nvidia",
        "bridge": "br-int",
        "endpoints": [
            {"name": "vm1-endpoint", "vf_pci": "0000:03:00.2", "representor": "pf0vf0"},
            {"name": "vm2-endpoint", "vf_pci": "0000:03:00.3", "representor": "pf0vf1"},
        ],
    },
    "result": {
        "configured_ports": ["pf0vf0", "pf0vf1"],
        "configured_flows": [],
        "offload_requested": True,
        "offload_configured": True,
    },
}


class OffloadCheckerForwardingResultCliTests(unittest.TestCase):
    def _write_fixture(self, tmpdir: str, data: Dict[str, Any] = None) -> str:
        path = Path(tmpdir) / "configure_forwarding.json"
        path.write_text(json.dumps(data or CONFIGURE_FORWARDING_FIXTURE), encoding="utf-8")
        return str(path)

    def test_bridge_and_interfaces_derived_from_forwarding_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fwd_path = self._write_fixture(tmpdir)
            out_dir = str(Path(tmpdir) / "out")
            rc = checker.main(["--forwarding-result", fwd_path, "--output-dir", out_dir])
            self.assertEqual(rc, 0)

            report = json.loads((Path(out_dir) / "offload-validation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["inputs"]["bridge"], "br-int")
            self.assertEqual(set(report["inputs"]["interfaces"]), {"pf0vf0", "pf0vf1"})
            self.assertEqual(report["forwarding_result"]["name"], "odse-vm2vm-nvidia")
            self.assertEqual(report["forwarding_result"]["vf_pci_addresses"], ["0000:03:00.2", "0000:03:00.3"])
            self.assertEqual(report["forwarding_result"]["representors"], ["pf0vf0", "pf0vf1"])
            self.assertTrue(report["forwarding_result"]["offload_requested"])

            md_path = Path(out_dir) / "offload-validation-report.md"
            self.assertTrue(md_path.exists())
            md_text = md_path.read_text(encoding="utf-8")
            self.assertIn("Forwarding Result", md_text)
            self.assertIn("pf0vf0", md_text)

    def test_explicit_interfaces_merge_with_forwarding_result_ports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fwd_path = self._write_fixture(tmpdir)
            out_dir = str(Path(tmpdir) / "out")
            rc = checker.main(
                [
                    "--forwarding-result",
                    fwd_path,
                    "--interface",
                    "pf0hpf",
                    "--output-dir",
                    out_dir,
                    "--json",
                ]
            )
            self.assertEqual(rc, 0)
            report = json.loads((Path(out_dir) / "offload-validation.json").read_text(encoding="utf-8"))
            self.assertEqual(
                set(report["inputs"]["interfaces"]), {"pf0hpf", "pf0vf0", "pf0vf1"}
            )
            # --output-dir with --json only writes the JSON file, not markdown.
            self.assertFalse((Path(out_dir) / "offload-validation-report.md").exists())

    def test_explicit_bridge_overrides_forwarding_result_bridge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fwd_path = self._write_fixture(tmpdir)
            out_dir = str(Path(tmpdir) / "out")
            rc = checker.main(
                ["--forwarding-result", fwd_path, "--bridge", "br-phy", "--output-dir", out_dir]
            )
            self.assertEqual(rc, 0)
            report = json.loads((Path(out_dir) / "offload-validation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["inputs"]["bridge"], "br-phy")

    def test_missing_forwarding_result_file_does_not_abort_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = str(Path(tmpdir) / "out")
            rc = checker.main(
                ["--forwarding-result", str(Path(tmpdir) / "nope.json"), "--output-dir", out_dir]
            )
            self.assertEqual(rc, 0)
            report = json.loads((Path(out_dir) / "offload-validation.json").read_text(encoding="utf-8"))
            self.assertIsNotNone(report["forwarding_result"]["error"])
            # Report still completes with an assessment (unknown, absent data).
            self.assertIn("status", report["assessment"])

    def test_no_forwarding_result_flag_omits_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = str(Path(tmpdir) / "out")
            rc = checker.main(["--output-dir", out_dir])
            self.assertEqual(rc, 0)
            report = json.loads((Path(out_dir) / "offload-validation.json").read_text(encoding="utf-8"))
            self.assertIsNone(report["forwarding_result"])


if __name__ == "__main__":
    unittest.main()
