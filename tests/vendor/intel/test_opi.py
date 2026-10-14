"""Intel OPI client from tests/fixtures/intel_ipu (no HTTP)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import OperationResult, VendorCapability
from plugins.intel_ipu.discovery import load_config
from plugins.intel_ipu.opi import MODE_HTTP, OpiClient
from tests.vendor.harness import FIXTURES, row_from_discovery


class IntelOpiFixtureTests(unittest.TestCase):
    def test_mock_from_config_fixture(self):
        client = OpiClient.from_config(load_config(FIXTURES / "intel_ipu" / "config.json"))
        listed = client.list_devices()
        self.assertEqual(listed.devices[0].id, "intel-mock-1")
        tel = client.get_telemetry("intel-mock-1")
        self.assertEqual(tel.telemetry, [])

    def test_http_placeholder_does_not_send(self):
        client = OpiClient(mode=MODE_HTTP, base_url="https://opi.example.test")
        result = client.list_devices()
        self.assertFalse(result.executed)
        self.assertFalse(result.details["http_attempted"])

    def test_capability_report_opi_flag(self):
        result = OperationResult(
            operation="discover",
            vendor="intel",
            status="ok",
            capabilities=VendorCapability(vendor="intel", opi_native=True),
            details={"opi_configured": True, "redfish_configured": True},
        )
        row = row_from_discovery("intel-ipu", result)
        self.assertTrue(row.opi_available)
        self.assertTrue(row.management_available)


if __name__ == "__main__":
    unittest.main()
