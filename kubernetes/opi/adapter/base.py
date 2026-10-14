"""VendorBackend ABC — OPI-aligned adapter (non-functional skeleton).

Implementations live in vendor_*.py. Methods raise
UnsupportedOperation until a vendor track fills them in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from models import (
    DPUDevice,
    LifecycleOperation,
    NetworkAttachment,
    NetworkFunction,
    OffloadPolicy,
    TelemetrySample,
)


class BackendError(Exception):
    """Mapped vendor/tool failure. Do not leak raw SDK exceptions."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class NotFound(BackendError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class UnsupportedOperation(BackendError):
    """Capability not implemented or not present on this hardware."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class VendorBackend(ABC):
    """Common contract for AMD, NVIDIA, Intel, Marvell, and simulated."""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """amd | nvidia | intel | marvell | simulated"""

    def list_devices(self) -> List[DPUDevice]:
        # TODO: wrap tools/inventory/dpu_inventory.py or vendor inventory.
        raise UnsupportedOperation(f"{self.backend_id}: list_devices not implemented")

    def get_device(self, device_id: str) -> DPUDevice:
        raise UnsupportedOperation(f"{self.backend_id}: get_device not implemented")

    def apply_network_function(self, spec: NetworkFunction) -> NetworkFunction:
        raise UnsupportedOperation(
            f"{self.backend_id}: NetworkFunction apply not implemented"
        )

    def delete_network_function(self, function_id: str) -> None:
        raise UnsupportedOperation(
            f"{self.backend_id}: NetworkFunction delete not implemented"
        )

    def apply_attachment(self, spec: NetworkAttachment) -> NetworkAttachment:
        # TODO: ovs_setup / representor bind; never guess netdev names.
        raise UnsupportedOperation(
            f"{self.backend_id}: NetworkAttachment apply not implemented"
        )

    def delete_attachment(self, attachment_id: str) -> None:
        raise UnsupportedOperation(
            f"{self.backend_id}: NetworkAttachment delete not implemented"
        )

    def apply_offload_policy(self, spec: OffloadPolicy) -> OffloadPolicy:
        # TODO: if hw_offload and capability unknown, reject — do not guess.
        raise UnsupportedOperation(
            f"{self.backend_id}: OffloadPolicy apply not implemented"
        )

    def collect_telemetry(
        self, source_object_id: Optional[str] = None
    ) -> List[TelemetrySample]:
        # TODO: ethtool/devlink/OVS; omit missing metrics (no fake zeros).
        raise UnsupportedOperation(
            f"{self.backend_id}: collect_telemetry not implemented"
        )

    def submit_lifecycle(self, spec: LifecycleOperation) -> LifecycleOperation:
        # TODO: reject dryRun/rollbackOnFailure if the vendor cannot honor them.
        raise UnsupportedOperation(
            f"{self.backend_id}: LifecycleOperation submit not implemented"
        )

    def get_lifecycle(self, operation_id: str) -> LifecycleOperation:
        raise UnsupportedOperation(
            f"{self.backend_id}: get_lifecycle not implemented"
        )


def load_backend(backend_id: str) -> VendorBackend:
    """Factory. TODO: entry points / plugin dir; keep explicit for now."""
    mapping = {
        "amd": "vendor_amd.AmdPensandoBackend",
        "nvidia": "vendor_nvidia.NvidiaBlueFieldBackend",
        "intel": "vendor_intel.IntelIpuBackend",
        "marvell": "vendor_marvell.MarvellOcteonBackend",
    }
    if backend_id not in mapping:
        raise BackendError(f"unknown backend_id {backend_id!r}", retryable=False)
    module_name, class_name = mapping[backend_id].split(".")
    module = __import__(module_name, fromlist=[class_name])
    cls = getattr(module, class_name)
    return cls()
