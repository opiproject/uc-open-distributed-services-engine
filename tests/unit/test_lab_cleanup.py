"""tools/lab_cleanup.py tests (no hardware; dry-run only).

Covers: dry-run/apply gating, the reset_sriov_numvfs "matching prior
state file" safety gate, preserved-interface refusals, port-removal
merging from prior stage evidence, and an end-to-end CLI smoke test
against config/lab-nvidia-vm2vm.example.yaml.
"""

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

from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("odse_lab_cleanup", ROOT / "tools" / "lab_cleanup.py")
lab_cleanup = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = lab_cleanup
_SPEC.loader.exec_module(lab_cleanup)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class _TempResultsRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_results_root = lab_cleanup.RESULTS_ROOT
        lab_cleanup.RESULTS_ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        lab_cleanup.RESULTS_ROOT = self._orig_results_root
        self._tmpdir.cleanup()

    def _run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = lab_cleanup.main(argv)
        return rc, out.getvalue(), err.getvalue()


class DryRunGatingTests(unittest.TestCase):
    class _Args:
        def __init__(self, dry_run=False, apply=False):
            self.dry_run = dry_run
            self.apply = apply

    def test_default_is_dry_run_with_no_config(self):
        self.assertTrue(lab_cleanup._dry_run(self._Args(), None))
        self.assertFalse(lab_cleanup._apply_allowed(self._Args(), None))

    def test_apply_without_config_override_stays_dry_run(self):
        args = self._Args(apply=True)
        config = {"safety": {"dry_run_default": True}}
        self.assertTrue(lab_cleanup._dry_run(args, config))
        self.assertFalse(lab_cleanup._apply_allowed(args, config))

    def test_apply_with_config_override_executes(self):
        args = self._Args(apply=True)
        config = {"safety": {"dry_run_default": False}}
        self.assertFalse(lab_cleanup._dry_run(args, config))
        self.assertTrue(lab_cleanup._apply_allowed(args, config))

    def test_explicit_dry_run_flag_always_wins(self):
        args = self._Args(dry_run=True, apply=True)
        config = {"safety": {"dry_run_default": False}}
        self.assertTrue(lab_cleanup._dry_run(args, config))
        self.assertFalse(lab_cleanup._apply_allowed(args, config))


class SplitPciAddressTests(unittest.TestCase):
    def test_valid_address(self):
        self.assertEqual(
            lab_cleanup._split_pci_address("0000:03:00.2"),
            {"domain": "0000", "bus": "03", "slot": "00", "function": "2"},
        )

    def test_invalid_address_returns_none(self):
        self.assertIsNone(lab_cleanup._split_pci_address("not-a-pci-address"))
        self.assertIsNone(lab_cleanup._split_pci_address(""))


class HostdevDetachXmlTests(unittest.TestCase):
    def test_renders_expected_fields(self):
        xml = lab_cleanup._vf_hostdev_detach_xml("0000:03:00.2")
        self.assertIsNotNone(xml)
        self.assertIn("domain='0x0000'", xml)
        self.assertIn("bus='0x03'", xml)
        self.assertIn("slot='0x00'", xml)
        self.assertIn("function='0x2'", xml)

    def test_invalid_pci_returns_none(self):
        self.assertIsNone(lab_cleanup._vf_hostdev_detach_xml("garbage"))


class _FakeArgs:
    def __init__(self, **kwargs):
        self.dry_run = kwargs.pop("dry_run", True)
        self.apply = kwargs.pop("apply", False)
        self.undefine_vms = kwargs.pop("undefine_vms", False)
        self.restore_switchdev_legacy = kwargs.pop("restore_switchdev_legacy", False)
        self.restart_ovs = kwargs.pop("restart_ovs", False)


def _make_context(config: Dict[str, Any], run_dir: Path, args: _FakeArgs = None) -> Any:
    return lab_cleanup.CleanupContext(
        args or _FakeArgs(),
        config,
        "nvidia-bluefield",
        run_dir,
        "test-run",
    )


