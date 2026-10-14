"""Unit tests for tools/capability_report.py (no hardware)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (  # noqa: E402
    DPUDevice,
    OperationResult,
    RepresentorPort,
    VendorCapability,
    VirtualFunction,
)

_SPEC = importlib.util.spec_from_file_location(
    "odse_capability_report",
    ROOT / "tools" / "capability_report.py",
)
cap = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = cap
_SPEC.loader.exec_module(cap)


class MappingTests(unittest.TestCase):
    def test_unknown_stays_null(self):
        result = OperationResult(
            operation="discover",
            vendor="amd",
            status="skipped",
            message="no hints",
            capabilities=VendorCapability(
                vendor="amd",
                switchdev=None,
                vdpa=None,
                ovs_hw_offload=False,
            ),
            details={
                "switchdev": None,
                "representors": None,
                "ovs_hw_offload": False,
                "vdpa": None,
                "rdma": {"detected": False},
            },
        )
        row = cap.row_from_discovery("amd-pensando", result)
        self.assertFalse(row.detected)
        self.assertEqual(row.confidence, "none")
        self.assertIsNone(row.switchdev_available)
        self.assertIsNone(row.vdpa_available)
        self.assertIsNone(row.ovs_hw_offload_available)
        self.assertIsNone(row.representors_available)
        self.assertFalse(row.rdma_available)

    def test_nvidia_representors_proven(self):
        result = OperationResult(
            operation="discover",
            vendor="nvidia",
            status="ok",
            devices=[
                DPUDevice(
                    id="nvidia-0000:03:00.0",
                    vendor="nvidia",
                    pci_address="0000:03:00.0",
                    capabilities=VendorCapability(vendor="nvidia", sriov=True),
                )
            ],
            virtual_functions=[VirtualFunction(pci_address="0000:03:00.2")],
            representors=[
                RepresentorPort(netdev="eth0", mapping_method="portname", confidence="high")
            ],
            details={"ovs_present": True, "mlx5_interfaces": ["eth0"]},
        )
        row = cap.row_from_discovery("nvidia-bluefield", result)
        self.assertTrue(row.detected)
        self.assertTrue(row.sriov_available)
        self.assertTrue(row.representors_available)
        self.assertTrue(row.ovs_available)
        self.assertIsNone(row.ovs_hw_offload_available)
        self.assertIsNone(row.switchdev_available)

    def test_intel_opi_config_is_low_confidence(self):
        result = OperationResult(
            operation="discover",
            vendor="intel",
            status="ok",
            devices=[],
            capabilities=VendorCapability(vendor="intel", opi_native=True),
            details={"redfish_configured": True, "opi_configured": True},
        )
        row = cap.row_from_discovery("intel-ipu", result)
        self.assertTrue(row.detected)
        self.assertEqual(row.confidence, "low")
        self.assertTrue(row.management_available)
        self.assertTrue(row.opi_available)
        self.assertIsNone(row.switchdev_available)


class RenderTests(unittest.TestCase):
    def test_json_and_markdown(self):
        row = cap.CapabilityRow(
            vendor="amd-pensando",
            detected=False,
            confidence="none",
            devices=[],
            management_available=None,
            sriov_available=None,
            switchdev_available=None,
            representors_available=None,
            ovs_available=None,
            ovs_hw_offload_available=None,
            vdpa_available=None,
            rdma_available=False,
            opi_available=None,
            telemetry_available=None,
            recommended_next_step="skip",
            discovery_status="skipped",
            discovery_message="no hints",
        )
        payload = json.loads(cap.render_json([row]))
        self.assertEqual(payload["cli"], "odse-capability-report/v1alpha1")
        self.assertIsNone(payload["vendors"][0]["switchdev_available"])
        md = cap.render_markdown([row])
        self.assertIn("amd-pensando", md)
        self.assertIn("null", md)


class CliTests(unittest.TestCase):
    def test_main_json_with_mocked_discover(self):
        fake = MagicMock()
        fake.discover.return_value = OperationResult(
            operation="discover",
            vendor="amd",
            status="skipped",
            message="no hints",
        )
        with patch.object(cap, "load_backend", return_value=fake):
            rc = cap.main(["--format", "json", "--vendor", "amd-pensando"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.discover.call_count, 1)
