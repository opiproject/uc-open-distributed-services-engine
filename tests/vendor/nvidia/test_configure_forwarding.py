"""NVIDIA configure_forwarding() — dry-run OVS/representor planning (no live OVS)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (
    CommandResult,
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
)
from plugins.nvidia_bluefield.backend import NvidiaBlueFieldBackend


def _intent(**overrides) -> ForwardingIntent:
    base = dict(
        name="odse-vm2vm-nvidia",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor="pf0vf0"),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor="pf0vf1"),
        ],
        policy=ForwardingPolicy(action="allow", bidirectional=True),
        offload=ForwardingOffloadRequest(requested=True),
        explicit_flows={"enabled": False},
        bridge="br-int",
        details={"datapath_type": "doca"},
    )
    base.update(overrides)
    return ForwardingIntent(**base)


class _FakeRemote:
    """Stands in for plugins.base.remote.RemoteCommandRunner: records every
    run_ssh() call and returns a canned CommandResult reflecting whatever
    dry_run it was actually called with, without touching real SSH."""

    def __init__(self) -> None:
        self.calls: list = []

    def run_ssh(self, host, user, command, key_path=None, port=22, dry_run=True, *, timeout=30.0, reason=None, sudo=False):
        self.calls.append(
            {"command": list(command), "dry_run": dry_run, "reason": reason, "timeout": timeout}
        )
        return CommandResult(
            argv=["ssh", "%s@%s" % (user, host), "--"] + list(command),
            executed=not dry_run,
            dry_run=dry_run,
            returncode=0 if not dry_run else None,
            stdout="",
            stderr="",
        )


class NvidiaConfigureForwardingTests(unittest.TestCase):
    def setUp(self):
        self.backend = NvidiaBlueFieldBackend()

    def test_missing_representor_fails_cleanly(self):
        intent = _intent(
            endpoints=[
                ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor=None),
                ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor="pf0vf1"),
            ]
        )
        result = self.backend.configure_forwarding(intent)
        self.assertFalse(result.success)
        self.assertTrue(any("representor missing" in e for e in result.errors))

    def test_two_endpoint_bridge_forwarding_is_planned(self):
        result = self.backend.configure_forwarding(_intent())
        self.assertTrue(result.success)
        self.assertEqual(result.vendor, "nvidia")
        self.assertEqual(result.forwarding_entity, "NVIDIA BlueField/ConnectX E-Switch")
        self.assertEqual(
            result.control_entity,
            "labctl -> nvidia backend -> DPU-side OVS/OVS-DOCA -> BlueField E-Switch",
        )
        self.assertNotIn("host path", result.forwarding_entity)
        self.assertIn("pf0vf0", result.configured_ports)
        self.assertIn("pf0vf1", result.configured_ports)
        self.assertTrue(result.offload_requested)
        self.assertFalse(result.offload_confirmed)
        # dry-run always: no ovs-vsctl/ovs-ofctl was actually executed
        self.assertTrue(all(not cmd.executed for cmd in result.commands))
        self.assertTrue(all("ssh" in (cmd.argv or []) for cmd in result.commands))

    def test_host_switchdev_profile_uses_host_path(self):
        backend = NvidiaBlueFieldBackend(
            lab_config={
                "nvidia": {
                    "mode": "host-switchdev",
                    "ovs_location": "host",
                    "allow_host_ovs_for_forwarding": True,
                }
            }
        )
        result = backend.configure_forwarding(_intent(details={"datapath_type": "system"}))
        self.assertTrue(result.success)
        self.assertEqual(result.forwarding_entity, "NVIDIA BlueField/ConnectX E-Switch (host path)")
        self.assertIn("host OVS", result.control_entity)

    def test_explicit_flows_adds_bidirectional_flows(self):
        intent = _intent(explicit_flows={"enabled": True})
        result = self.backend.configure_forwarding(intent)
        self.assertTrue(result.success)
        self.assertEqual(len(result.configured_flows), 2)
        self.assertIn("in_port=pf0vf0,actions=output:pf0vf1", result.configured_flows)
        self.assertIn("in_port=pf0vf1,actions=output:pf0vf0", result.configured_flows)
        self.assertTrue(any("ovs-ofctl" in w or "syntax" in w for w in result.warnings))

    def test_offload_not_requested_leaves_offload_configured_none(self):
        intent = _intent(offload=ForwardingOffloadRequest(requested=False))
        result = self.backend.configure_forwarding(intent)
        self.assertFalse(result.offload_requested)
        self.assertIsNone(result.offload_configured)

    def test_dry_run_default_is_true_when_omitted(self):
        # No dry_run kwarg at all -- must behave exactly like the explicit
        # dry_run=True tests above (backward compatible with callers/tests
        # written before dry_run existed on this method).
        result = self.backend.configure_forwarding(_intent())
        self.assertTrue(all(not cmd.executed for cmd in result.commands))

    def test_dry_run_false_actually_executes_ovs_commands(self):
        # system (kernel OVS-TC) datapath, matching the common lab config --
        # avoids the doca-capability-probe branch (a separate concern) so
        # this test is purely about dry_run threading through to real
        # execution.
        backend = NvidiaBlueFieldBackend(
            lab_config={"dpu": {"management_ip": "192.0.2.10", "ssh_user": "root"}}
        )
        fake_remote = _FakeRemote()
        backend._remote = fake_remote
        result = backend.configure_forwarding(
            _intent(details={"datapath_type": "system"}), dry_run=False
        )
        self.assertTrue(result.success)
        self.assertTrue(result.commands)
        self.assertTrue(all(cmd.executed for cmd in result.commands))
        self.assertTrue(fake_remote.calls)
        self.assertTrue(all(not c["dry_run"] for c in fake_remote.calls))
        self.assertIn("configured OVS bridge/representor forwarding on the DPU", result.message)
        self.assertTrue(result.offload_requested)
        self.assertTrue(result.offload_confirmed)

    def test_dry_run_false_with_failing_command_reports_error_not_confirmed(self):
        class _FailingRemote(_FakeRemote):
            def run_ssh(self, host, user, command, key_path=None, port=22, dry_run=True, *, timeout=30.0, reason=None, sudo=False):
                self.calls.append({"command": list(command), "dry_run": dry_run, "reason": reason})
                return CommandResult(
                    argv=["ssh", "%s@%s" % (user, host), "--"] + list(command),
                    executed=not dry_run,
                    dry_run=dry_run,
                    returncode=1 if not dry_run else None,
                    stdout="",
                    stderr="ovs-vsctl: unix:/var/run/openvswitch/db.sock: database connection failed",
                )

        backend = NvidiaBlueFieldBackend(
            lab_config={"dpu": {"management_ip": "192.0.2.10", "ssh_user": "root"}}
        )
        backend._remote = _FailingRemote()
        result = backend.configure_forwarding(
            _intent(details={"datapath_type": "system"}), dry_run=False
        )
        self.assertFalse(result.success)
        self.assertFalse(result.offload_confirmed)
        self.assertIn("attempted to configure OVS bridge/representor forwarding on the DPU", result.message)

    def test_dry_run_false_uses_dpu_command_timeout_seconds_as_floor(self):
        # ovs.py's helpers hardcode timeout=15.0 per call; a slow-link DPU
        # (docs/operations/dpu-remote-execution.md) needs
        # dpu.command_timeout_seconds honored instead, same as
        # discover-representors.
        backend = NvidiaBlueFieldBackend(
            lab_config={
                "dpu": {
                    "management_ip": "192.0.2.10",
                    "ssh_user": "root",
                    "command_timeout_seconds": 45,
                }
            }
        )
        fake_remote = _FakeRemote()
        backend._remote = fake_remote
        backend.configure_forwarding(_intent(details={"datapath_type": "system"}), dry_run=False)
        self.assertTrue(fake_remote.calls)
        self.assertTrue(all(c["timeout"] == 45 for c in fake_remote.calls))

    def test_dry_run_false_respects_larger_explicit_timeout_than_configured(self):
        backend = NvidiaBlueFieldBackend(
            lab_config={
                "dpu": {
                    "management_ip": "192.0.2.10",
                    "ssh_user": "root",
                    "command_timeout_seconds": 5,
                }
            }
        )
        fake_remote = _FakeRemote()
        backend._remote = fake_remote
        backend.configure_forwarding(_intent(details={"datapath_type": "system"}), dry_run=False)
        # ovs.py's own hardcoded 15.0 per-call default is larger than the
        # (deliberately tiny, for this test) configured 5s -- the larger
        # value should win, never silently shrinking a caller's timeout.
        self.assertTrue(all(c["timeout"] == 15.0 for c in fake_remote.calls))


if __name__ == "__main__":
    unittest.main()
