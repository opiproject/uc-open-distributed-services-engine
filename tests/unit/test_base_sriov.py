"""plugins/base/sriov.py — vendor-neutral sriov_numvfs sysfs helper.

The read/refuse/write decision tree used to live only in
plugins/nvidia_bluefield/switchdev.py; these are new direct tests for
the extracted shared implementation (nvidia and intel now both
delegate to it -- see tests/vendor/nvidia/test_switchdev.py and
tests/vendor/intel/test_configure_sriov.py for the per-vendor wiring).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base import sriov
from plugins.base.models import CommandResult


class BaseSriovTests(unittest.TestCase):
    def test_requires_pf(self):
        result = sriov.configure_sriov_numvfs(None, 2, dry_run=True)
        self.assertEqual(result.status, "error")
        self.assertFalse(result.executed)

    def test_rejects_bad_numvfs(self):
        result = sriov.configure_sriov_numvfs("pf0", -1, dry_run=True)
        self.assertEqual(result.status, "error")

    def test_refuses_when_current_unreadable_without_force(self):
        result = sriov.configure_sriov_numvfs(
            "pf0", 2, dry_run=True, read_file_fn=lambda _p: None
        )
        self.assertEqual(result.status, "error")
        self.assertIn("unknown", result.message)

    def test_already_satisfied_is_noop(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "2"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        result = sriov.configure_sriov_numvfs("pf0", 2, dry_run=True, read_file_fn=_reader)
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)

    def test_apply_executes_write_and_tags_requested_vendor(self):
        def _reader(path):
            if path.endswith("sriov_numvfs"):
                return "0"
            if path.endswith("sriov_totalvfs"):
                return "8"
            return None

        writes = []

        def _writer(path, content, dry_run=True):
            writes.append((path, content, dry_run))
            return CommandResult(
                argv=["write_file", path], dry_run=dry_run, executed=True, returncode=0
            )

        result = sriov.configure_sriov_numvfs(
            "enp10s0f0",
            2,
            dry_run=False,
            vendor="intel",
            read_file_fn=_reader,
            write_file_fn=_writer,
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)
        self.assertEqual(result.vendor, "intel")
        self.assertEqual(writes, [("/sys/class/net/enp10s0f0/device/sriov_numvfs", "2\n", False)])


if __name__ == "__main__":
    unittest.main()