class ResetSriovNumvfsGateTests(unittest.TestCase):
    def _config(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        cfg["dpu"]["pf_name"] = "pf0hpf"
        cfg["dpu"]["pf_pci"] = "0000:03:00.0"
        cfg.setdefault("cleanup", {})["reset_host_vfs"] = True
        return cfg

    def test_skips_without_prior_prepare_vfs_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            ctx = _make_context(self._config(), run_dir)
            result = lab_cleanup.stage_reset_sriov_numvfs(ctx)
            self.assertTrue(result["skipped"])
            self.assertIn("no prior prepare_vfs.json", result["message"])

    def test_proceeds_when_prior_state_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "prepare_vfs.json").write_text(
                json.dumps({"pf_name": "pf0hpf", "pf_pci": "0000:03:00.0"}), encoding="utf-8"
            )
            ctx = _make_context(self._config(), run_dir)
            result = lab_cleanup.stage_reset_sriov_numvfs(ctx)
            self.assertNotIn("skipped", result)
            self.assertEqual(result["pf_name"], "pf0hpf")
            self.assertIn("sriov_numvfs", result["command"]["argv"][0])

    def test_skips_when_prior_state_pf_pci_mismatches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "prepare_vfs.json").write_text(
                json.dumps({"pf_name": "pf0hpf", "pf_pci": "0000:99:00.0"}), encoding="utf-8"
            )
            ctx = _make_context(self._config(), run_dir)
            result = lab_cleanup.stage_reset_sriov_numvfs(ctx)
            self.assertTrue(result["skipped"])
            self.assertIn("does not match the state file", result["message"])

    def test_refuses_when_pf_name_is_preserved_interface(self):
        config = self._config()
        config["host"]["management_interface"] = config["dpu"]["pf_name"]
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "prepare_vfs.json").write_text(
                json.dumps({"pf_name": config["dpu"]["pf_name"], "pf_pci": config["dpu"]["pf_pci"]}),
                encoding="utf-8",
            )
            ctx = _make_context(config, run_dir)
            result = lab_cleanup.stage_reset_sriov_numvfs(ctx)
            self.assertTrue(result["skipped"])
            self.assertIn("preserved interface", result["message"])


