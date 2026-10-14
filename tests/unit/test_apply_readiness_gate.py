"""labctl apply readiness gate (Prompt R10)."""

from __future__ import annotations

import copy
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
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402
import apply_readiness_gate  # noqa: E402
import lab_state  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("odse_labctl_apply_gate", ROOT / "tools" / "labctl.py")
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


def _apply_config() -> Dict[str, Any]:
    cfg = copy.deepcopy(load_vm2vm_config(NVIDIA_EXAMPLE))
    cfg["safety"]["dry_run_default"] = False
    return cfg


class _TempResultsRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_results_root = labctl.RESULTS_ROOT
        labctl.RESULTS_ROOT = Path(self._tmpdir.name)
        self.cfg = _apply_config()
        self.cfg_hash = lab_state.config_hash(self.cfg)

    def tearDown(self):
        labctl.RESULTS_ROOT = self._orig_results_root
        self._tmpdir.cleanup()

    def _write_config(self, cfg: Dict[str, Any]) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(cfg, handle)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def _run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = labctl.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _seed(self, run_dir: Path, stage: str, extra: Dict[str, Any] = None, dry_run: bool = True):
        payload = {"stage": stage}
        if extra:
            payload.update(extra)
        lab_state.save_stage(
            run_dir,
            stage,
            payload,
            config_hash_value=self.cfg_hash,
            dry_run=dry_run,
            apply=False,
        )


class EvaluateApplyReadinessTests(_TempResultsRootMixin):
    def test_missing_apply_flag_fails_apply_check(self):
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="create-vms",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=False,
        )
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["error"], apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE)
        self.assertIn("apply_flag", gate["failed_checks"])

    def test_missing_validate_config_blocks(self):
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="create-vms",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=True,
        )
        self.assertFalse(gate["ok"])
        self.assertIn("config_validated", gate["failed_checks"])

    def test_force_refused_for_configure_forwarding(self):
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        self._seed(run_dir, "validate-config")
        self._seed(run_dir, "discover-representors")
        self._seed(run_dir, "prepare-ovs")
        self._seed(run_dir, "configure-forwarding", dry_run=True)
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="configure-forwarding",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=True,
            force=True,
        )
        self.assertFalse(gate["ok"])
        self.assertIn("force_not_allowed_for_forwarding", gate["failed_checks"])

    def test_force_skips_dry_run_plan_for_cleanup_only(self):
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        self._seed(run_dir, "validate-config")
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="cleanup",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=True,
            force=True,
        )
        self.assertTrue(gate["ok"], gate["message"])

    def test_happy_path_create_vms(self):
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        self._seed(run_dir, "validate-config")
        self._seed(run_dir, "create-vms", dry_run=True)
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="create-vms",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=True,
        )
        self.assertTrue(gate["ok"], gate["message"])

    def test_attach_vfs_blocked_without_verify_vms(self):
        """Regression: attach-vfs --apply must not proceed on the strength
        of create-vms alone -- `virsh start`'s captured result can be
        stale/misleading (see docs/operations/lab-runbook-vm2vm-single-dpu.md,
        the verify-vms stage). Requiring verify-vms.json to exist for this
        run forces the operator to have actually re-checked domstate + SSH
        before attaching a VF.
        """
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        self._seed(run_dir, "validate-config")
        self._seed(run_dir, "discover")
        self._seed(run_dir, "create-vms", dry_run=True)
        self._seed(run_dir, "attach-vfs", dry_run=True)
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="attach-vfs",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=True,
        )
        self.assertFalse(gate["ok"])
        prior_check = next(c for c in gate["checks"] if c["name"] == "prior_stage_outputs")
        self.assertIn("verify-vms", prior_check["detail"])

    def test_attach_vfs_gate_passes_once_verify_vms_seeded(self):
        run_dir = Path(self._tmpdir.name) / "run"
        run_dir.mkdir()
        self._seed(run_dir, "validate-config")
        self._seed(run_dir, "discover")
        self._seed(run_dir, "create-vms", dry_run=True)
        self._seed(run_dir, "verify-vms", {"all_ready": True})
        self._seed(run_dir, "attach-vfs", dry_run=True)
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage="attach-vfs",
            config=self.cfg,
            run_dir=run_dir,
            apply_requested=True,
        )
        self.assertTrue(gate["ok"], gate["message"])


class LabctlApplyGateCliTests(_TempResultsRootMixin):
    def test_apply_without_gate_inputs_is_blocked(self):
        path = self._write_config(self.cfg)
        rc, out, _ = self._run_cli(
            ["vm2vm", "create-vms", "--config", path, "--run-id", "gate-1", "--apply", "--json"]
        )
        self.assertEqual(rc, 2)
        envelope = json.loads(out)
        self.assertEqual(
            envelope["error"], apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE
        )
        self.assertEqual(
            envelope["result"]["error"], apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE
        )


if __name__ == "__main__":
    unittest.main()
