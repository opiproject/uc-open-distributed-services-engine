"""NVIDIA switchdev helpers from fixtures / dry-run (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult
from plugins.nvidia_bluefield import switchdev as sw
from plugins.nvidia_bluefield.discovery import discover
from tests.vendor.harness import nvidia_samples, row_from_discovery


class NvidiaSwitchdevFixtureTests(unittest.TestCase):
    def test_parse_eswitch_show_only(self):
        self.assertEqual(
            sw.parse_eswitch_show("pci/0000:03:00.0: mode legacy inline-mode none\n"),
            "legacy",
        )
        self.assertIsNone(sw.parse_eswitch_show(""))

    def test_set_switchdev_stays_dry_run(self):
        result = sw.set_eswitch_mode_switchdev(
            "0000:03:00.0",
            dry_run=True,
            force=False,
            bound_vfs=[],
            command_exists_fn=lambda _n: True,
            runner=lambda argv, **_kw: CommandResult(
                argv=list(argv), dry_run=True, executed=False
            ),
        )
        self.assertFalse(result.executed)
        self.assertTrue(result.destructive)

    def test_configure_sriov_requires_pf(self):
        result = sw.configure_sriov_numvfs(None, 2, dry_run=True)
        self.assertEqual(result.status, "error")
        self.assertFalse(result.executed)

    def test_configure_sriov_rejects_bad_numvfs(self):
        result = sw.configure_sriov_numvfs("ens1f0np0", -1, dry_run=True)
        self.assertEqual(result.status, "error")
        result = sw.configure_sriov_numvfs("ens1f0np0", "2", dry_run=True)
        self.assertEqual(result.status, "error")

    def test_configure_sriov_refuses_when_current_unreadable_without_force(self):
        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 2, dry_run=True, read_file_fn=lambda _p: None
        )
        self.assertEqual(result.status, "error")
        self.assertFalse(result.executed)
        self.assertIn("unknown", result.message)
        self.assertEqual(result.commands, [])

    def test_configure_sriov_refuses_above_totalvfs(self):
        files = {"numvfs": "0", "totalvfs": "8"}

        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return files["numvfs"]
            if path.endswith("sriov_totalvfs"):
                return files["totalvfs"]
            return None

        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 16, dry_run=True, read_file_fn=_reader
        )
        self.assertEqual(result.status, "error")
        self.assertIn("sriov_totalvfs=8", result.message)
        self.assertEqual(result.commands, [])

    def test_configure_sriov_already_satisfied_is_noop(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "2"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 2, dry_run=True, read_file_fn=_reader
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)
        self.assertEqual(result.commands, [])
        self.assertEqual(result.physical_functions[0].current_vfs, 2)

    def test_configure_sriov_plans_single_write_from_zero(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "0"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        writes = []

        def _writer(path, content, dry_run=True):
            writes.append((path, content, dry_run))
            return CommandResult(argv=["write_file", path], dry_run=dry_run, executed=not dry_run)

        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 2, dry_run=True, read_file_fn=_reader, write_file_fn=_writer
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1], "2\n")
        self.assertTrue(writes[0][2])

    def test_configure_sriov_resets_before_nonzero_to_nonzero_change(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "4"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        writes = []

        def _writer(path, content, dry_run=True):
            writes.append(content)
            return CommandResult(argv=["write_file", path], dry_run=dry_run, executed=not dry_run)

        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 2, dry_run=True, read_file_fn=_reader, write_file_fn=_writer
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(writes, ["0\n", "2\n"])

    def test_configure_sriov_apply_executes_writes(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "0"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        writes = []

        def _writer(path, content, dry_run=True):
            writes.append((content, dry_run))
            return CommandResult(
                argv=["write_file", path], dry_run=dry_run, executed=True, returncode=0
            )

        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 2, dry_run=False, read_file_fn=_reader, write_file_fn=_writer
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)
        self.assertEqual(writes, [("2\n", False)])
        self.assertEqual(result.physical_functions[0].current_vfs, 2)

    def test_configure_sriov_apply_stops_on_write_failure(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "4"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        def _writer(path, content, dry_run=True):
            return CommandResult(
                argv=["write_file", path],
                dry_run=dry_run,
                executed=True,
                returncode=1,
                stderr="device or resource busy",
            )

        result = sw.configure_sriov_numvfs(
            "ens1f0np0", 2, dry_run=False, read_file_fn=_reader, write_file_fn=_writer
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(len(result.commands), 1)
        self.assertEqual(result.details["failed_at"], "reset_to_zero")

    def test_representors_from_discovery_fixtures(self):
        result = sw.discover_representors(samples=nvidia_samples())
        names = [r.netdev for r in result.representors]
        self.assertIn("pf0vf0", names)
        self.assertNotIn("eth0v1", names)
        inv = discover(samples=nvidia_samples())
        row = row_from_discovery("nvidia-bluefield", inv)
        self.assertTrue(row.representors_available)
        self.assertIsNone(row.switchdev_available)


if __name__ == "__main__":
    unittest.main()
