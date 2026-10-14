"""create-vms / attach-vfs dry-run (no virsh execute)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location("odse_labctl", ROOT / "tools" / "labctl.py")
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class VmCreationDryRunTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = labctl.RESULTS_ROOT
        labctl.RESULTS_ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        labctl.RESULTS_ROOT = self._orig
        self._tmpdir.cleanup()

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = labctl.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_create_vms_plans_virsh_define_without_executing(self):
        rc, out, err = self._run(
            [
                "vm2vm",
                "create-vms",
                "--config",
                str(NVIDIA_EXAMPLE),
                "--run-id",
                "vm-dry",
                "--json",
            ]
        )
        self.assertEqual(rc, 0, err)
        envelope = json.loads(out)
        result = envelope["result"]
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["wait_for_boot"])
        self.assertEqual(len(result["vms"]), 2)
        for vm in result["vms"]:
            self.assertNotIn("error", vm)
            self.assertFalse(vm["executed"])
            self.assertIn("xml_preview", vm)
            self.assertFalse(vm["include_vf_hostdev"])
            self.assertFalse(vm["define"]["executed"])
        saved = labctl.load_stage_json(
            labctl.run_dir_for("nvidia-bluefield", "vm-dry"),
            "create-vms",
        )
        self.assertEqual(saved["stage"], "create-vms")

    def test_attach_vfs_skips_without_pci_and_does_not_guess(self):
        rc, out, _ = self._run(
            [
                "vm2vm",
                "attach-vfs",
                "--config",
                str(NVIDIA_EXAMPLE),
                "--run-id",
                "vf-dry",
                "--json",
            ]
        )
        self.assertEqual(rc, 0)
        result = json.loads(out)["result"]
        for vm in result["vms"]:
            self.assertTrue(vm.get("skipped"))
            self.assertIsNone(vm.get("vf_pci"))

    def test_attach_vfs_plans_from_prior_mapping(self):
        run_id = "vf-mapped"
        run_dir = labctl.run_dir_for("nvidia-bluefield", run_id)
        labctl.save_stage_json(
            run_dir,
            "discover-representors",
            {
                "vf_to_representor_map": {
                    "mappings": [
                        {"vf_index": 0, "vf_pci": "0000:03:00.2", "confidence": "high"},
                        {"vf_index": 1, "vf_pci": "0000:03:00.3", "confidence": "high"},
                    ]
                }
            },
        )
        rc, out, _ = self._run(
            [
                "vm2vm",
                "attach-vfs",
                "--config",
                str(NVIDIA_EXAMPLE),
                "--run-id",
                run_id,
                "--json",
            ]
        )
        self.assertEqual(rc, 0)
        result = json.loads(out)["result"]
        pcis = [vm["vf_pci"] for vm in result["vms"]]
        self.assertEqual(pcis, ["0000:03:00.2", "0000:03:00.3"])
        for vm in result["vms"]:
            self.assertFalse(vm["command"]["executed"])
            self.assertIn("attach-device", vm["command"]["argv"])


if __name__ == "__main__":
    unittest.main()
