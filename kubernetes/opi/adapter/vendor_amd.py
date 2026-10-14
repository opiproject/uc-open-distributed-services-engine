"""AMD Pensando VendorBackend stub.

TODO: confirm SR-IOV/representors exist before implementing attach/offload.
See docs/vendor-readiness/vendor-capability-matrix.md (mostly discovery-required).
"""

from __future__ import annotations

from base import UnsupportedOperation, VendorBackend


class AmdPensandoBackend(VendorBackend):
    @property
    def backend_id(self) -> str:
        return "amd"

    # TODO: ionic / DSC inventory via vendor CLI or Redfish — not assumed present.
    def list_devices(self):  # type: ignore[override]
        raise UnsupportedOperation(
            "amd: list_devices TODO — verify DSC enumeration (plan AMD-1..AMD-5)"
        )
