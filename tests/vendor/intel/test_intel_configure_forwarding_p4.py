"""Intel configure_forwarding() builds a P4Runtime-shaped placeholder when
capability was proven via the P4 control plane, not the OPI one -- these
are alternate paths, not a fallback of each other (no hardware, no gRPC)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
    OperationResult,
)
from plugins.intel_ipu.backend import IntelIpuBackend


def _intent() -> ForwardingIntent:
    return ForwardingIntent(
        name="odse-vm2vm-intel",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor="p4repr0"),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor="p4repr1"),
        ],
        policy=ForwardingPolicy(bidirectional=True),
        offload=ForwardingOffloadRequest(requested=False),
    )


def _proven(control_plane: str) -> OperationResult:
    return OperationResult(
        operation="check_vm_to_vm_forwarding_capability",
        vendor="intel",
        status="ok",
        message="proven (test)",
        executed=True,
        details={"capability_proven": True, "control_plane": control_plane, "facts": {}},
    )


class IntelConfigureForwardingP4Tests(unittest.TestCase):
    def test_p4_control_plane_builds_p4runtime_placeholder(self):
        backend = IntelIpuBackend(config={"p4": {"endpoint": "infrap4d.example.invalid:9339"}})
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven("p4"),
        ):
            result = backend.configure_forwarding(_intent())
        self.assertIn("p4_placeholder", result.details)
        self.assertNotIn("opi_placeholder", result.details)
        self.assertIn("P4Runtime", result.control_entity)
        self.assertFalse(result.success)  # still dry-run/placeholder, not submitted

    def test_opi_control_plane_still_builds_opi_placeholder(self):
        backend = IntelIpuBackend(config={"opi": {"endpoint": "https://opi.example.invalid"}})
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven("opi"),
        ):
            result = backend.configure_forwarding(_intent())
        self.assertIn("opi_placeholder", result.details)
        self.assertNotIn("p4_placeholder", result.details)
        self.assertIn("OPI", result.control_entity)


class IntelConfigureForwardingP4ApplyTests(unittest.TestCase):
    """--apply (dry_run=False): actually calls P4RuntimeClient.write_table_entry()
    instead of stopping at the placeholder -- see plugins/intel_ipu/backend.py's
    _submit_p4_forwarding(). Never invents a FLOW string: refuses unless
    p4.forwarding_entries has a real {table, flow} for every endpoint.
    """

    def test_apply_refuses_without_configured_table_flow_for_any_endpoint(self):
        backend = IntelIpuBackend(config={"p4": {"mode": "mock", "forwarding_entries": []}})
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven("p4"),
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertFalse(result.success)
        self.assertIn("never invents a P4 FLOW string", result.message)
        self.assertIn("vm1-endpoint", result.errors[0])
        self.assertIn("vm2-endpoint", result.errors[0])
        # nothing submitted -- refusal happens before any write attempt
        self.assertNotIn("p4_submitted_entries", result.details)

    def test_apply_refuses_when_only_some_endpoints_are_configured(self):
        backend = IntelIpuBackend(
            config={
                "p4": {
                    "mode": "mock",
                    "forwarding_entries": [
                        {"vf_index": 0, "table": "l2_fwd", "flow": "port=0,action=set_egress(1)"}
                    ],
                }
            }
        )
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven("p4"),
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertFalse(result.success)
        self.assertIn("vm2-endpoint", result.errors[0])
        self.assertNotIn("vm1-endpoint", result.errors[0])

    def test_apply_submits_real_write_table_entry_calls_when_fully_configured(self):
        backend = IntelIpuBackend(
            config={
                "p4": {
                    "mode": "mock",
                    "forwarding_entries": [
                        {"vf_index": 0, "table": "l2_fwd", "flow": "port=0,action=set_egress(1)"},
                        {"vf_index": 1, "table": "l2_fwd", "flow": "port=1,action=set_egress(0)"},
                    ],
                }
            }
        )
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven("p4"),
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertTrue(result.success)
        self.assertIn("submitted 2 P4Runtime table entries", result.message)
        submitted = result.details["p4_submitted_entries"]
        self.assertEqual(len(submitted), 2)
        self.assertTrue(all(e["status"] == "ok" for e in submitted))

    def test_apply_via_cli_mode_without_acc_ssh_reports_not_executed_not_success(self):
        """cli mode with no ACC SSH target configured plans the p4rt-ctl
        command but does not execute it (RemoteCommandRunner refuses) --
        this must surface as success=False, never a fabricated success."""
        backend = IntelIpuBackend(
            config={
                "p4": {
                    "mode": "cli",
                    "forwarding_entries": [
                        {"vf_index": 0, "table": "l2_fwd", "flow": "port=0,action=set_egress(1)"},
                        {"vf_index": 1, "table": "l2_fwd", "flow": "port=1,action=set_egress(0)"},
                    ],
                },
                "dpu": {},
            }
        )
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven("p4"),
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertFalse(result.success)
        submitted = result.details["p4_submitted_entries"]
        self.assertTrue(all(e["executed"] is False for e in submitted))


if __name__ == "__main__":
    unittest.main()
