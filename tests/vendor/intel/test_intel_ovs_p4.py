"""Intel OVS-as-P4Runtime-client helpers (plugins/intel_ipu/ovs_p4.py).

No live OVS, no live SSH -- ACC SSH is either omitted (proves the "not
executed" honesty path) or monkeypatched via RemoteCommandRunner.run_ssh.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult
from plugins.intel_ipu import ovs_p4


class IntelOvsP4NoSshConfiguredTests(unittest.TestCase):
    """No dpu.management_ip/ssh_user configured: every call must plan,
    never execute, and never fabricate success."""

    def test_probe_capabilities_not_executed_without_acc_ssh(self):
        result = ovs_p4.probe_capabilities({}, dry_run=False)
        self.assertFalse(result.executed)
        self.assertTrue(all(not c.executed for c in result.commands))

    def test_create_bridge_not_executed_without_acc_ssh(self):
        result = ovs_p4.create_bridge({}, "br-int", dry_run=False)
        self.assertFalse(result.executed)

    def test_add_port_not_executed_without_acc_ssh(self):
        result = ovs_p4.add_port({}, "br-int", "acc-pr0", dry_run=False)
        self.assertFalse(result.executed)

    def test_probe_control_plane_live_returns_none_without_acc_ssh(self):
        """Never invents True/False when the ACC is unreachable/unconfigured."""
        self.assertIsNone(ovs_p4.probe_control_plane_live({}))


class IntelOvsP4WithSshTests(unittest.TestCase):
    """Real ACC SSH target configured; RemoteCommandRunner.run_ssh mocked."""

    def _cfg(self):
        return {"dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"}}

    def test_create_bridge_executes_add_br_over_ssh(self):
        with mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh",
            return_value=CommandResult(
                argv=["ovs-vsctl", "--may-exist", "add-br", "br-int"],
                executed=True,
                dry_run=False,
                returncode=0,
                stdout="",
            ),
        ) as run_ssh:
            result = ovs_p4.create_bridge(self._cfg(), "br-int", dry_run=False)
        self.assertTrue(run_ssh.called)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)

    def test_add_port_executes_add_port_over_ssh(self):
        with mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh",
            return_value=CommandResult(
                argv=["ovs-vsctl", "--may-exist", "add-port", "br-int", "acc-pr0"],
                executed=True,
                dry_run=False,
                returncode=0,
                stdout="",
            ),
        ):
            result = ovs_p4.add_port(self._cfg(), "br-int", "acc-pr0", dry_run=False)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)

    def test_add_port_error_on_nonzero_returncode(self):
        with mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh",
            return_value=CommandResult(
                argv=["ovs-vsctl", "--may-exist", "add-port", "br-int", "acc-pr0"],
                executed=True,
                dry_run=False,
                returncode=1,
                stdout="",
                stderr="no such device",
            ),
        ):
            result = ovs_p4.add_port(self._cfg(), "br-int", "acc-pr0", dry_run=False)
        self.assertEqual(result.status, "error")

    def test_probe_control_plane_live_true_when_ssh_succeeds(self):
        with mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh",
            return_value=CommandResult(
                argv=["ovs-vsctl", "get", "Open_vSwitch", ".", "datapath_types"],
                executed=True,
                dry_run=False,
                returncode=0,
                stdout="[system]",
            ),
        ):
            self.assertTrue(ovs_p4.probe_control_plane_live(self._cfg()))

    def test_probe_control_plane_live_none_when_ssh_fails(self):
        with mock.patch(
            "plugins.base.remote.RemoteCommandRunner.run_ssh",
            return_value=CommandResult(
                argv=["ovs-vsctl", "get", "Open_vSwitch", ".", "datapath_types"],
                executed=True,
                dry_run=False,
                returncode=1,
                stderr="database connection failed",
            ),
        ):
            self.assertIsNone(ovs_p4.probe_control_plane_live(self._cfg()))

    def test_dry_run_true_never_executes_even_with_ssh_target(self):
        with mock.patch("plugins.base.remote.RemoteCommandRunner.run_ssh") as run_ssh:
            ovs_p4.create_bridge(self._cfg(), "br-int", dry_run=True)
        # run_ssh is still called (to build the planned command), but the
        # inner plugins.base.commands.run must see dry_run=True and never
        # actually spawn a process; RemoteCommandRunner.run_ssh itself is
        # mocked here so assert it was invoked with dry_run=True.
        self.assertTrue(run_ssh.called)
        _, kwargs = run_ssh.call_args
        self.assertTrue(kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
