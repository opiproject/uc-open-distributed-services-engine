"""NVIDIA BlueField telemetry placeholder. No frozen DOCA metric names."""

from vendor_plugins import VendorTelemetryPlugin


class NvidiaBlueFieldTelemetry(VendorTelemetryPlugin):
    name = "nvidia_bluefield"

    def collect(self, context):
        return {
            "implemented": False,
            "note": "TODO: DOCA/mst extras for <DOCA_VERSION>; core uses ethtool/OVS only",
        }
