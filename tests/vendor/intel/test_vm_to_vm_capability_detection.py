"""Intel VM-to-VM capability detection (no OVS, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import OperationResult
from plugins.intel_ipu.vm_to_vm_capability import (
    INTEL_VM_TO_VM_MESSAGE,
    INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED,
    check_vm_to_vm_forwarding_capability,
)


class VmToVmCapabilityTests(unittest.TestCase):
    def test_unproven_returns_required_status_and_message(self):
        result = check_vm_to_vm_forwarding_capability({"opi": {"endpoint": "https://opi.example.invalid"}})
        self.assertEqual(result.details["status"], INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED)
        self.assertEqual(result.message, INTEL_VM_TO_VM_MESSAGE)
        self.assertFalse(result.details["capability_proven"])

    def test_proven_only_with_complete_samples(self):
        result = check_vm_to_vm_forwarding_capability(
            {"opi": {"endpoint": "https://opi.example.invalid"}},
            samples={
                "vm_to_vm_forwarding_supported": True,
                "vfs_manageable": True,
                "vf_endpoint_map": {"0": "ep0"},
                "opi_l2_forwarding_api": True,
            },
        )
        self.assertTrue(result.details["capability_proven"])
        self.assertEqual(result.details["control_plane"], "opi")

    def test_proven_via_p4_pipeline_without_opi(self):
        """P4Runtime/IPDK is an alternate control plane to OPI, not a
        fallback of it -- proving it alone (with no opi_l2_forwarding_api
        sample) is sufficient."""
        result = check_vm_to_vm_forwarding_capability(
            {"p4": {"endpoint": "infrap4d.example.invalid:9339"}},
            samples={
                "vm_to_vm_forwarding_supported": True,
                "vfs_manageable": True,
                "vf_endpoint_map": {"0": "ep0"},
                "p4_pipeline_configured": True,
            },
        )
        self.assertTrue(result.details["capability_proven"])
        self.assertEqual(result.details["control_plane"], "p4")

    def test_p4_live_probe_not_run_when_mode_is_mock(self):
        """p4.mode defaults to mock; the live SSH probe must never fire
        for mock/grpc modes, only explicit cli + dry_run_default=false."""
        with mock.patch("plugins.intel_ipu.p4runtime.P4RuntimeClient.get_forwarding_pipeline_config") as probe:
            check_vm_to_vm_forwarding_capability({"p4": {"mode": "mock"}})
            probe.assert_not_called()

    def test_p4_live_probe_not_run_without_explicit_dry_run_default_false(self):
        """p4.mode=cli alone is not enough -- remote_execution.dry_run_default
        must be explicitly false, same dual-gate convention as NVIDIA's
        DPU-mode SSH probing."""
        with mock.patch("plugins.intel_ipu.p4runtime.P4RuntimeClient.get_forwarding_pipeline_config") as probe:
            check_vm_to_vm_forwarding_capability({"p4": {"mode": "cli"}})
            probe.assert_not_called()

    def test_p4_live_probe_fills_fact_when_opted_in_and_pipeline_found(self):
        cfg = {
            "p4": {"mode": "cli", "device_id": "br0"},
            "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"},
            "remote_execution": {"dry_run_default": False},
        }
        live_result = OperationResult(
            operation="p4_get_forwarding_pipeline_config",
            vendor="intel",
            status="ok",
            executed=True,
            details={"pipeline_configured": True},
        )
        with mock.patch(
            "plugins.intel_ipu.p4runtime.P4RuntimeClient.get_forwarding_pipeline_config",
            return_value=live_result,
        ):
            result = check_vm_to_vm_forwarding_capability(
                cfg,
                samples={
                    "vm_to_vm_forwarding_supported": True,
                    "vfs_manageable": True,
                    "vf_endpoint_map": {"0": "ep0"},
                    # p4_pipeline_configured intentionally omitted -- must
                    # come from the live probe, not a sample override.
                },
            )
        self.assertTrue(result.details["capability_proven"])
        self.assertEqual(result.details["control_plane"], "p4")

    def test_explicit_sample_false_overrides_live_probe(self):
        """An explicit samples={'p4_pipeline_configured': False} must win
        over the live probe -- samples always take precedence."""
        cfg = {
            "p4": {"mode": "cli"},
            "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"},
            "remote_execution": {"dry_run_default": False},
        }
        live_result = OperationResult(
            operation="p4_get_forwarding_pipeline_config",
            vendor="intel",
            status="ok",
            executed=True,
            details={"pipeline_configured": True},
        )
        with mock.patch(
            "plugins.intel_ipu.p4runtime.P4RuntimeClient.get_forwarding_pipeline_config",
            return_value=live_result,
        ) as probe:
            result = check_vm_to_vm_forwarding_capability(
                cfg, samples={"p4_pipeline_configured": False}
            )
            probe.assert_not_called()
        self.assertFalse(result.details["facts"]["p4_or_pipeline_path"])

    def test_proven_via_ovs_p4_control_plane_without_opi_or_p4(self):
        """OVS-as-P4Runtime-client is an alternate, independent control
        plane too -- proving it alone (no opi_l2_forwarding_api or
        p4_pipeline_configured sample) is sufficient."""
        result = check_vm_to_vm_forwarding_capability(
            {"intel": {"control_plane": "ovs"}},
            samples={
                "vm_to_vm_forwarding_supported": True,
                "vfs_manageable": True,
                "vf_endpoint_map": {"0": "ep0"},
                "ovs_p4_control_plane": True,
            },
        )
        self.assertTrue(result.details["capability_proven"])
        self.assertEqual(result.details["control_plane"], "ovs")

    def test_opi_wins_over_ovs_when_both_proven(self):
        result = check_vm_to_vm_forwarding_capability(
            {},
            samples={
                "vm_to_vm_forwarding_supported": True,
                "vfs_manageable": True,
                "vf_endpoint_map": {"0": "ep0"},
                "opi_l2_forwarding_api": True,
                "ovs_p4_control_plane": True,
            },
        )
        self.assertEqual(result.details["control_plane"], "opi")

    def test_p4_wins_over_ovs_when_both_proven_and_opi_is_not(self):
        result = check_vm_to_vm_forwarding_capability(
            {},
            samples={
                "vm_to_vm_forwarding_supported": True,
                "vfs_manageable": True,
                "vf_endpoint_map": {"0": "ep0"},
                "p4_pipeline_configured": True,
                "ovs_p4_control_plane": True,
            },
        )
        self.assertEqual(result.details["control_plane"], "p4")

    def test_ovs_live_probe_not_run_when_control_plane_is_not_ovs(self):
        with mock.patch("plugins.intel_ipu.ovs_p4.probe_control_plane_live") as probe:
            check_vm_to_vm_forwarding_capability(
                {"intel": {"control_plane": "p4"}, "remote_execution": {"dry_run_default": False}}
            )
            probe.assert_not_called()

    def test_ovs_live_probe_not_run_without_explicit_dry_run_default_false(self):
        """intel.control_plane=ovs alone is not enough -- same dual-gate
        convention as the p4 live probe."""
        with mock.patch("plugins.intel_ipu.ovs_p4.probe_control_plane_live") as probe:
            check_vm_to_vm_forwarding_capability({"intel": {"control_plane": "ovs"}})
            probe.assert_not_called()

    def test_ovs_live_probe_fills_fact_when_opted_in_and_reachable(self):
        cfg = {
            "intel": {"control_plane": "ovs"},
            "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"},
            "remote_execution": {"dry_run_default": False},
        }
        with mock.patch("plugins.intel_ipu.ovs_p4.probe_control_plane_live", return_value=True):
            result = check_vm_to_vm_forwarding_capability(
                cfg,
                samples={
                    "vm_to_vm_forwarding_supported": True,
                    "vfs_manageable": True,
                    "vf_endpoint_map": {"0": "ep0"},
                    # ovs_p4_control_plane intentionally omitted -- must come
                    # from the live probe, not a sample override.
                },
            )
        self.assertTrue(result.details["capability_proven"])
        self.assertEqual(result.details["control_plane"], "ovs")

    def test_explicit_sample_false_overrides_ovs_live_probe(self):
        cfg = {
            "intel": {"control_plane": "ovs"},
            "remote_execution": {"dry_run_default": False},
        }
        with mock.patch(
            "plugins.intel_ipu.ovs_p4.probe_control_plane_live", return_value=True
        ) as probe:
            result = check_vm_to_vm_forwarding_capability(
                cfg, samples={"ovs_p4_control_plane": False}
            )
            probe.assert_not_called()
        self.assertFalse(result.details["facts"]["ovs_p4_control_plane"])

    def test_p4_endpoint_alone_avoids_opi_endpoint_required_error(self):
        """A configured p4.endpoint should not force the OPI-specific
        endpoint-required error when nothing is proven yet."""
        result = check_vm_to_vm_forwarding_capability(
            {"p4": {"endpoint": "infrap4d.example.invalid:9339"}}
        )
        self.assertFalse(result.details["capability_proven"])
        self.assertNotIn("OPI_ENDPOINT_REQUIRED", result.details["errors"])


if __name__ == "__main__":
    unittest.main()
