"""Intel OPI-style client skeleton.

Does not assume the vendor daemon speaks odse.opi/v1alpha1. Mock mode
returns configured stand-ins. HTTP mode is a placeholder and does not
send requests.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from ..base.models import (
    STATUS_ERROR,
    STATUS_NOT_IMPLEMENTED,
    STATUS_OK,
    STATUS_SKIPPED,
    DPUDevice,
    OperationResult,
    VendorCapability,
)
from .discovery import load_config

LOG = logging.getLogger(__name__)
VENDOR = "intel"
MODE_MOCK = "mock"
MODE_HTTP = "http"


class OpiClient:
    """Thin OPI adapter for Intel IPU. Mapping table is empty until filled."""

    def __init__(
        self,
        mode: str = MODE_MOCK,
        base_url: Optional[str] = None,
        mock_devices: Optional[List[Mapping[str, Any]]] = None,
        mapping_table: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.mode = (mode or MODE_MOCK).lower()
        if self.mode not in (MODE_MOCK, MODE_HTTP):
            self.mode = MODE_MOCK
        self.base_url = base_url
        self.mock_devices = list(mock_devices or [])
        self.mapping_table = dict(mapping_table or {})
        self._nfs: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def from_config(cls, config: Optional[Mapping[str, Any]] = None) -> "OpiClient":
        cfg = dict(config) if config is not None else load_config()
        mode = cfg.get("opi_mode") or MODE_MOCK
        return cls(
            mode=str(mode),
            base_url=cfg.get("opi_endpoint"),
            mock_devices=list(cfg.get("mock_devices") or []),
            mapping_table=cfg.get("mapping_table") or {},
        )

    def list_devices(self) -> OperationResult:
        if self.mode == MODE_HTTP:
            return self._http_placeholder("list_devices")
        devices = [_device_from_mock(row) for row in self.mock_devices]
        LOG.info("intel OPI mock list_devices count=%s", len(devices))
        return OperationResult(
            operation="opi_list_devices",
            vendor=VENDOR,
            status=STATUS_OK,
            message="mock OPI list_devices; not a vendor daemon",
            executed=True,
            devices=devices,
            details={"mode": MODE_MOCK, "mapping_table": dict(self.mapping_table)},
        )

    def get_device(self, device_id: str) -> OperationResult:
        if self.mode == MODE_HTTP:
            return self._http_placeholder("get_device")
        if not device_id:
            return OperationResult(
                operation="opi_get_device",
                vendor=VENDOR,
                status=STATUS_ERROR,
                message="device_id required",
                executed=False,
            )
        listed = self.list_devices()
        for dev in listed.devices:
            if dev.id == device_id:
                return OperationResult(
                    operation="opi_get_device",
                    vendor=VENDOR,
                    status=STATUS_OK,
                    message="mock get_device",
                    executed=True,
                    devices=[dev],
                    details={"mode": MODE_MOCK, "device_id": device_id},
                )
        return OperationResult(
            operation="opi_get_device",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="mock: device %r not in mock_devices" % device_id,
            executed=True,
            devices=[],
            details={"mode": MODE_MOCK, "device_id": device_id},
        )

    def deploy_network_function(self, spec: Mapping[str, Any]) -> OperationResult:
        if self.mode == MODE_HTTP:
            return self._http_placeholder("deploy_network_function")
        payload = _nf_payload(spec)
        name = payload.get("name") or payload.get("id")
        if not name:
            return OperationResult(
                operation="opi_deploy_network_function",
                vendor=VENDOR,
                status=STATUS_ERROR,
                message="NF spec needs name or id",
                executed=False,
            )
        self._nfs[str(name)] = payload
        LOG.info("intel OPI mock deploy NF %s (not scheduled on IPU)", name)
        return OperationResult(
            operation="opi_deploy_network_function",
            vendor=VENDOR,
            status=STATUS_OK,
            message="mock deploy %s; no IPU runtime invoked" % name,
            executed=True,
            destructive=False,
            details={"mode": MODE_MOCK, "name": name, "spec": payload},
        )

    def delete_network_function(self, name: str) -> OperationResult:
        if self.mode == MODE_HTTP:
            return self._http_placeholder("delete_network_function")
        existed = name in self._nfs
        self._nfs.pop(name, None)
        return OperationResult(
            operation="opi_delete_network_function",
            vendor=VENDOR,
            status=STATUS_OK if existed else STATUS_SKIPPED,
            message=("mock deleted %s" % name) if existed else "mock: NF %r not present" % name,
            executed=True,
            destructive=True,
            details={"mode": MODE_MOCK, "name": name, "existed": existed},
        )

    def get_telemetry(self, device_id: str) -> OperationResult:
        if self.mode == MODE_HTTP:
            return self._http_placeholder("get_telemetry")
        LOG.info("intel OPI mock telemetry: omit missing metrics for %s", device_id)
        return OperationResult(
            operation="opi_get_telemetry",
            vendor=VENDOR,
            status=STATUS_OK,
            message="mock telemetry empty (no fake zeros)",
            executed=True,
            telemetry=[],
            details={"mode": MODE_MOCK, "device_id": device_id},
        )

    def _http_placeholder(self, operation: str) -> OperationResult:
        LOG.warning("intel OPI HTTP mode placeholder: no request to %s", self.base_url)
        return OperationResult(
            operation="opi_%s" % operation,
            vendor=VENDOR,
            status=STATUS_NOT_IMPLEMENTED,
            message=(
                "HTTP OPI client is a placeholder; no request sent to %s. "
                "Fill mapping_table to odse.opi/v1alpha1 before enabling."
                % (self.base_url or "<opi_endpoint>")
            ),
            executed=False,
            details={
                "mode": MODE_HTTP,
                "base_url": self.base_url,
                "mapping_table": dict(self.mapping_table) or None,
                "http_attempted": False,
            },
        )


def plan_map() -> OperationResult:
    LOG.info("intel_ipu.opi: mapping table empty")
    return OperationResult(
        operation="opi_map",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: map vendor OPI proto → odse.opi/v1alpha1",
        executed=False,
        capabilities=VendorCapability(vendor=VENDOR, opi_native=None),
        details={"mapping_table": None},
    )


def _device_from_mock(row: Mapping[str, Any]) -> DPUDevice:
    return DPUDevice(
        id=row.get("id"),
        vendor=VENDOR,
        model=row.get("model"),
        serial_number=row.get("serial_number"),
        firmware_version=row.get("firmware_version"),
        pci_address=row.get("pci_address"),
        management_address=row.get("management_address"),
        capabilities=VendorCapability(vendor=VENDOR, opi_native=None),
        vendor_extensions={"mock": True},
    )


def _nf_payload(spec: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(spec)
