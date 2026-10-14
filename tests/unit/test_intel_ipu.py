"""Intel IPU discovery and OPI client tests (fixtures, no hardware)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.intel_ipu.backend import IntelIpuBackend
from plugins.intel_ipu.discovery import (
    build_inventory,
    config_public_view,
    discover,
    load_config,
    looks_like_intel_ipu,
    parse_lspci_nn,
)
from plugins.intel_ipu.opi import MODE_HTTP, OpiClient

FIXTURES = ROOT / "tests" / "fixtures" / "intel_ipu"


class HintTests(unittest.TestCase):
    def test_ipu_hints(self):
        self.assertTrue(looks_like_intel_ipu("Infrastructure Processing Unit E2100"))
        self.assertTrue(looks_like_intel_ipu("Intel IPU"))
        self.assertFalse(looks_like_intel_ipu("Intel Corporation Ethernet Controller E810"))
        self.assertFalse(looks_like_intel_ipu("Intel Corporation Ethernet Connection"))
        self.assertFalse(looks_like_intel_ipu("Mellanox BlueField"))


class DiscoveryTests(unittest.TestCase):
    def test_lspci_keeps_ipu_drops_ice(self):
        rows = parse_lspci_nn((FIXTURES / "lspci.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pci_address"], "0000:17:00.0")

    def test_build_inventory_and_config(self):
        cfg = load_config(FIXTURES / "config.json")
        public = config_public_view(cfg)
        self.assertEqual(public.get("secret"), "***REDACTED***")
        self.assertNotIn("should-not-leak", json.dumps(public))
        result = build_inventory(
            lspci=(FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
            ip_link="2: eth0: <BROADCAST>\n",
            devlink_dev="pci/0000:17:00.0:\n",
            config=cfg,
        )
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].vendor, "intel")
        self.assertIsNone(result.devices[0].capabilities.switchdev)
        self.assertIsNone(result.devices[0].capabilities.ovs_hw_offload)
        self.assertTrue(result.details["redfish_configured"])
        self.assertFalse(result.details["management_cli_executed"])
        self.assertEqual(result.representors, [])

    def test_ice_only_skipped_without_config(self):
        result = build_inventory(lspci="18:00.0 Ethernet controller: Intel E810\n")
        self.assertEqual(result.devices, [])
        self.assertEqual(result.status, "skipped")

    def test_discover_samples(self):
        result = discover(
            samples={
                "lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
                "config": {"redfish_base": "https://imc/redfish/v1"},
            }
        )
        self.assertTrue(result.devices)

    def test_backend_skips_switchdev(self):
        backend = IntelIpuBackend(config={"redfish_base": "https://x/redfish/v1"})
        sw = backend.configure_switchdev("0000:17:00.0")
        self.assertEqual(sw.status, "skipped")
        ovs = backend.configure_ovs_bridge("br-int")
        self.assertEqual(ovs.status, "skipped")


class OpiTests(unittest.TestCase):
    def test_mock_list_get_nf_telemetry(self):
        client = OpiClient.from_config(load_config(FIXTURES / "config.json"))
        listed = client.list_devices()
        self.assertEqual(listed.devices[0].id, "intel-mock-1")
        got = client.get_device("intel-mock-1")
        self.assertEqual(got.status, "ok")
        missing = client.get_device("nope")
        self.assertEqual(missing.status, "skipped")
        dep = client.deploy_network_function({"name": "nf1", "image": "x"})
        self.assertEqual(dep.status, "ok")
        tel = client.get_telemetry("intel-mock-1")
        self.assertEqual(tel.telemetry, [])
        gone = client.delete_network_function("nf1")
        self.assertEqual(gone.status, "ok")

    def test_http_placeholder_sends_nothing(self):
        client = OpiClient(mode=MODE_HTTP, base_url="https://opi.example.test")
        result = client.list_devices()
        self.assertEqual(result.status, "not_implemented")
        self.assertFalse(result.executed)
        self.assertFalse(result.details["http_attempted"])


if __name__ == "__main__":
    unittest.main()
