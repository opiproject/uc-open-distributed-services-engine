"""create-vms: `virsh define` failures must never fall through to `virsh
start`, and the specific "already exists with uuid" collision gets a
safe, evidence-based auto-heal (undefine+redefine) only when the stale
domain is confirmed NOT running.

Regression for example-output/virsh-error.txt: `virsh define` failed
with "already exists with uuid ...", but the old code proceeded to
`virsh start` anyway, so `virsh list` reported "running" against a
domain definition labctl never actually confirmed.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "odse_labctl_create_vms_recovery", ROOT / "tools" / "labctl.py"
)
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)


def _cmd(argv, returncode=0, stdout="", stderr="", executed=True) -> CommandResult:
    return CommandResult(
        argv=argv, executed=executed, dry_run=False, returncode=returncode, stdout=stdout, stderr=stderr
    )


_UUID_COLLISION_STDERR = (
    "error: operation failed: domain 'odse-vm2vm-vm1' already exists with "
    "uuid 0b4863f3-8514-41d5-8807-f65ffa9c3ff2"
)


class _ScriptedRunner:
    """Canned CommandResults keyed by (argv[0], argv[1]); each key's list is
    consumed in call order so a retried `virsh define` gets the *next*
    scripted result, not the first."""

    def __init__(self, script: Dict[Tuple[str, str], List[CommandResult]]):
        self._queues = {key: list(values) for key, values in script.items()}
        self.calls: List[List[str]] = []

    def __call__(self, argv, **kwargs) -> CommandResult:
        self.calls.append(list(argv))
        key = (argv[0], argv[1])
        queue = self._queues.get(key)
        if not queue:
            raise AssertionError("no scripted result left for %r (argv=%r)" % (key, argv))
        return queue.pop(0)

    @property
    def call_keys(self) -> List[Tuple[str, str]]:
        return [(c[0], c[1]) for c in self.calls]


class CreateVmsDefineFailureTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmpdir.name)
        self._orig_run = labctl.run
        self.addCleanup(self._restore)

    def _restore(self):
        labctl.run = self._orig_run
        self._tmpdir.cleanup()

    def _ctx(self, vms):
        class _Args:
            dry_run = False
            apply = True

        config = {"vms": vms, "safety": {"dry_run_default": False}}
        return labctl.StageContext(_Args(), config, "nvidia-bluefield", None, self.run_dir, "run-1")

    @staticmethod
    def _vm(name="odse-vm2vm-vm1"):
        return {
            "name": name,
            "image": "/var/lib/libvirt/images/%s.qcow2" % name,
            "memory_mb": 2048,
            "vcpus": 2,
        }

    def test_generic_define_failure_never_starts_the_domain(self):
        runner = _ScriptedRunner(
            {("virsh", "define"): [_cmd(["virsh", "define", "x"], returncode=1, stderr="error: some other libvirt error")]}
        )
        labctl.run = runner
        result = labctl.stage_create_vms(self._ctx([self._vm()]))
        entry = result["vms"][0]
        self.assertIn("error", entry)
        self.assertIsNone(entry["start"])
        self.assertNotIn(("virsh", "start"), runner.call_keys)

    def test_uuid_collision_with_running_stale_domain_is_left_alone(self):
        runner = _ScriptedRunner(
            {
                ("virsh", "define"): [_cmd(["virsh", "define", "x"], returncode=1, stderr=_UUID_COLLISION_STDERR)],
                ("virsh", "domstate"): [_cmd(["virsh", "domstate", "odse-vm2vm-vm1"], returncode=0, stdout="running\n")],
            }
        )
        labctl.run = runner
        result = labctl.stage_create_vms(self._ctx([self._vm()]))
        entry = result["vms"][0]
        self.assertIn("RUNNING", entry["error"])
        self.assertFalse(entry["define_recovery"]["auto_recovered"])
        self.assertIsNone(entry["start"])
        self.assertNotIn(("virsh", "undefine"), runner.call_keys)
        self.assertNotIn(("virsh", "start"), runner.call_keys)

    def test_uuid_collision_with_unknown_state_is_left_alone(self):
        runner = _ScriptedRunner(
            {
                ("virsh", "define"): [_cmd(["virsh", "define", "x"], returncode=1, stderr=_UUID_COLLISION_STDERR)],
                ("virsh", "domstate"): [
                    _cmd(["virsh", "domstate", "odse-vm2vm-vm1"], executed=False, stderr="failed to connect")
                ],
            }
        )
        labctl.run = runner
        result = labctl.stage_create_vms(self._ctx([self._vm()]))
        entry = result["vms"][0]
        self.assertIn("could not be confirmed", entry["error"])
        self.assertIsNone(entry["start"])
        self.assertNotIn(("virsh", "undefine"), runner.call_keys)

    def test_uuid_collision_with_shut_off_stale_domain_auto_heals_and_starts(self):
        runner = _ScriptedRunner(
            {
                ("virsh", "define"): [
                    _cmd(["virsh", "define", "x"], returncode=1, stderr=_UUID_COLLISION_STDERR),
                    _cmd(["virsh", "define", "x"], returncode=0),
                ],
                ("virsh", "domstate"): [_cmd(["virsh", "domstate", "odse-vm2vm-vm1"], returncode=0, stdout="shut off\n")],
                ("virsh", "undefine"): [
                    _cmd(["virsh", "undefine", "odse-vm2vm-vm1"], returncode=0, stdout="Domain 'odse-vm2vm-vm1' has been undefined\n")
                ],
                ("virsh", "start"): [
                    _cmd(["virsh", "start", "odse-vm2vm-vm1"], returncode=0, stdout="Domain 'odse-vm2vm-vm1' started\n")
                ],
            }
        )
        labctl.run = runner
        result = labctl.stage_create_vms(self._ctx([self._vm()]))
        entry = result["vms"][0]
        self.assertNotIn("error", entry)
        self.assertTrue(entry["define_recovery"]["auto_recovered"])
        self.assertIsNotNone(entry["start"])
        self.assertTrue(entry["start"]["executed"])
        self.assertEqual(
            runner.call_keys,
            [
                ("virsh", "define"),
                ("virsh", "domstate"),
                ("virsh", "undefine"),
                ("virsh", "define"),
                ("virsh", "start"),
            ],
        )

    def test_uuid_collision_undefine_failure_does_not_start(self):
        runner = _ScriptedRunner(
            {
                ("virsh", "define"): [_cmd(["virsh", "define", "x"], returncode=1, stderr=_UUID_COLLISION_STDERR)],
                ("virsh", "domstate"): [_cmd(["virsh", "domstate", "odse-vm2vm-vm1"], returncode=0, stdout="shut off\n")],
                ("virsh", "undefine"): [_cmd(["virsh", "undefine", "odse-vm2vm-vm1"], returncode=1, stderr="permission denied")],
            }
        )
        labctl.run = runner
        result = labctl.stage_create_vms(self._ctx([self._vm()]))
        entry = result["vms"][0]
        self.assertIn("undefine", entry["error"])
        self.assertIsNone(entry["start"])
        self.assertNotIn(("virsh", "start"), runner.call_keys)


if __name__ == "__main__":
    unittest.main()
