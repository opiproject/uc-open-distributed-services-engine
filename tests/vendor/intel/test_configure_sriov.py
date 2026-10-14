"""Intel IPU backend.configure_sriov() — sysfs sriov_numvfs wiring (no live hardware).

Mirrors tests/vendor/nvidia/test_configure_sriov.py: the sysfs
sriov_numvfs mechanism (plugins/base/sriov.py) is a generic kernel
SR-IOV control file, not vendor-specific, so once dpu.pf_name/pf_pci
are known (see tests/vendor/intel/test_discovery.py) prepare-vfs should
actually create VFs the same way NVIDIA's backend does, instead of
always returning STATUS_SKIPPED/executed=False.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base import sriov as _sriov
from plugins.base.models import CommandResult
from plugins.intel_ipu.backend import IntelIpuBackend


class IntelConfigureSriovBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = IntelIpuBackend()

    def test_operation_name_is_configure_sriov(self):
        result = self.backend.configure_sriov(pf="enp10s0f0", numvfs=2)
        self.assertEqual(result.operation, "configure_sriov")

    def test_missing_pf_is_a_clean_skip_not_a_permanent_stub(self):
        result = self.backend.configure_sriov(pf=None, numvfs=2)
        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.executed)
        self.assertNotIn("not_implemented", result.status)

    def test_defaults_to_dry_run_and_delegates_to_shared_sriov_helper(self):
        with mock.patch.object(
            _sriov, "configure_sriov_numvfs", wraps=_sriov.configure_sriov_numvfs
        ) as spy:
            self.backend.configure_sriov(pf="enp10s0f0", numvfs=2)
            spy.assert_called_once()
            _args, kwargs = spy.call_args
            self.assertTrue(kwargs.get("dry_run", True))
            self.assertFalse(kwargs.get("force", False))
            self.assertEqual(kwargs.get("vendor"), "intel")

    def test_apply_dry_run_false_is_forwarded(self):
        with mock.patch.object(
            _sriov, "configure_sriov_numvfs", wraps=_sriov.configure_sriov_numvfs
        ) as spy:
            self.backend.configure_sriov(pf="enp10s0f0", numvfs=2, dry_run=False)
            _args, kwargs = spy.call_args
            self.assertFalse(kwargs.get("dry_run"))

    def test_apply_actually_writes_sriov_numvfs_once_pf_is_known(self):
        """End-to-end: real host-4 pf_name ("enp10s0f0") + --apply writes
        sriov_numvfs, exactly like the NVIDIA path -- this is the fix for
        the "prepare-vfs always returns skipped" gap found from the
        example-output/host-4/R2-prepare-vfs-apply.txt run.
        """

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

        with mock.patch("plugins.base.sriov.read_file", side_effect=_reader), mock.patch(
            "plugins.base.sriov.write_file", side_effect=_writer
        ):
            result = self.backend.configure_sriov(pf="enp10s0f0", numvfs=2, dry_run=False)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)
        self.assertEqual(writes, [("/sys/class/net/enp10s0f0/device/sriov_numvfs", "2\n", False)])
        self.assertEqual(result.physical_functions[0].current_vfs, 2)
        self.assertEqual(result.vendor, "intel")


if __name__ == "__main__":
    unittest.main()
