"""Intel configure_forwarding() via the ovs control plane (P4Runtime-backed
ovs-vswitchd, not p4rt-ctl -- see plugins/intel_ipu/ovs_p4.py). Dry-run
builds a placeholder; --apply actually calls ovs-vsctl over ACC SSH."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (
    CommandResult,
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
    OperationResult,
)
from plugins.intel_ipu.backend import IntelIpuBackend


def _intent(representors=("acc-pr0", "acc-pr1")) -> ForwardingIntent:
    return ForwardingIntent(
        name="odse-vm2vm-intel",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor=representors[0]),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor=representors[1]),
        ],
        policy=ForwardingPolicy(bidirectional=True),
        offload=ForwardingOffloadRequest(requested=False),
    )


def _proven_ovs() -> OperationResult:
    return OperationResult(
        operation="check_vm_to_vm_forwarding_capability",
        vendor="intel",
        status="ok",
        message="proven (test)",
        executed=True,
        details={"capability_proven": True, "control_plane": "ovs", "facts": {}},
    )


def _ssh_ok(host, user, argv, **_kw):
    return CommandResult(argv=list(argv), executed=True, dry_run=False, returncode=0, stdout="")


class IntelConfigureForwardingOvsDryRunTests(unittest.TestCase):
    def test_ovs_control_plane_builds_ovs_placeholder_dry_run(self):
        backend = IntelIpuBackend(config={"intel": {"control_plane": "ovs"}})
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven_ovs(),
        ):
            result = backend.configure_forwarding(_intent())
        self.assertIn("ovs_placeholder", result.details)
        self.assertNotIn("p4_placeholder", result.details)
        self.assertNotIn("opi_placeholder", result.details)
        self.assertIn("OVS", result.control_entity)
        self.assertFalse(result.success)  # still dry-run/placeholder, not submitted


class IntelConfigureForwardingOvsApplyTests(unittest.TestCase):
    """--apply (dry_run=False): actually calls ovs-vsctl over ACC SSH via
    plugins/intel_ipu/ovs_p4.py. Never invents a representor name."""

    def test_apply_refuses_without_representor_for_any_endpoint(self):
        backend = IntelIpuBackend(config={"intel": {"control_plane": "ovs"}})
        intent = _intent(representors=(None, None))
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven_ovs(),
        ):
            result = backend.configure_forwarding(intent, dry_run=False)
        self.assertFalse(result.success)
        self.assertIn("no representor known", result.message)
        self.assertIn("vm1-endpoint", result.errors[0])
        self.assertIn("vm2-endpoint", result.errors[0])

    def test_apply_refuses_when_only_some_endpoints_have_representors(self):
        backend = IntelIpuBackend(config={"intel": {"control_plane": "ovs"}})
        intent = _intent(representors=("acc-pr0", None))
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven_ovs(),
        ):
            result = backend.configure_forwarding(intent, dry_run=False)
        self.assertFalse(result.success)
        self.assertIn("vm2-endpoint", result.errors[0])
        self.assertNotIn("vm1-endpoint", result.errors[0])

    def test_apply_without_acc_ssh_reports_not_executed_not_success(self):
        """No dpu.management_ip/ssh_user configured: commands are planned
        but never executed -- must surface as success=False, never a
        fabricated success."""
        backend = IntelIpuBackend(config={"intel": {"control_plane": "ovs"}, "dpu": {}})
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven_ovs(),
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertFalse(result.success)
        self.assertTrue(all(not c.executed for c in result.commands))

    def test_apply_configures_bridge_and_ports_over_ssh_when_reachable(self):
        backend = IntelIpuBackend(
            config={
                "intel": {"control_plane": "ovs"},
                "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"},
                "ovs": {"bridge": "br-int"},
            }
        )
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven_ovs(),
        ), mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh", side_effect=_ssh_ok
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertTrue(result.success)
        self.assertEqual(result.details["ovs_bridge"], "br-int")
        self.assertEqual(sorted(result.details["ovs_configured_ports"]), ["acc-pr0", "acc-pr1"])
        # simple bidirectional bridging needs no explicit flow
        self.assertEqual(result.details["ovs_configured_flows"], [])

    def test_apply_surfaces_error_when_add_port_fails_over_ssh(self):
        def _fail_second_port(host, user, argv, **_kw):
            if "acc-pr1" in argv:
                return CommandResult(
                    argv=list(argv), executed=True, dry_run=False, returncode=1, stderr="no such device"
                )
            return _ssh_ok(host, user, argv)

        backend = IntelIpuBackend(
            config={
                "intel": {"control_plane": "ovs"},
                "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"},
            }
        )
        with mock.patch(
            "plugins.intel_ipu.vm_to_vm_capability.check_vm_to_vm_forwarding_capability",
            return_value=_proven_ovs(),
        ), mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh", side_effect=_fail_second_port
        ):
            result = backend.configure_forwarding(_intent(), dry_run=False)
        self.assertFalse(result.success)
        self.assertTrue(result.errors)


class IntelConfigureOvsBridgePrepareStageTests(unittest.TestCase):
    """prepare-ovs / configure_ovs_bridge() -- always plans (dry_run=True),
    same convention as NVIDIA's prepare-ovs; real creation happens under
    configure-forwarding --apply (see IntelConfigureForwardingOvsApplyTests)."""

    def test_refuses_when_control_plane_is_not_ovs(self):
        backend = IntelIpuBackend(config={"intel": {"control_plane": "p4"}})
        result = backend.configure_ovs_bridge("br-int")
        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.executed)

    def test_refuses_by_default_when_control_plane_unset(self):
        backend = IntelIpuBackend(config={})
        result = backend.configure_ovs_bridge("br-int")
        self.assertEqual(result.status, "skipped")

    def test_plans_bridge_when_control_plane_is_ovs(self):
        backend = IntelIpuBackend(
            config={
                "intel": {"control_plane": "ovs"},
                "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"},
            }
        )
        result = backend.configure_ovs_bridge("br-int")
        self.assertEqual(result.operation, "configure_ovs_bridge")
        self.assertFalse(result.executed)  # always dry_run=True at this stage
        self.assertEqual(result.details.get("bridge"), "br-int")


if __name__ == "__main__":
    unittest.main()
