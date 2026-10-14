"""DPU-side NVIDIA cleanup plans SSH OVS teardown, not host OVS."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.nvidia_bluefield.backend import NvidiaBlueFieldBackend
from plugins.nvidia_bluefield.dpu_ovs import UNKNOWN_BRIDGE_REFUSED, plan_dpu_ovs_cleanup

_SPEC = importlib.util.spec_from_file_location("odse_lab_cleanup", ROOT / "tools" / "lab_cleanup.py")
lab_cleanup = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = lab_cleanup
_SPEC.loader.exec_module(lab_cleanup)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class _FakeArgs:
    def __init__(self):
        self.dry_run = True
        self.apply = False
        self.undefine_vms = False
        self.restore_switchdev_legacy = False
        self.restart_ovs = False


class DpuSideCleanupPlanTests(unittest.TestCase):
    def test_backend_plans_dpu_ssh_not_host_ovs(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        backend = NvidiaBlueFieldBackend(lab_config=cfg)
        result = backend.cleanup()
        self.assertEqual(result.details.get("location"), "dpu")
        self.assertFalse(result.executed)
        self.assertTrue(any("ssh" in (c.argv or []) for c in result.commands))
        self.assertEqual(result.details.get("dpu_ovs_status"), UNKNOWN_BRIDGE_REFUSED)

    def test_lab_cleanup_remove_ports_uses_dpu_ssh(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "configure_forwarding.json").write_text(
                json.dumps({"result": {"configured_ports": ["pf0vf0", "pf0vf1"]}}),
                encoding="utf-8",
            )
            ctx = lab_cleanup.CleanupContext(_FakeArgs(), cfg, "nvidia-bluefield", run_dir, "r1")
            result = lab_cleanup.stage_remove_ovs_ports(ctx)
            self.assertEqual(result["location"], "dpu")
            self.assertTrue(result["host_ovs_untouched"])
            self.assertEqual(result["ports"], ["pf0vf0", "pf0vf1"])
            argv = result["results"][0]["command"]["argv"]
            self.assertIn("ssh", argv)
            self.assertFalse(result["results"][0]["command"]["executed"])

    def test_switchdev_and_host_ovs_restart_skipped_in_dpu_mode(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = lab_cleanup.CleanupContext(_FakeArgs(), cfg, "nvidia-bluefield", Path(tmpdir), "r1")
            sw = lab_cleanup.stage_restore_switchdev_legacy(ctx)
            self.assertTrue(sw["skipped"])
            rst = lab_cleanup.stage_restart_ovs(ctx)
            self.assertTrue(rst["skipped"])
            reset = lab_cleanup.stage_reset_sriov_numvfs(ctx)
            self.assertTrue(reset["skipped"])


class CleanupRefusesUnknownBridgeTests(unittest.TestCase):
    def test_plan_dpu_ovs_cleanup_refuses_untracked_bridge(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        result = plan_dpu_ovs_cleanup(
            cfg,
            owned_bridge="br-int",
            owned_ports=[],
            lab_owned_bridge=False,
            dry_run=True,
        )
        self.assertEqual(result.message, UNKNOWN_BRIDGE_REFUSED)
        self.assertTrue(result.details.get("refused_del_br"))

    def test_lab_cleanup_refuses_delete_without_lab_owned(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = lab_cleanup.CleanupContext(_FakeArgs(), cfg, "nvidia-bluefield", Path(tmpdir), "r1")
            result = lab_cleanup.stage_delete_ovs_bridge(ctx)
            self.assertTrue(result["skipped"])
            self.assertEqual(result["error"], "UNKNOWN_BRIDGE_REFUSED")
            self.assertNotIn("command", result)


if __name__ == "__main__":
    unittest.main()
