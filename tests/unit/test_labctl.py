"""tools/labctl.py orchestrator tests (no hardware; dry-run only)."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("odse_labctl", ROOT / "tools" / "labctl.py")
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


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


class ResolveRunIdTests(_TempResultsRootMixin):
    def test_explicit_run_id_wins(self):
        self.assertEqual(labctl.resolve_run_id("nvidia-bluefield", "my-run"), "my-run")

    def test_falls_back_to_latest_pointer(self):
        labctl.mark_latest("nvidia-bluefield", "run-123")
        self.assertEqual(labctl.resolve_run_id("nvidia-bluefield", None), "run-123")

    def test_generates_fresh_timestamp_when_no_latest(self):
        run_id = labctl.resolve_run_id("nvidia-bluefield", None)
        self.assertRegex(run_id, r"^\d{8}T\d{6}Z$")


class DryRunGatingTests(unittest.TestCase):
    class _Args:
        def __init__(self, dry_run=False, apply=False):
            self.dry_run = dry_run
            self.apply = apply

    def test_default_is_dry_run_with_no_config(self):
        self.assertTrue(labctl._dry_run(self._Args(), None))
        self.assertFalse(labctl._apply_allowed(self._Args(), None))

    def test_apply_without_config_override_stays_dry_run(self):
        args = self._Args(apply=True)
        config = {"safety": {"dry_run_default": True}}
        self.assertTrue(labctl._dry_run(args, config))
        self.assertFalse(labctl._apply_allowed(args, config))

    def test_apply_with_config_override_executes(self):
        args = self._Args(apply=True)
        config = {"safety": {"dry_run_default": False}}
        self.assertFalse(labctl._dry_run(args, config))
        self.assertTrue(labctl._apply_allowed(args, config))

    def test_explicit_dry_run_flag_always_wins(self):
        args = self._Args(dry_run=True, apply=True)
        config = {"safety": {"dry_run_default": False}}
        self.assertTrue(labctl._dry_run(args, config))
        self.assertFalse(labctl._apply_allowed(args, config))


class SplitPciAddressTests(unittest.TestCase):
    def test_splits_valid_address(self):
        self.assertEqual(
            labctl._split_pci_address("0000:03:00.2"),
            {"domain": "0000", "bus": "03", "slot": "00", "function": "2"},
        )

    def test_rejects_malformed_address(self):
        self.assertIsNone(labctl._split_pci_address("not-a-pci-address"))
        self.assertIsNone(labctl._split_pci_address(""))


class LibvirtXmlRenderingTests(unittest.TestCase):
    def test_renders_all_placeholders_with_vf_pci(self):
        vm = {
            "name": "vm1",
            "memory_mb": 2048,
            "vcpus": 2,
            "image": "/var/lib/libvirt/images/vm1.qcow2",
            "vf_pci": "0000:03:00.2",
            "mac": "52:54:00:12:34:56",
        }
        xml = labctl._libvirt_xml_for_vm(vm)
        self.assertNotIn("@", xml)
        self.assertIn("<name>vm1</name>", xml)
        self.assertIn("<memory unit='KiB'>2097152</memory>", xml)
        self.assertIn("domain='0x0000'", xml)
        self.assertIn("bus='0x03'", xml)
        self.assertIn("slot='0x00'", xml)
        self.assertIn("function='0x2'", xml)
        self.assertIn("<mac address='52:54:00:12:34:56'/>", xml)

    def test_leaves_pci_placeholders_when_vf_pci_missing(self):
        vm = {"name": "vm1", "memory_mb": 2048, "vcpus": 2, "image": "/x.qcow2"}
        xml = labctl._libvirt_xml_for_vm(vm)
        self.assertIn("@VF_DOMAIN@", xml)
        self.assertIn("@MAC_ADDRESS@", xml)

    def test_raises_on_malformed_vf_pci(self):
        vm = {"name": "vm1", "memory_mb": 2048, "vcpus": 2, "image": "/x.qcow2", "vf_pci": "garbage"}
        with self.assertRaises(ValueError):
            labctl._libvirt_xml_for_vm(vm)


class VmManagementHostTests(unittest.TestCase):
    def test_explicit_management_ip_wins(self):
        vm = {"name": "vm1", "management_ip": "192.0.2.50"}
        self.assertEqual(labctl._vm_management_host(vm), "192.0.2.50")

    def test_no_virsh_and_no_management_ip_returns_none(self):
        vm = {"name": "vm1"}
        # On this dev machine virsh is not on PATH; confirms graceful None.
        if not labctl.command_exists("virsh"):
            self.assertIsNone(labctl._vm_management_host(vm))


class StageCliTests(_TempResultsRootMixin):
    def test_validate_config_stage_reports_valid(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "validate-config", "--config", str(NVIDIA_EXAMPLE), "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        self.assertEqual(envelope["stage"], "validate-config")
        self.assertEqual(envelope["vendor"], "nvidia-bluefield")
        self.assertTrue(envelope["result"]["valid"])
        self.assertEqual(envelope["result"]["issues"], [])
        dpu_ovs = envelope["result"]["dpu_ovs_validation"]
        self.assertTrue((dpu_ovs.get("details") or {}).get("host_ovs_untouched"))
        for cmd in dpu_ovs.get("commands") or []:
            argv = cmd.get("argv") or []
            self.assertIn("ssh", argv)
            self.assertNotIn("add-br", argv)

    def test_discover_stage_runs_dpu_inventory_and_saves_json(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "discover", "--config", str(NVIDIA_EXAMPLE), "--run-id", "r1", "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        self.assertEqual(envelope["result"]["stage"], "discover")
        self.assertIn("dpu_inventory", envelope["result"])
        saved = labctl.run_dir_for("nvidia-bluefield", "r1") / "discover.json"
        self.assertTrue(saved.exists())

    def test_discover_representors_uses_dpu_location_not_host_sysfs(self):
        captured: Dict[str, Any] = {}
        real_run_tool = labctl._run_tool

        def _spy(argv, *, reason, timeout=30.0):
            if "vf_to_representor_map.py" in argv[1]:
                captured["argv"] = argv
            return real_run_tool(argv, reason=reason, timeout=timeout)

        labctl._run_tool = _spy
        try:
            rc, out, _ = self._run_cli(
                ["vm2vm", "discover-representors", "--config", str(NVIDIA_EXAMPLE), "--json"]
            )
        finally:
            labctl._run_tool = real_run_tool
        self.assertEqual(rc, 0)
        argv = captured["argv"]
        self.assertIn("--location", argv)
        self.assertEqual(argv[argv.index("--location") + 1], "dpu")
        self.assertIn("--config", argv)
        self.assertIn("--pf-pci", argv)
        self.assertEqual(argv[argv.index("--pf-pci") + 1], "<PF_PCI>")
        self.assertNotIn("--pf", argv)
        self.assertNotIn("--vendor", argv)
        envelope = json.loads(out)
        self.assertEqual(envelope["result"]["location"], "dpu")
        self.assertEqual(envelope["result"]["strategy"], "nvidia-portname")

    def test_discover_representors_dpu_location_scales_subprocess_timeout(self):
        # location=="dpu" can make 3 sequential real SSH round trips to the
        # DPU (see collect_dpu_samples_via_ssh in vf_to_representor_map.py);
        # the default 30s _run_tool timeout is sized for one quick local
        # call, not three real SSH round trips -- labctl scales it off
        # dpu.command_timeout_seconds instead of leaving it fixed.
        captured: Dict[str, Any] = {}
        real_run_tool = labctl._run_tool

        def _spy(argv, *, reason, timeout=30.0):
            if "vf_to_representor_map.py" in argv[1]:
                captured["timeout"] = timeout
            return real_run_tool(argv, reason=reason, timeout=timeout)

        labctl._run_tool = _spy
        try:
            rc, _, _ = self._run_cli(
                ["vm2vm", "discover-representors", "--config", str(NVIDIA_EXAMPLE), "--json"]
            )
        finally:
            labctl._run_tool = real_run_tool
        self.assertEqual(rc, 0)
        # NVIDIA_EXAMPLE ships dpu.command_timeout_seconds: 30 -> 30*3+20.
        self.assertEqual(captured["timeout"], 110.0)

    def test_run_id_defaults_to_latest_across_invocations(self):
        rc1, out1, _ = self._run_cli(
            ["vm2vm", "validate-config", "--config", str(NVIDIA_EXAMPLE), "--run-id", "pinned", "--json"]
        )
        rc2, out2, _ = self._run_cli(
            ["vm2vm", "discover", "--config", str(NVIDIA_EXAMPLE), "--json"]
        )
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual(json.loads(out2)["run_id"], "pinned")

    def test_configure_forwarding_errors_without_representors(self):
        run_id = "no-reps"
        run_dir = labctl.run_dir_for("nvidia-bluefield", run_id)
        labctl.save_stage_json(
            run_dir,
            "discover-representors",
            {"vf_to_representor_map": {"location": "dpu", "mappings": []}},
        )
        rc, out, _ = self._run_cli(
            [
                "vm2vm",
                "configure-forwarding",
                "--config",
                str(NVIDIA_EXAMPLE),
                "--run-id",
                run_id,
                "--json",
            ]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        result = envelope["result"]["result"]
        self.assertFalse(result["success"])
        self.assertTrue(any("representor" in e for e in result["errors"]))

    def test_configure_forwarding_uses_prior_discover_representors_mapping(self):
        run_id = "with-map"
        run_dir = labctl.run_dir_for("nvidia-bluefield", run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        labctl.save_stage_json(
            run_dir,
            "discover-representors",
            {
                "vf_to_representor_map": {
                    "location": "dpu",
                    "mappings": [
                        {
                            "vf_index": 0,
                            "representor_netdev": "pf0vf0",
                            "vf_pci": "0000:03:00.2",
                            "confidence": "high",
                        },
                        {
                            "vf_index": 1,
                            "representor_netdev": "pf0vf1",
                            "vf_pci": "0000:03:00.3",
                            "confidence": "high",
                        },
                    ]
                }
            },
        )
        rc, out, _ = self._run_cli(
            ["vm2vm", "configure-forwarding", "--config", str(NVIDIA_EXAMPLE), "--run-id", run_id, "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        endpoints = envelope["result"]["intent"]["endpoints"]
        representors = {ep["name"]: ep["representor"] for ep in endpoints}
        self.assertEqual(representors["vm1-endpoint"], "pf0vf0")
        self.assertEqual(representors["vm2-endpoint"], "pf0vf1")

    def test_configure_forwarding_rejects_low_confidence_when_required(self):
        run_id = "low-conf"
        run_dir = labctl.run_dir_for("nvidia-bluefield", run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        labctl.save_stage_json(
            run_dir,
            "discover-representors",
            {
                "vf_to_representor_map": {
                    "location": "dpu",
                    "mappings": [
                        {
                            "vf_index": 0,
                            "representor_netdev": None,
                            "dpu_representor_name": "pf0vf0",
                            "confidence": "low",
                        },
                        {
                            "vf_index": 1,
                            "representor_netdev": None,
                            "confidence": "low",
                        },
                    ]
                }
            },
        )
        rc, out, _ = self._run_cli(
            ["vm2vm", "configure-forwarding", "--config", str(NVIDIA_EXAMPLE), "--run-id", run_id, "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        result = envelope["result"]
        self.assertFalse(result["success"])
        self.assertTrue(any("high-confidence" in e or "require_high_confidence" in e for e in result["errors"]))

    def test_invalid_config_blocks_non_validate_stages(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        del config["forwarding"]
        config["dpu"]["num_vfs"] = 4
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            json.dump(config, handle)
            temp_path = handle.name
        try:
            rc, _, err = self._run_cli(
                ["vm2vm", "discover", "--config", temp_path, "--json"]
            )
            self.assertEqual(rc, 1)
            self.assertIn("config is invalid", err)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_destructive_stage_held_at_dry_run_by_default(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "prepare-vfs", "--config", str(NVIDIA_EXAMPLE), "--apply", "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        self.assertTrue(envelope["dry_run"])
        self.assertFalse(envelope["apply"])

    def test_run_all_safe_forces_dry_run_and_covers_every_stage(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "run-all-safe", "--config", str(NVIDIA_EXAMPLE), "--run-id", "safe1", "--apply", "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        stage_names = [s["stage"] for s in envelope["stages"]]
        self.assertEqual(stage_names, list(labctl.RUN_ALL_SAFE_ORDER))
        for stage in envelope["stages"]:
            self.assertTrue(stage["dry_run"])
            self.assertFalse(stage["apply"])

    def test_create_vms_renders_template_when_present(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "create-vms", "--config", str(NVIDIA_EXAMPLE), "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        vms = envelope["result"]["vms"]
        self.assertEqual(len(vms), 2)
        for vm in vms:
            self.assertNotIn("error", vm)
            self.assertIn("xml_preview", vm)

    def test_configure_vm_ips_reports_missing_management_host(self):
        rc, out, _ = self._run_cli(
            ["vm2vm", "configure-vm-ips", "--config", str(NVIDIA_EXAMPLE), "--json"]
        )
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        for vm in envelope["result"]["vms"]:
            self.assertIn("error", vm)
            self.assertIn("management_ip", vm["error"])

    def test_configure_vm_ips_uses_explicit_management_ip(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["vms"][0]["management_ip"] = "192.0.2.99"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            json.dump(config, handle)
            temp_path = handle.name
        try:
            rc, out, _ = self._run_cli(
                ["vm2vm", "configure-vm-ips", "--config", temp_path, "--json"]
            )
            self.assertEqual(rc, 0)
            envelope = json.loads(out)
            vm1 = envelope["result"]["vms"][0]
            self.assertEqual(vm1.get("management_host"), "192.0.2.99")
            self.assertNotIn("error", vm1)
            expected_ssh_user = config["vms"][0]["ssh_user"]
            self.assertEqual(
                vm1["command"]["argv"][:2],
                ["ssh", "%s@192.0.2.99" % expected_ssh_user],
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_unknown_vendor_type_returns_error(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        config["vendor"]["type"] = "not-a-real-vendor"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            json.dump(config, handle)
            temp_path = handle.name
        try:
            rc, _, err = self._run_cli(["vm2vm", "validate-config", "--config", temp_path, "--json"])
            self.assertEqual(rc, 1)
            self.assertIn("unknown vendor backend", err)
        finally:
            Path(temp_path).unlink(missing_ok=True)


class RunTrafficTimeoutTests(unittest.TestCase):
    """run-traffic's iperf3 client runs `-t <duration_seconds>` over SSH,
    so the local subprocess timeout must be duration_seconds plus
    headroom for SSH connect/auth and iperf3's own startup/summary
    overhead -- not commands.DEFAULT_TIMEOUT_SECONDS (30s), which is the
    same value as the default traffic.duration_seconds (30) and so
    guaranteed the client got cut off right as the real test finished."""

    class _Args:
        dry_run = True
        apply = False

    def _stage_run_traffic_calls(self, config):
        from plugins.base.models import CommandResult

        calls: list = []

        def fake_run(argv, *, dry_run=True, timeout=None, reason=None, **kwargs):
            calls.append({"argv": list(argv), "timeout": timeout, "reason": reason})
            return CommandResult(argv=list(argv), executed=False, dry_run=dry_run, reason=reason)

        ctx = labctl.StageContext(
            self._Args(), config, "nvidia-bluefield", None, Path("."), "run-1"
        )
        with unittest.mock.patch.object(labctl, "run", fake_run):
            labctl.stage_run_traffic(ctx)
        return calls

    def _config(self, **traffic_overrides):
        traffic = {"type": "ping and iperf3", "iperf_parallel_streams": 4}
        traffic.update(traffic_overrides)
        return {
            "vms": [
                {"name": "vm1", "management_ip": "192.0.2.10", "ssh_user": "u1"},
                {
                    "name": "vm2",
                    "management_ip": "192.0.2.11",
                    "ssh_user": "u2",
                    "ip_cidr": "198.51.100.12/24",
                },
            ],
            "traffic": traffic,
        }

    def test_iperf3_client_timeout_has_headroom_over_duration_seconds(self):
        calls = self._stage_run_traffic_calls(self._config(duration_seconds=30))
        client_calls = [c for c in calls if "iperf3 client" in (c["reason"] or "")]
        self.assertEqual(len(client_calls), 1)
        self.assertGreater(client_calls[0]["timeout"], 30.0)
        self.assertEqual(client_calls[0]["timeout"], 60.0)

    def test_iperf3_client_timeout_scales_with_custom_duration(self):
        calls = self._stage_run_traffic_calls(self._config(duration_seconds=120))
        client_calls = [c for c in calls if "iperf3 client" in (c["reason"] or "")]
        self.assertEqual(client_calls[0]["timeout"], 150.0)

    def test_ping_and_server_commands_unaffected(self):
        calls = self._stage_run_traffic_calls(self._config(duration_seconds=30))
        other_calls = [c for c in calls if "iperf3 client" not in (c["reason"] or "")]
        self.assertTrue(other_calls)
        for call in other_calls:
            self.assertIsNone(call["timeout"])


if __name__ == "__main__":
    unittest.main()
