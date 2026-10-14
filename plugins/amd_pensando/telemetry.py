"""AMD telemetry stub. ionic stats ≠ hardware offload proof. No fake zeros."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.models import STATUS_NOT_IMPLEMENTED, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "amd"


def collect(source_id: Optional[str] = None) -> OperationResult:
    LOG.info("amd_pensando.telemetry: omitting metrics; source=%s", source_id)
    return OperationResult(
        operation="collect_telemetry",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: ionic ethtool -S; omit missing; do not label as offload hits",
        executed=False,
        telemetry=[],
        details={"source_id": source_id},
    )
