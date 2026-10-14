"""Marvell OCTEON VendorBackend stub.

TODO: SDK/DAO vs in-kernel representors. OVN-Kubernetes DPU mode is
rated future — do not assume a second k8s API on the DPU.
"""

from __future__ import annotations

from base import UnsupportedOperation, VendorBackend


class MarvellOcteonBackend(VendorBackend):
    @property
    def backend_id(self) -> str:
        return "marvell"

    def list_devices(self):  # type: ignore[override]
        raise UnsupportedOperation(
            "marvell: list_devices TODO — vendor SDK/CLI wrap; no assumed switchdev"
        )
