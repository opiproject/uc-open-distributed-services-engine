"""Intel IPU OPI / DPU Operator readiness (read-only).

Do not treat Intel as NVIDIA OVS representor forwarding unless discovery
proves that path. Missing OPI is INTEL_OPI_ENDPOINT_REQUIRED, not a fake
OVS plan.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
from . import discovery, dpu_operator, opi

LOG = logging.getLogger(__name__)
VENDOR = "intel"

INTEL_READY_FOR_OPI_DRY_RUN = "INTEL_READY_FOR_OPI_DRY_RUN"
INTEL_OPI_ENDPOINT_REQUIRED = "INTEL_OPI_ENDPOINT_REQUIRED"
INTEL_DPU_OPERATOR_NOT_FOUND = "INTEL_DPU_OPERATOR_NOT_FOUND"
INTEL_IPU_MANAGEMENT_UNREACHABLE = "INTEL_IPU_MANAGEMENT_UNREACHABLE"
INTEL_VM_TO_VM_DISCOVERY_REQUIRED = "INTEL_VM_TO_VM_DISCOVERY_REQUIRED"
INTEL_NOT_READY = "INTEL_NOT_READY"

INTEL_OPI_PROFILE = "intel-ipu-opi-dpu-operator"


def _placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">")


def _configured(value: Any) -> bool:
    return bool(value) and not _placeholder(value)


def evaluate_intel_readiness(
    config: Optional[Dict[str, Any]] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    cfg = config or {}
    samples = dict(samples or {})
    intel = cfg.get("intel") if isinstance(cfg.get("intel"), dict) else {}
    opi_cfg = cfg.get("opi") if isinstance(cfg.get("opi"), dict) else {}
    dpu_op = cfg.get("dpu_operator") if isinstance(cfg.get("dpu_operator"), dict) else {}
    ipu = cfg.get("ipu") if isinstance(cfg.get("ipu"), dict) else {}
    dpu = cfg.get("dpu") if isinstance(cfg.get("dpu"), dict) else {}

    lspci = samples.get("lspci")
    devices = discovery.parse_lspci_nn(lspci) if lspci is not None else None
    ipu_present = None if devices is None else bool(devices)

    opi_endpoint = (
        opi_cfg.get("endpoint")
        or dpu.get("opi_endpoint")
        or cfg.get("opi_endpoint")
    )
    opi_configured = _configured(opi_endpoint)
    opi_reachable = samples.get("opi_reachable")
    if opi_reachable is None and opi_configured:
        opi_reachable = None  # not probed live
    redfish = ipu.get("redfish_endpoint") or dpu.get("redfish_endpoint")
    kubeconfig = dpu_op.get("kubeconfig")
    operator_required = bool(intel.get("require_dpu_operator") or dpu_op.get("require_installed"))
    operator_found = samples.get("dpu_operator_installed")
    if operator_found is None and kubeconfig:
        operator_found = Path(str(kubeconfig)).exists() if not _placeholder(kubeconfig) else False

    plugin_available = samples.get("ipu_plugin_available")
    vm2vm_proven = bool(samples.get("vm_to_vm_forwarding_supported"))
    if intel.get("allow_ovs_assumption"):
        return OperationResult(
            operation="intel_opi_readiness",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="Intel must not assume OVS representor forwarding",
            executed=True,
            details={"readiness_status": INTEL_NOT_READY, "checks": {}, "warnings": ["allow_ovs_assumption is true"]},
        )

    checks = {
        "ipu_device": ipu_present,
        "opi_endpoint_configured": opi_configured,
        "opi_reachable": opi_reachable,
        "redfish_configured": _configured(redfish),
        "dpu_operator_kubeconfig": _configured(kubeconfig),
        "dpu_operator_found": operator_found,
        "ipu_plugin_available": plugin_available,
        "vm_to_vm_forwarding_proven": vm2vm_proven,
        "require_opi": intel.get("require_opi", True),
        "require_dpu_operator": operator_required,
        "require_redfish": bool(intel.get("require_redfish")),
    }

    status = INTEL_READY_FOR_OPI_DRY_RUN
    if ipu_present is False:
        status = INTEL_NOT_READY
    elif intel.get("require_opi", True) and not opi_configured:
        status = INTEL_OPI_ENDPOINT_REQUIRED
    elif opi_reachable is False:
        status = INTEL_IPU_MANAGEMENT_UNREACHABLE
    elif operator_required and not operator_found:
        status = INTEL_DPU_OPERATOR_NOT_FOUND
    elif intel.get("require_redfish") and not _configured(redfish):
        status = INTEL_IPU_MANAGEMENT_UNREACHABLE
    elif not vm2vm_proven:
        status = INTEL_VM_TO_VM_DISCOVERY_REQUIRED

    LOG.info("intel readiness %s", status)
    return OperationResult(
        operation="intel_opi_readiness",
        vendor=VENDOR,
        status=STATUS_OK if status in (INTEL_READY_FOR_OPI_DRY_RUN, INTEL_VM_TO_VM_DISCOVERY_REQUIRED) else STATUS_SKIPPED,
        message=status,
        executed=True,
        details={
            "readiness_status": status,
            "checks": checks,
            "warnings": []
            if vm2vm_proven
            else ["VM-to-VM VF forwarding is discovery-required; use intel-nf for first-run"],
            "opi_probe": opi.plan_map().message,
            "dpu_operator": dpu_operator.plan_reconcile().message,
            "profile": (cfg.get("vendor") or {}).get("profile") or INTEL_OPI_PROFILE,
        },
    )
