"""RemoteCommandRunner unit tests (no SSH, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.remote import RemoteCommandRunner, is_dpu_mode, ssh_params


class RemoteRunnerTests(unittest.TestCase):
    def test_run_local_dry_run_not_executed(self):
        runner = RemoteCommandRunner()
        result = runner.run_local(["ovs-vsctl", "show"], dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.executed)
        self.assertFalse(result.shell)

    def test_run_ssh_dry_run_builds_argv(self):
        runner = RemoteCommandRunner()
        result = runner.run_ssh(
            "192.0.2.10",
            "ubuntu",
            ["ovs-vsctl", "show"],
            key_path="/tmp/id_ed25519",
            port=22,
            dry_run=True,
            sudo=True,
        )
        self.assertFalse(result.executed)
        self.assertIn("ssh", result.argv)
        self.assertIn("ubuntu@192.0.2.10", result.argv)
        self.assertIn("ovs-vsctl", result.argv)
        self.assertIn("sudo", result.argv)
        self.assertNotIn(True, [result.shell])

    def test_copy_and_collect_dry_run(self):
        runner = RemoteCommandRunner()
        up = runner.copy_to_remote(
            "/tmp/a", "/tmp/b", host="192.0.2.10", user="ubuntu", dry_run=True
        )
        down = runner.collect_remote_file(
            "/tmp/b", "/tmp/c", host="192.0.2.10", user="ubuntu", dry_run=True
        )
        self.assertFalse(up.executed)
        self.assertFalse(down.executed)
        self.assertEqual(up.argv[0], "scp")

    def test_dpu_mode_helper(self):
        self.assertTrue(
            is_dpu_mode({"nvidia": {"mode": "dpu", "ovs_location": "dpu"}})
        )
        self.assertFalse(
            is_dpu_mode({"nvidia": {"mode": "host-switchdev", "ovs_location": "host"}})
        )
        params = ssh_params({"dpu": {"management_ip": "192.0.2.10", "ssh_user": "ubuntu"}})
        self.assertEqual(params["host"], "192.0.2.10")

    def test_ssh_params_command_timeout_defaults_to_30(self):
        params = ssh_params({"dpu": {"management_ip": "192.0.2.10", "ssh_user": "ubuntu"}})
        self.assertEqual(params["command_timeout_seconds"], 30.0)

    def test_ssh_params_command_timeout_is_configurable(self):
        params = ssh_params(
            {"dpu": {"management_ip": "192.0.2.10", "ssh_user": "ubuntu", "command_timeout_seconds": 60}}
        )
        self.assertEqual(params["command_timeout_seconds"], 60.0)

    def test_ssh_argv_sets_connect_timeout(self):
        runner = RemoteCommandRunner()
        result = runner.run_ssh("192.0.2.10", "ubuntu", ["ovs-vsctl", "show"], dry_run=True)
        argv_str = " ".join(result.argv)
        self.assertIn("ConnectTimeout=10", argv_str)