class DeleteOvsBridgeGateTests(unittest.TestCase):
    def test_refuses_when_bridge_matches_preserved_interface(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["host"]["management_interface"] = config["ovs"]["bridge"]
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            result = lab_cleanup.stage_delete_ovs_bridge(ctx)
            self.assertTrue(result["skipped"])
            self.assertIn("preserved interface", result["message"])

    def test_plans_deletion_when_lab_owned(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "prepare-ovs.json").write_text(
                json.dumps({"stage": "prepare-ovs", "bridge": config["ovs"]["bridge"], "lab_owned": True}),
                encoding="utf-8",
            )
            ctx = _make_context(config, run_dir)
            result = lab_cleanup.stage_delete_ovs_bridge(ctx)
            self.assertNotIn("skipped", result)
            self.assertEqual(result["bridge"], config["ovs"]["bridge"])
            self.assertTrue(result["command"]["dry_run"])
            self.assertEqual(result["location"], "dpu")


class PortsToRemoveTests(unittest.TestCase):
    def test_merges_configure_forwarding_and_discover_representors_and_static_ports(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["ovs"]["ports"] = ["extra-static-port"]
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "configure_forwarding.json").write_text(
                json.dumps({"result": {"configured_ports": ["pf0vf0"]}}), encoding="utf-8"
            )
            (run_dir / "discover_representors.json").write_text(
                json.dumps(
                    {
                        "vf_to_representor_map": {
                            "mappings": [{"representor_netdev": "pf0vf1"}, {"representor_netdev": "pf0vf0"}]
                        }
                    }
                ),
                encoding="utf-8",
            )
            ctx = _make_context(config, run_dir)
            ports = lab_cleanup._ports_to_remove(ctx)
        self.assertEqual(ports, ["pf0vf0", "pf0vf1", "extra-static-port"])

    def test_excludes_preserved_interfaces(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        mgmt = config["host"]["management_interface"]
        config["ovs"]["ports"] = [mgmt, "safe-port"]
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            ports = lab_cleanup._ports_to_remove(ctx)
        self.assertEqual(ports, ["safe-port"])
        self.assertNotIn(mgmt, ports)

    def test_empty_when_no_prior_evidence(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            self.assertEqual(lab_cleanup._ports_to_remove(ctx), [])


class DetachAndRestoreVfSkipTests(unittest.TestCase):
    def test_detach_skips_when_vf_pci_unknown(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        for vm in config["vms"]:
            vm["vf_pci"] = None
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            result = lab_cleanup.stage_detach_vfs(ctx)
        for entry in result["vms"]:
            self.assertTrue(entry["skipped"])

    def test_restore_vf_driver_skips_when_vf_pci_unknown(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        for vm in config["vms"]:
            vm["vf_pci"] = None
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            result = lab_cleanup.stage_restore_vf_driver(ctx)
        for entry in result["vms"]:
            self.assertTrue(entry["skipped"])


class StopVmsUndefineFlagTests(unittest.TestCase):
    def test_undefine_skipped_by_default(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            result = lab_cleanup.stage_stop_vms(ctx)
        for entry in result["vms"]:
            self.assertTrue(entry["undefine"]["skipped"])

    def test_undefine_runs_when_flag_set(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir), _FakeArgs(undefine_vms=True))
            result = lab_cleanup.stage_stop_vms(ctx)
        for entry in result["vms"]:
            self.assertNotIn("skipped", entry["undefine"])
            self.assertEqual(entry["undefine"]["argv"][0:2], ["virsh", "undefine"])


class OptInStagesDefaultSkippedTests(unittest.TestCase):
    def test_restore_switchdev_legacy_skipped_by_default(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            result = lab_cleanup.stage_restore_switchdev_legacy(ctx)
        self.assertTrue(result["skipped"])

    def test_restart_ovs_skipped_by_default(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            result = lab_cleanup.stage_restart_ovs(ctx)
        self.assertTrue(result["skipped"])

    def test_restore_switchdev_legacy_runs_when_opted_in_and_enabled(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["nvidia"] = {
            "mode": "host-switchdev",
            "ovs_location": "host",
            "representor_location": "host",
            "allow_host_ovs_for_forwarding": True,
        }
        config["dpu"]["enable_switchdev"] = True
        config["dpu"]["pf_pci"] = "0000:03:00.0"
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir), _FakeArgs(restore_switchdev_legacy=True))
            result = lab_cleanup.stage_restore_switchdev_legacy(ctx)
        self.assertNotIn("skipped", result)
        self.assertIn("devlink", result["command"]["argv"])


class RollbackRisksTests(unittest.TestCase):
    def test_always_includes_base_risks(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir))
            risks = lab_cleanup.rollback_risks(ctx)
        self.assertTrue(any("Removing OVS ports/bridge" in r for r in risks))
        self.assertTrue(any(config["dpu"]["pf_name"] in r for r in risks))

    def test_undefine_vms_adds_specific_risk(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(config, Path(tmpdir), _FakeArgs(undefine_vms=True))
            risks = lab_cleanup.rollback_risks(ctx)
        self.assertTrue(any("--undefine-vms" in r for r in risks))


class CliEndToEndTests(_TempResultsRootMixin):
    def test_dry_run_completes_and_writes_evidence(self):
        rc, out, err = self._run_cli(
            ["--config", str(NVIDIA_EXAMPLE), "--run-id", "cli-e2e-1", "--pretty"]
        )
        self.assertEqual(rc, 0)
        report = json.loads(out)
        self.assertEqual(report["schema_version"], "odse-lab-cleanup/v1alpha1")
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["apply"])
        self.assertEqual(set(report["stages"].keys()), set(lab_cleanup.CLEANUP_STAGES))

        evidence_path = (
            lab_cleanup.RESULTS_ROOT / "nvidia-bluefield" / "cli-e2e-1" / "cleanup" / "cleanup-summary.json"
        )
        self.assertTrue(evidence_path.exists())
        saved = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["run_id"], "cli-e2e-1")

    def test_apply_without_dry_run_default_false_stays_dry_run(self):
        rc, out, _ = self._run_cli(
            ["--config", str(NVIDIA_EXAMPLE), "--run-id", "cli-e2e-2", "--apply"]
        )
        self.assertEqual(rc, 0)
        report = json.loads(out)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["apply"])

    def test_invalid_config_fails_before_any_stage_runs(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["dpu"]["num_vfs"] = 4
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            json.dump(config, handle)
            temp_path = handle.name
        try:
            rc, _, err = self._run_cli(["--config", temp_path])
            self.assertEqual(rc, 1)
            self.assertIn("config is invalid", err)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_rollback_risks_printed_to_stderr_only_when_applying(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["safety"]["dry_run_default"] = False
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            json.dump(config, handle)
            temp_path = handle.name
        try:
            rc, out, err = self._run_cli(
                ["--config", temp_path, "--run-id", "cli-e2e-3", "--apply"]
            )
            self.assertEqual(rc, 0)
            self.assertIn("Rollback risks", err)
            # stdout must still be pure JSON (labctl.py parses it directly).
            json.loads(out)
        finally:
            Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
