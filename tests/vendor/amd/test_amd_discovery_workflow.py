"""AMD discovery workflow stages: dry-run VF create, no assumed representors."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando.discovery_workflow import (
    discover_representors,
    dry_run_create_vfs,
)
from tests.vendor.harness import amd_samples


class AmdDiscoveryWorkflowTests(unittest.TestCase):
    def test_apply_does_not_create_vfs(self):
        result = dry_run_create_vfs({}, apply=True, samples=amd_samples())
        self.assertFalse(result.executed)
        self.assertFalse(result.details["created"])

    def test_dry_run_create_vfs_is_planned_only(self):
        result = dry_run_create_vfs({}, apply=False)
        self.assertFalse(result.executed)
        self.assertIn("planned", result.details)

    def test_representors_not_assumed(self):
        result = discover_representors({}, samples=amd_samples())
        self.assertEqual(result.details["representors"], [])
        self.assertFalse(result.details["assumed"])
        self.assertEqual(result.representors, [])


if __name__ == "__main__":
    unittest.main()
