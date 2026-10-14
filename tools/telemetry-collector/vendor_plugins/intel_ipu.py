"""Intel IPU telemetry placeholder. Redfish TelemetryService not assumed."""

from vendor_plugins import VendorTelemetryPlugin


class IntelIpuTelemetry(VendorTelemetryPlugin):
    name = "intel_ipu"

    def collect(self, context):
        return {
            "implemented": False,
            "note": "TODO: Redfish TelemetryService / IMC if reachable (intel-ipu-e2100.md §13)",
        }
