"""Marvell telemetry stub. Prefer rte_flow/DAO stats later; omit if missing."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.models import STATUS_NOT_IMPLEMENTED, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "marvell"


def collect(source_id: Optional[str] = None) -> OperationResult:
    LOG.info("marvell_octeon.telemetry: no samples; source=%s", source_id)
    return OperationResult(
        operation="collect_telemetry",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: DAO/DPDK counters; do not emit synthetic zeros",
        executed=False,
        telemetry=[],
        details={"source_id": source_id},
    )
