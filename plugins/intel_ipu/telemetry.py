"""Intel telemetry stub. Omit missing IMC/OPI metrics; no fake zeros."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.models import STATUS_NOT_IMPLEMENTED, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "intel"


def collect(source_id: Optional[str] = None) -> OperationResult:
    LOG.info("intel_ipu.telemetry: no samples (stub); source=%s", source_id)
    return OperationResult(
        operation="collect_telemetry",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: Redfish/OPI telemetry when a daemon exists; empty list not zeros",
        executed=False,
        telemetry=[],
        details={"source_id": source_id},
    )
