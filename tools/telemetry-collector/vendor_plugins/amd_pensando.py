"""AMD Pensando telemetry placeholder. Do not emit fake ionic/DSC counters."""

from vendor_plugins import VendorTelemetryPlugin


class AmdPensandoTelemetry(VendorTelemetryPlugin):
    name = "amd_pensando"

    def collect(self, context):
        return {
            "implemented": False,
            "note": "TODO: PSM/DSC counters if management path exists (amd-pensando.md §13)",
        }
