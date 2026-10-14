"""verify-vms stage: read-only check between create-vms and attach-vfs.

Exercises `_verify_one_vm` with an injected fake `run_fn` (no real virsh/
ssh needed) plus `stage_verify_vms` end-to-end aggregation. See
docs/operations/lab-runbook-vm2vm-single-dpu.md and
docs/operations/labctl-usage.md for why this stage exists: `virsh
domstate` right now, not a possibly-stale `virsh start` result, plus an
independent SSH reachability probe on the management NIC.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("odse_labctl_verify", ROOT / "tools" / "labctl.py")
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)


def _executed(argv: List[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        argv=argv,
        executed=True,
        dry_run=False,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class _FakeRunner:
    """Routes `virsh domstate` and `ssh` calls to canned CommandResults."""

    def __init__(self, domstate: CommandResult, ssh: CommandResult = None):
        self.domstate = domstate
        self.ssh = ssh
        self.calls: List[List[str]] = []

    def __call__(self, argv: List[str], **kwargs) -> CommandResult:
        self.calls.append(list(argv))
        if argv[0] == "virsh":
            return self.domstate
        if argv[0] == "ssh":
            assert self.ssh is not None, "ssh should not have been called"
            return self.ssh
        raise AssertionError("unexpected argv: %r" % argv)


def _always_present(_name: str) -> bool:
    """Fake command_exists_fn: pretends virsh/ssh are both on PATH, so
    tests exercise the real run_fn-dispatch logic instead of the
    graceful-degradation branch (which is covered separately below)."""
    return True


class VerifyOneVmTests(unittest.TestCase):
    def test_missing_name_is_a_clean_error(self):
        entry = labctl._verify_one_vm({})
        self.assertIsNone(entry["running"])
        self.assertIsNone(entry["ssh_reachable"])
        self.assertIn("missing name", entry["error"])

    def test_not_running_skips_ssh_probe_entirely(self):
        runner = _FakeRunner(domstate=_executed(["virsh", "domstate", "vm1"], 0, "shut off\n"))
        entry = labctl._verify_one_vm(
            {"name": "vm1", "ssh_user": "odse"}, run_fn=runner, command_exists_fn=_always_present
        )
        self.assertEqual(entry["domstate"], "shut off")
        self.assertFalse(entry["running"])
        self.assertIsNone(entry["ssh_reachable"])
        # Only one call: the ssh probe must never fire for a non-running domain.
        self.assertEqual(len(runner.calls), 1)

    def test_domstate_query_failure_is_unknown_not_false(self):
        runner = _FakeRunner(
            domstate=CommandResult(
                argv=["virsh", "domstate", "vm1"],
                executed=False,
                dry_run=False,
                stderr="failed to get domain 'vm1'",
            )
        )
        entry = labctl._verify_one_vm({"name": "vm1"}, run_fn=runner, command_exists_fn=_always_present)
        self.assertIsNone(entry["running"])
        self.assertIsNone(entry["ssh_reachable"])
        self.assertIn("could not query domain state", entry["message"])

    def test_running_but_no_management_host_reports_guest_ssh_required(self):
        runner = _FakeRunner(domstate=_executed(["virsh", "domstate", "vm1"], 0, "running\n"))
        entry = labctl._verify_one_vm(
            {"name": "vm1", "ssh_user": "odse"}, run_fn=runner, command_exists_fn=_always_present
        )
        self.assertTrue(entry["running"])
        self.assertIsNone(entry["ssh_reachable"])
        self.assertEqual(entry.get("code"), labctl.GUEST_SSH_REQUIRED)

    def test_running_and_ssh_reachable_is_all_green(self):
        runner = _FakeRunner(
            domstate=_executed(["virsh", "domstate", "vm1"], 0, "running\n"),
            ssh=_executed(["ssh"], 0, "odse-verify-vms-ok\n"),
        )
        entry = labctl._verify_one_vm(
            {"name": "vm1", "ssh_user": "odse", "management_ip": "192.0.2.50"},
            run_fn=runner,
            command_exists_fn=_always_present,
        )
        self.assertTrue(entry["running"])
        self.assertTrue(entry["ssh_reachable"])
        self.assertEqual(entry["management_host"], "192.0.2.50")
        self.assertNotIn("message", entry)
        # ssh must be invoked with a bounded, non-interactive connect timeout.
        ssh_call = runner.calls[-1]
        self.assertIn("BatchMode=yes", " ".join(ssh_call))
        self.assertIn("ConnectTimeout=5", " ".join(ssh_call))

    def test_running_but_ssh_probe_fails_is_not_reachable(self):
        runner = _FakeRunner(
            domstate=_executed(["virsh", "domstate", "vm1"], 0, "running\n"),
            ssh=CommandResult(argv=["ssh"], executed=True, dry_run=False, returncode=255, stderr="Connection refused"),
        )
        entry = labctl._verify_one_vm(
            {"name": "vm1", "ssh_user": "odse", "management_ip": "192.0.2.50"},
            run_fn=runner,
            command_exists_fn=_always_present,
        )
        self.assertTrue(entry["running"])
        self.assertFalse(entry["ssh_reachable"])
        self.assertIn("still be booting", entry["message"])
        # No ssh_key_path configured -> message should point at that being
        # the likely gap, not silently say nothing about identity at all.
        self.assertIn("ssh_key_path", entry["message"])

    def test_ssh_key_path_is_passed_to_ssh_with_dash_i(self):
        runner = _FakeRunner(
            domstate=_executed(["virsh", "domstate", "vm1"], 0, "running\n"),
            ssh=_executed(["ssh"], 0, "odse-verify-vms-ok\n"),
        )
        entry = labctl._verify_one_vm(
            {
                "name": "vm1",
                "ssh_user": "odse",
                "management_ip": "192.0.2.50",
                "ssh_key_path": "/home/lab/.ssh/odse_vm2vm_id_ed25519",
            },
            run_fn=runner,
            command_exists_fn=_always_present,
        )
        self.assertTrue(entry["ssh_reachable"])
        ssh_call = runner.calls[-1]
        self.assertIn("-i", ssh_call)
        self.assertEqual(ssh_call[ssh_call.index("-i") + 1], "/home/lab/.ssh/odse_vm2vm_id_ed25519")

    def test_no_ssh_key_path_omits_dash_i(self):
        runner = _FakeRunner(
            domstate=_executed(["virsh", "domstate", "vm1"], 0, "running\n"),
            ssh=_executed(["ssh"], 0, "odse-verify-vms-ok\n"),
        )
        labctl._verify_one_vm(
            {"name": "vm1", "ssh_user": "odse", "management_ip": "192.0.2.50"},
            run_fn=runner,
            command_exists_fn=_always_present,
        )
        ssh_call = runner.calls[-1]
        self.assertNotIn("-i", ssh_call)

    def test_placeholder_ssh_key_path_is_treated_as_unset(self):
        runner = _FakeRunner(
            domstate=_executed(["virsh", "domstate", "vm1"], 0, "running\n"),
            ssh=_executed(["ssh"], 0, "odse-verify-vms-ok\n"),
        )
        labctl._verify_one_vm(
            {
                "name": "vm1",
                "ssh_user": "odse",
                "management_ip": "192.0.2.50",
                "ssh_key_path": "<PATH_TO_PRIVATE_KEY>",
            },
            run_fn=runner,
            command_exists_fn=_always_present,
        )
        ssh_call = runner.calls[-1]
        self.assertNotIn("-i", ssh_call)


class VmSshIdentityArgsTests(unittest.TestCase):
    def test_returns_dash_i_pair_when_set(self):
        self.assertEqual(
            labctl._vm_ssh_identity_args({"ssh_key_path": "/k"}),
            ["-i", "/k"],
        )

    def test_returns_empty_list_when_unset(self):
        self.assertEqual(labctl._vm_ssh_identity_args({}), [])
        self.assertEqual(labctl._vm_ssh_identity_args({"ssh_key_path": None}), [])
        self.assertEqual(labctl._vm_ssh_identity_args({"ssh_key_path": ""}), [])

    def test_returns_empty_list_for_unfilled_placeholder(self):
        self.assertEqual(labctl._vm_ssh_identity_args({"ssh_key_path": "<KEY_PATH>"}), [])


class StageVerifyVmsTests(unittest.TestCase):
    def _ctx(self, config: Dict[str, Any]):
        class _Args:
            dry_run = False
            apply = False

        return labctl.StageContext(_Args(), config, "nvidia-bluefield", None, Path("/tmp/odse-verify-vms-test"), "run-1")

    def test_all_ready_false_when_no_vms_configured(self):
        result = labctl.stage_verify_vms(self._ctx({"vms": []}))
        self.assertEqual(result["stage"], "verify-vms")
        self.assertEqual(result["vms"], [])
        self.assertFalse(result["all_ready"])

    def test_all_ready_reflects_every_vm(self):
        # No virsh on this machine (CI/dev box) -> every entry degrades
        # gracefully to an explicit "virsh not found" error rather than
        # inventing a running/ssh_reachable verdict.
        if labctl.command_exists("virsh"):
            self.skipTest("virsh is installed on this machine; this checks the graceful-degradation path")
        result = labctl.stage_verify_vms(
            self._ctx({"vms": [{"name": "odse-vm2vm-vm1"}, {"name": "odse-vm2vm-vm2"}]})
        )
        self.assertFalse(result["all_ready"])
        for vm in result["vms"]:
            self.assertIsNone(vm["running"])
            self.assertEqual(vm["error"], "virsh not found on PATH")


if __name__ == "__main__":
    unittest.main()
