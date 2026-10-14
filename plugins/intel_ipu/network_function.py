"""Intel network-function offload first-run path (skeleton).

Not mixed with VM-to-VM OVS representor forwarding.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from ..base.models import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, OperationResult
from .opi import OpiClient
from .readiness import INTEL_DPU_OPERATOR_NOT_FOUND, INTEL_OPI_ENDPOINT_REQUIRED, evaluate_intel_readiness

LOG = logging.getLogger(__name__)
VENDOR = "intel"
OPI_ENDPOINT_REQUIRED = "OPI_ENDPOINT_REQUIRED"
DPU_OPERATOR_REQUIRED = "DPU_OPERATOR_REQUIRED"


def validate_network_function_config(config: Optional[Dict[str, Any]]) -> list:
    issues = []
    cfg = config or {}
    nf = cfg.get("network_function") if isinstance(cfg.get("network_function"), dict) else None
    if not nf:
        issues.append("network_function mapping is required for intel-nf")
        return issues
    if not nf.get("name"):
        issues.append("network_function.name is required")
    ntype = nf.get("type")
    if ntype not in ("proxy", "firewall", "packet-filter", "custom", None):
        issues.append("network_function.type must be proxy|firewall|packet-filter|custom")
    if nf.get("placement") not in ("ipu", None):
        issues.append("network_function.placement must be ipu for this first-run path")
    vendor = (cfg.get("vendor") or {}).get("type")
    if vendor and vendor != "intel-ipu":
        issues.append("intel-nf requires vendor.type=intel-ipu")
    return issues


def plan_deploy_network_function(
    config: Optional[Dict[str, Any]] = None,
    *,
    apply: bool = False,
    dry_run: bool = True,
    samples: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    cfg = config or {}
    issues = validate_network_function_config(cfg)
    if issues:
        return OperationResult(
            operation="deploy_network_function",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="; ".join(issues),
            executed=False,
            details={"errors": issues},
        )
    ready = evaluate_intel_readiness(cfg, samples=samples)
    status = (ready.details or {}).get("readiness_status")
    intel = cfg.get("intel") if isinstance(cfg.get("intel"), dict) else {}
    if intel.get("require_opi", True) and status == INTEL_OPI_ENDPOINT_REQUIRED:
        return OperationResult(
            operation="deploy_network_function",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message=OPI_ENDPOINT_REQUIRED,
            executed=False,
            details={"error": OPI_ENDPOINT_REQUIRED, "readiness": ready.details},
        )
    if intel.get("require_dpu_operator") and status == INTEL_DPU_OPERATOR_NOT_FOUND:
        return OperationResult(
            operation="deploy_network_function",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message=DPU_OPERATOR_REQUIRED,
            executed=False,
            details={"error": DPU_OPERATOR_REQUIRED, "readiness": ready.details},
        )
    nf = cfg.get("network_function") or {}
    client = OpiClient.from_config(
        {
            "opi_mode": (cfg.get("opi") or {}).get("auth_mode") or "mock",
            "opi_endpoint": (cfg.get("opi") or {}).get("endpoint"),
        }
    )
    if apply and not dry_run:
        return client.deploy_network_function(nf)
    LOG.info("intel-nf deploy dry-run name=%s", nf.get("name"))
    return OperationResult(
        operation="deploy_network_function",
        vendor=VENDOR,
        status=STATUS_OK,
        message="network-function deploy planned (dry-run); not mixed with VM-to-VM",
        executed=False,
        destructive=True,
        details={
            "network_function": nf,
            "dry_run": True,
            "apply": apply,
            "first_run_path": "intel-network-function-offload",
        },
    )
