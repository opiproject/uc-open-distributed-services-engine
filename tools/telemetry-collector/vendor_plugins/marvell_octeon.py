"""Marvell OCTEON telemetry placeholder. rte_flow/DAO stats not in core."""

from vendor_plugins import VendorTelemetryPlugin


class MarvellOcteonTelemetry(VendorTelemetryPlugin):
    name = "marvell_octeon"

    def collect(self, context):
        return {
            "implemented": False,
            "note": "TODO: DAO/DPDK rte_flow hit counters (marvell-octeon.md §11)",
        }
