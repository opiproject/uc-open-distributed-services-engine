"""Tests for NVIDIA eswitch helpers (mocked runner, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult
from plugins.nvidia_bluefield import switchdev as sw


class FakeRunner:
    def __init__(self, stdout_by_argv=None, returncode=0):
        self.calls: List[Tuple[List[str], bool]] = []
        self.stdout_by_argv = stdout_by_argv or {}
        self.returncode = returncode

    def __call__(self, argv, *, dry_run=True, timeout=None, reason=None, **_kwargs):
        argv_list = list(argv)
        self.calls.append((argv_list, dry_run))
        if dry_run:
            return CommandResult(argv=argv_list, dry_run=True, executed=False, reason=reason)
        key = tuple(argv_list)
        stdout = self.stdout_by_argv.get(key, self.stdout_by_argv.get("default", ""))
        return CommandResult(
            argv=argv_list,
            dry_run=False,
            executed=True,
            returncode=self.returncode,
            stdout=stdout,
            reason=reason,
        )


class FakeWriter:
    def __init__(self):
        self.calls = []

    def __call__(self, path, content, dry_run=True):
        self.calls.append((str(path), content, dry_run))
        return CommandResult(
            argv=["write_file", str(path)],
            dry_run=dry_run,
            executed=not dry_run,
            returncode=0 if not dry_run else None,
        )


class ParseTests(unittest.TestCase):
    def test_parse_eswitch_show(self):
        self.assertEqual(
            sw.parse_eswitch_show(
                "pci/0000:03:00.0: mode legacy inline-mode none encap enable\n"
            ),
            "legacy",
        )
        self.assertEqual(
            sw.parse_eswitch_show("pci/0000:03:00.0:\n  mode switchdev\n"),
            "switchdev",
        )
        self.assertIsNone(sw.parse_eswitch_show(""))
        self.assertIsNone(sw.parse_eswitch_show("pci/0000:03:00.0: inline-mode none"))

    def test_normalize_pci(self):
        self.assertEqual(sw.normalize_pci("pci/0000:03:00.0"), "0000:03:00.0")
        self.assertIsNone(sw.normalize_pci("eth0"))


class GetModeTests(unittest.TestCase):
    def test_get_eswitch_mode_devlink(self):
        runner = FakeRunner(
            {
                (
                    "devlink",
                    "dev",
                    "eswitch",
                    "show",
                    "pci/0000:03:00.0",
                ): "pci/0000:03:00.0: mode switchdev\n"
            }
        )
        result = sw.get_eswitch_mode(
            "0000:03:00.0",
            runner=runner,
            command_exists_fn=lambda n: n == "devlink",
        )
        self.assertEqual(result.details["mode"], "switchdev")
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.destructive)
        self.assertEqual(runner.calls[0][1], False)

    def test_get_skips_without_devlink_or_sysfs(self):
        result = sw.get_eswitch_mode(
            "0000:03:00.0",
            command_exists_fn=lambda n: False,
            allow_sysfs=False,
        )
        self.assertEqual(result.status, "skipped")
        self.assertIsNone(result.details["mode"])


class SetModeTests(unittest.TestCase):
    def test_default_dry_run_does_not_execute(self):
        runner = FakeRunner()
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            runner=runner,
            command_exists_fn=lambda n: n == "devlink",
            bound_vfs=[],
        )
        self.assertTrue(result.details["dry_run"])
        self.assertFalse(result.executed)
        self.assertEqual(runner.calls[0][1], True)
        self.assertIn("mode", runner.calls[0][0])
        self.assertIn("switchdev", runner.calls[0][0])

    def test_legacy_dry_run(self):
        runner = FakeRunner()
        result = sw.set_eswitch_mode_legacy(
            "0000:03:00.0",
            runner=runner,
            command_exists_fn=lambda n: True,
            bound_vfs=[],
        )
        self.assertIn("legacy", runner.calls[0][0])
        self.assertEqual(result.operation, "set_eswitch_mode_legacy")

    def test_refuse_bound_vfs_without_force(self):
        runner = FakeRunner()
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            dry_run=False,
            force=False,
            runner=runner,
            command_exists_fn=lambda n: True,
            bound_vfs=["virtfn0"],
        )
        self.assertEqual(result.status, "error")
        self.assertFalse(result.executed)
        self.assertEqual(runner.calls, [])
        self.assertIn("bound", result.message.lower())

    def test_refuse_unknown_bind_state_without_force(self):
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            force=False,
            command_exists_fn=lambda n: True,
            pci_sysfs_root="/nonexistent/odse-pci",
        )
        self.assertEqual(result.status, "error")
        self.assertIn("unknown", result.message.lower())

    def test_force_allows_bound_vfs_dry_run(self):
        runner = FakeRunner()
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            dry_run=True,
            force=True,
            runner=runner,
            command_exists_fn=lambda n: True,
            bound_vfs=["virtfn0"],
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)
        self.assertTrue(result.details["force"])

    def test_sysfs_fallback_disabled_by_default(self):
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            bound_vfs=[],
            command_exists_fn=lambda n: False,
            allow_sysfs=False,
        )
        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.details.get("sysfs_fallback"))

    def test_sysfs_fallback_when_explicit(self):
        writer = FakeWriter()
        result = sw.set_eswitch_mode_legacy(
            "0000:03:00.0",
            dry_run=True,
            bound_vfs=[],
            command_exists_fn=lambda n: False,
            allow_sysfs=True,
            netdev="p0",
            write_file_fn=writer,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.details["method"], "sysfs")
        self.assertTrue(writer.calls[0][2])
        self.assertIn("p0", writer.calls[0][0])
        self.assertIn("legacy", writer.calls[0][1])

    def test_apply_executes_devlink_when_unbound(self):
        runner = FakeRunner()
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            dry_run=False,
            force=False,
            runner=runner,
            command_exists_fn=lambda n: True,
            bound_vfs=[],
        )
        self.assertFalse(result.details["dry_run"])
        self.assertTrue(result.executed)
        self.assertEqual(runner.calls[0][1], False)


class BindTests(unittest.TestCase):
    def test_unbind_dry_run(self):
        writer = FakeWriter()
        result = sw.unbind_vf(
            "0000:03:00.2",
            dry_run=True,
            current_driver="mlx5_core",
            write_file_fn=writer,
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)
        self.assertTrue(writer.calls[0][2])
        self.assertIn("unbind", writer.calls[0][0])

    def test_unbind_unknown_driver(self):
        result = sw.unbind_vf("0000:03:00.2", dry_run=True, pci_sysfs_root="/nope")
        self.assertEqual(result.status, "error")
        self.assertIsNone(result.details["driver"])

    def test_bind_default_mlx5(self):
        writer = FakeWriter()
        result = sw.bind_vf("0000:03:00.2", dry_run=True, write_file_fn=writer)
        self.assertEqual(result.details["driver"], "mlx5_core")
        self.assertIn("bind", writer.calls[0][0])


class DiscoverRepTests(unittest.TestCase):
    def test_discover_representors_uses_samples(self):
        samples = {
            "ip_link": (
                "3: pf0vf0: <BROADCAST>\n"
                "    portname pf0vf0\n"
                "    phys_port_name pf0vf0\n"
            )
        }
        result = sw.discover_representors(samples=samples)
        self.assertEqual(result.operation, "discover_representors")
        self.assertTrue(any(r.netdev == "pf0vf0" for r in result.representors))


if __name__ == "__main__":
    unittest.main()
