"""NVIDIA telemetry stub. No synthetic zeros (OPI-6)."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.models import STATUS_NOT_IMPLEMENTED, OperationResult, VendorCapability

LOG = logging.getLogger(__name__)
VENDOR = "nvidia"


def collect(source_id: Optional[str] = None) -> OperationResult:
    LOG.info(
        "nvidia_bluefield.telemetry: omitting metrics (none collected); source=%s",
        source_id,
    )
    return OperationResult(
        operation="collect_telemetry",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message=(
            "TODO: ethtool/OVS via tools/telemetry-collector; DOCA extras in "
            "vendor plugin only. Empty telemetry list — not fake zeros."
        ),
        executed=False,
        telemetry=[],
        capabilities=VendorCapability(vendor=VENDOR),
        details={"source_id": source_id},
    )
