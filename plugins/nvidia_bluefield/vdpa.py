"""NVIDIA vDPA stub. No vdpa device create / driver bind."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.commands import planned_command
from ..base.models import STATUS_NOT_IMPLEMENTED, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "nvidia"


def plan_configure(vf: Optional[str] = None) -> OperationResult:
    LOG.warning("nvidia_bluefield.vdpa: will not bind mlx5_vdpa (destructive stub)")
    return OperationResult(
        operation="configure_vdpa",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: qemu/vdpa path; mlx5_vdpa likely but unproven here. vf=%s" % (vf or "<VF>"),
        destructive=True,
        executed=False,
        commands=[
            planned_command(
                ["vdpa", "dev", "add", "name", "<VDPA_NAME>", "mgmtdev", "<MGTDEV>"],
                "destructive — not executed",
            )
        ],
    )
