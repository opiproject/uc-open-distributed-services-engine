"""NVIDIA BlueField VendorBackend stub.

TODO: wrap DOCA / mlx5 / BlueField BMC without baking a DOCA release into
this module. First track for ODSE; still not certified.
See docs/vendor-readiness/vendor-capability-matrix.md.
"""

from __future__ import annotations

from base import UnsupportedOperation, VendorBackend


class NvidiaBlueFieldBackend(VendorBackend):
    @property
    def backend_id(self) -> str:
        return "nvidia"

    # TODO: mst/mlxconfig/doca commands are version-specific — call out to
    # ansible/roles/vendor_nvidia_bluefield rather than hardcoding here.
    def list_devices(self):  # type: ignore[override]
        raise UnsupportedOperation(
            "nvidia: list_devices TODO — inventory via vendor role + dpu_inventory.py"
        )
