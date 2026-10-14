"""Intel DPU operator / OpenShift integration stub. No cluster mutations."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.models import STATUS_NOT_IMPLEMENTED, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "intel"


def plan_reconcile() -> OperationResult:
    LOG.warning("intel_ipu.dpu_operator: will not apply CRs or Helm")
    return OperationResult(
        operation="dpu_operator",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: kubernetes/dpu-operator + OpenShift DPU docs; comment-only until chart pin",
        destructive=True,
        executed=False,
    )


def probe_operator(config: Optional[dict] = None, *, samples: Optional[dict] = None) -> OperationResult:
    """Read-only: kubeconfig path presence only. No kubectl apply."""
    from pathlib import Path

    cfg = config or {}
    dpu_op = cfg.get("dpu_operator") if isinstance(cfg.get("dpu_operator"), dict) else {}
    kube = dpu_op.get("kubeconfig")
    samples = samples or {}
    if "dpu_operator_installed" in samples:
        found = bool(samples.get("dpu_operator_installed"))
    elif kube:
        found = Path(str(kube)).exists()
    else:
        found = False
    return OperationResult(
        operation="probe_dpu_operator",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED if not found else STATUS_NOT_IMPLEMENTED,
        message="DPU Operator %s (not installed/applied by this probe)" % ("found" if found else "not found"),
        executed=True,
        destructive=False,
        details={
            "kubeconfig": kube,
            "namespace": dpu_op.get("namespace"),
            "found": found,
            "applied": False,
        },
    )
