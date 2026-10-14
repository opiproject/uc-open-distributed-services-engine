"""Intel IPU VendorBackend stub.

TODO: Redfish + IPU OS / MicroShift lifecycle. Representor owner (IPU vs
host) is discovery-required.
"""

from __future__ import annotations

from base import UnsupportedOperation, VendorBackend


class IntelIpuBackend(VendorBackend):
    @property
    def backend_id(self) -> str:
        return "intel"

    def list_devices(self):  # type: ignore[override]
        raise UnsupportedOperation(
            "intel: list_devices TODO — Redfish/IPU manager; do not treat ice as IPU proof"
        )
