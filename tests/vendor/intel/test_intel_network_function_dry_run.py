"""Intel network-function deploy stays dry-run; missing OPI is explicit."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.intel_ipu.network_function import (
    OPI_ENDPOINT_REQUIRED,
    plan_deploy_network_function,
)


class IntelNetworkFunctionDryRunTests(unittest.TestCase):
    def test_missing_opi_returns_required(self):
        result = plan_deploy_network_function(
            {
                "vendor": {"type": "intel-ipu"},
                "intel": {"require_opi": True},
                "network_function": {"name": "nf1", "type": "proxy", "placement": "ipu"},
            }
        )
        self.assertEqual(result.message, OPI_ENDPOINT_REQUIRED)
        self.assertFalse(result.executed)

    def test_dry_run_does_not_execute(self):
        result = plan_deploy_network_function(
            {
                "vendor": {"type": "intel-ipu"},
                "intel": {"require_opi": True},
                "opi": {"endpoint": "https://opi.example.invalid"},
                "network_function": {"name": "nf1", "type": "proxy", "placement": "ipu"},
            },
            apply=False,
            dry_run=True,
        )
        self.assertFalse(result.executed)
        self.assertTrue(result.details["dry_run"])
        self.assertEqual(result.details["first_run_path"], "intel-network-function-offload")


if __name__ == "__main__":
    unittest.main()
