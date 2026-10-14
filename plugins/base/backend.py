"""VendorBackend ABC — vendor-neutral DPU/IPU enablement contract.

Implementations live under plugins/<vendor>/. Methods return
OperationResult; they must not run destructive CLI in this skeleton.

This is the *enablement* plugin (SR-IOV, switchdev, OVS, vDPA). The
OPI adapter in kubernetes/opi/adapter is a separate lifecycle API.
TODO: optionally wrap this backend from the OPI adapter.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .models import (
    FORWARDING_ERROR_NOT_IMPLEMENTED,
    STATUS_NOT_IMPLEMENTED,
    ForwardingIntent,
    ForwardingResult,
    OperationResult,
    VendorCapability,
)

LOG = logging.getLogger(__name__)


class VendorBackend(ABC):
    """Common interface for NVIDIA, Intel, Marvell, and AMD plugins."""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """nvidia | intel | marvell | amd"""

    def discover(self) -> OperationResult:
        return self._stub("discover", destructive=False)

    def validate_prerequisites(self) -> OperationResult:
        return self._stub("validate_prerequisites", destructive=False)

    def configure_sriov(
        self,
        pf: Optional[str] = None,
        numvfs: Optional[int] = None,
        dry_run: bool = True,
    ) -> OperationResult:
        return self._stub("configure_sriov", destructive=True)

    def configure_switchdev(self, pf: Optional[str] = None) -> OperationResult:
        return self._stub("configure_switchdev", destructive=True)

    def discover_representors(self, pf: Optional[str] = None) -> OperationResult:
        return self._stub("discover_representors", destructive=False)

    def configure_ovs_bridge(self, bridge: Optional[str] = None) -> OperationResult:
        return self._stub("configure_ovs_bridge", destructive=True)

    def configure_vdpa(self, vf: Optional[str] = None) -> OperationResult:
        return self._stub("configure_vdpa", destructive=True)

    def collect_telemetry(self, source_id: Optional[str] = None) -> OperationResult:
        return self._stub("collect_telemetry", destructive=False)

    def validate_offload(self) -> OperationResult:
        return self._stub("validate_offload", destructive=False)

    def cleanup(self) -> OperationResult:
        return self._stub("cleanup", destructive=True)

    def configure_forwarding(self, intent: ForwardingIntent, dry_run: bool = True) -> ForwardingResult:
        """Translate a common ForwardingIntent into vendor-specific operations.

        tools/labctl.py is the single user-facing control point for the
        VM-to-VM lab use case: users should not call OVS, DAO, OPI, DOCA,
        DPU Operator, tc, devlink, or vendor SDKs directly to connect
        VM1 to VM2. labctl builds a ForwardingIntent from the lab config
        and calls this method; each vendor backend decides how (or
        whether) to realize that intent given what has actually been
        discovered on that vendor's hardware. The default implementation
        here is a safe stub that performs no discovery and no mutation.

        dry_run mirrors tools/labctl.py's StageContext.dry_run (True
        unless the caller passed --apply and the apply-readiness gate
        passed): False means the backend should actually execute its
        commands, not just plan them.
        """
        msg = (
            f"{self.backend_id}: configure_forwarding is a safe stub "
            "(not_implemented). No OVS/DAO/OPI/DOCA command was run."
        )
        LOG.warning(msg)
        return ForwardingResult(
            success=False,
            vendor=self.backend_id,
            message=msg,
            errors=[FORWARDING_ERROR_NOT_IMPLEMENTED],
        )

    def _stub(self, operation: str, *, destructive: bool) -> OperationResult:
        msg = (
            f"{self.backend_id}: {operation} is a safe stub (not_implemented). "
            "TODO: wire tools/inventory, Ansible roles, or vendor CLI. "
            "No destructive action was taken."
        )
        LOG.warning(msg)
        return OperationResult(
            operation=operation,
            vendor=self.backend_id,
            status=STATUS_NOT_IMPLEMENTED,
            message=msg,
            destructive=destructive,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id),
        )
