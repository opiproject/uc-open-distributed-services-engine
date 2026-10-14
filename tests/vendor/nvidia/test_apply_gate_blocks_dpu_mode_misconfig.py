"""NVIDIA DPU-mode misconfig is blocked by the apply readiness gate."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402
import apply_readiness_gate  # noqa: E402
import lab_state  # noqa: E402

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class ApplyGateBlocksDpuModeMisconfigTests(unittest.TestCase):
    def _base(self):
        cfg = copy.deepcopy(load_vm2vm_config(NVIDIA_EXAMPLE))
        cfg["safety"]["dry_run_default"] = False
        return cfg

    def _seed_ok(self, run_dir: Path, cfg: dict) -> None:
        lab_state.save_stage(
            run_dir,
            "validate-config",
            {"stage": "validate-config"},
            config_hash_value=lab_state.config_hash(cfg),
            dry_run=True,
        )
        lab_state.save_stage(
            run_dir,
            "create-vms",
            {"stage": "create-vms"},
            config_hash_value=lab_state.config_hash(cfg),
            dry_run=True,
        )

    def test_host_ovs_location_blocked(self):
        cfg = self._base()
        cfg["nvidia"]["ovs_location"] = "host"
        cfg["nvidia"]["mode"] = "dpu"
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._seed_ok(run_dir, cfg)
            gate = apply_readiness_gate.evaluate_apply_readiness(
                stage="create-vms",
                config=cfg,
                run_dir=run_dir,
                apply_requested=True,
            )
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["error"], apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE)
        self.assertTrue(
            "nvidia_dpu_mode" in gate["failed_checks"]
            or "vendor_profile_valid" in gate["failed_checks"]
        )

    def test_missing_dpu_ssh_blocked(self):
        cfg = self._base()
        cfg["dpu"]["management_ip"] = None
        cfg["dpu"]["ssh_user"] = None
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._seed_ok(run_dir, cfg)
            gate = apply_readiness_gate.evaluate_apply_readiness(
                stage="create-vms",
                config=cfg,
                run_dir=run_dir,
                apply_requested=True,
            )
        self.assertFalse(gate["ok"])
        self.assertIn("nvidia_dpu_mode", gate["failed_checks"])
        self.assertIn("remote execution", gate["message"])

    def test_host_forwarding_enabled_blocked(self):
        cfg = self._base()
        cfg["host_forwarding_control"]["enabled"] = True
        cfg["nvidia"]["allow_host_ovs_for_forwarding"] = True
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._seed_ok(run_dir, cfg)
            gate = apply_readiness_gate.evaluate_apply_readiness(
                stage="create-vms",
                config=cfg,
                run_dir=run_dir,
                apply_requested=True,
            )
        self.assertFalse(gate["ok"])
        self.assertTrue(
            "nvidia_dpu_mode" in gate["failed_checks"]
            or "vendor_profile_valid" in gate["failed_checks"]
        )


if __name__ == "__main__":
    unittest.main()
