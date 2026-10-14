"""NVIDIA backend.configure_sriov() — sysfs sriov_numvfs wiring (no live hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.nvidia_bluefield import switchdev as sw
from plugins.nvidia_bluefield.backend import NvidiaBlueFieldBackend


class NvidiaConfigureSriovBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = NvidiaBlueFieldBackend()

    def test_operation_name_is_configure_sriov(self):
        result = self.backend.configure_sriov(pf="ens1f0np0", numvfs=2)
        self.assertEqual(result.operation, "configure_sriov")

    def test_defaults_to_dry_run_and_delegates_to_switchdev_helper(self):
        with mock.patch.object(
            sw, "configure_sriov_numvfs", wraps=sw.configure_sriov_numvfs
        ) as spy:
            self.backend.configure_sriov(pf="ens1f0np0", numvfs=2)
            spy.assert_called_once()
            _args, kwargs = spy.call_args
            self.assertTrue(kwargs.get("dry_run", True))
            self.assertFalse(kwargs.get("force", False))

    def test_apply_dry_run_false_is_forwarded(self):
        with mock.patch.object(
            sw, "configure_sriov_numvfs", wraps=sw.configure_sriov_numvfs
        ) as spy:
            self.backend.configure_sriov(pf="ens1f0np0", numvfs=2, dry_run=False)
            _args, kwargs = spy.call_args
            self.assertFalse(kwargs.get("dry_run"))

    def test_missing_pf_is_a_clean_error_not_a_stub(self):
        result = self.backend.configure_sriov(pf=None, numvfs=2)
        self.assertEqual(result.status, "error")
        self.assertNotIn("not_implemented", result.status)


if __name__ == "__main__":
    unittest.main()
