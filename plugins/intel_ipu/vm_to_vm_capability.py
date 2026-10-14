"""Intel VM-to-VM capability detection. Unproven paths stay discovery-required."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from ..base.models import (
    FORWARDING_ERROR_OPI_ENDPOINT_REQUIRED,
    STATUS_SKIPPED,
    OperationResult,
)
from .readiness import INTEL_OPI_ENDPOINT_REQUIRED, INTEL_VM_TO_VM_DISCOVERY_REQUIRED, evaluate_intel_readiness

LOG = logging.getLogger(__name__)
VENDOR = "intel"
INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED = (
    "INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED"
)
INTEL_VM_TO_VM_MESSAGE = (
    "Intel IPU VM-to-VM VF forwarding path was not discovered. "
    "Use Intel network-function-offload path first, or provide a supported "
    "OPI/IPU plugin configuration, a P4Runtime/IPDK pipeline configuration, "
    "or an OVS (P4Runtime-backed ovs-vswitchd) bridge configuration."
)


def _maybe_probe_ovs_live(cfg: Dict[str, Any]) -> Optional[bool]:
    """Best-effort live probe for `ovs_p4_control_plane`, run only when
    explicitly opted in via `intel.control_plane == "ovs"` *and*
    `remote_execution.dry_run_default == false` -- same dual-gate
    convention as `_maybe_probe_p4_live` below and NVIDIA's DPU-mode SSH
    probing (see docs/operations/dpu-remote-execution.md). Never runs
    implicitly, and never runs for any other `intel.control_plane` value.

    Returns None (not proven, not disproven) on anything short of a real
    executed, positive `ovs-vsctl` result over ACC SSH.
    """
    intel_cfg = cfg.get("intel") if isinstance(cfg.get("intel"), dict) else {}
    if str(intel_cfg.get("control_plane") or "").lower() != "ovs":
        return None
    remote = cfg.get("remote_execution") if isinstance(cfg.get("remote_execution"), dict) else {}
    if remote.get("dry_run_default", True):
        return None
    from .ovs_p4 import probe_control_plane_live

    LOG.info("intel vm-to-vm: intel.control_plane=ovs and dry_run_default=false, probing ACC OVS live")
    return probe_control_plane_live(cfg)


def _maybe_probe_p4_live(cfg: Dict[str, Any]) -> Optional[bool]:
    """Best-effort live probe for `p4_pipeline_configured`, run only when
    explicitly opted in via `p4.mode == "cli"` *and*
    `remote_execution.dry_run_default == false` -- the same dual-gate
    convention NVIDIA's DPU-mode SSH probing already uses (see
    docs/operations/dpu-remote-execution.md). Never runs for `p4.mode in
    ("mock", "grpc")`, and never runs implicitly.

    Returns None (not proven, not disproven) on anything short of a real
    executed, positive `p4rt-ctl get-pipe` result -- an unreachable ACC or
    missing p4rt-ctl means "discovery-required", not "confirmed absent".
    """
    p4_cfg = cfg.get("p4") if isinstance(cfg.get("p4"), dict) else {}
    if str(p4_cfg.get("mode") or "").lower() != "cli":
        return None
    remote = cfg.get("remote_execution") if isinstance(cfg.get("remote_execution"), dict) else {}
    if remote.get("dry_run_default", True):
        return None
    from .p4runtime import P4RuntimeClient

    LOG.info("intel vm-to-vm: p4.mode=cli and dry_run_default=false, probing ACC live")
    client = P4RuntimeClient.from_lab_config(cfg)
    result = client.get_forwarding_pipeline_config()
    if not result.executed:
        return None
    return bool((result.details or {}).get("pipeline_configured"))


def check_vm_to_vm_forwarding_capability(
    config: Optional[Dict[str, Any]] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    cfg = config or {}
    samples = dict(samples or {})
    ready = evaluate_intel_readiness(cfg, samples=samples)
    proven = bool(samples.get("vm_to_vm_forwarding_supported"))
    vfs_manageable = samples.get("vfs_manageable")
    vf_endpoint_map = samples.get("vf_endpoint_map")
    representor_like = samples.get("representor_like_model")
    opi_l2 = samples.get("opi_l2_forwarding_api")
    dpu_op_model = samples.get("dpu_operator_resource_model")
    p4_path = samples.get("p4_pipeline_configured")
    if p4_path is None:
        # Only fills in when the caller didn't already supply an explicit
        # sample (including an explicit False) -- see _maybe_probe_p4_live.
        p4_path = _maybe_probe_p4_live(cfg)
    ovs_path = samples.get("ovs_p4_control_plane")
    if ovs_path is None:
        # Same "explicit sample wins" rule -- see _maybe_probe_ovs_live.
        ovs_path = _maybe_probe_ovs_live(cfg)

    facts = {
        "vfs_manageable": vfs_manageable,
        "vf_endpoint_map": vf_endpoint_map,
        "representor_like_model": representor_like,
        "opi_or_plugin_l2_api": opi_l2,
        "dpu_operator_resource_model": dpu_op_model,
        "p4_or_pipeline_path": p4_path,
        "ovs_p4_control_plane": ovs_path,
        "proven": proven,
    }
    # opi_or_plugin_l2_api, p4_or_pipeline_path, and ovs_p4_control_plane
    # are three alternate, independent control planes over the same VFs --
    # proving any one of them (plus VF manageability/mapping) is
    # sufficient. Do not require more than one, and do not treat any of
    # them as a prerequisite for another. Note ovs_p4_control_plane is not
    # an independent *hardware* mechanism the way opi/p4 are relative to
    # each other -- it is an alternate P4Runtime *client* (ordinary
    # ovs-vsctl/ovs-ofctl instead of p4rt-ctl) onto the same infrap4d
    # backend p4_or_pipeline_path drives directly; see
    # plugins/intel_ipu/ovs_p4.py.
    control_plane_proven = bool(facts["opi_or_plugin_l2_api"]) or bool(facts["p4_or_pipeline_path"]) or bool(
        facts["ovs_p4_control_plane"]
    )
    if proven and facts["vfs_manageable"] and facts["vf_endpoint_map"] and control_plane_proven:
        if facts["opi_or_plugin_l2_api"]:
            via = "opi"
        elif facts["p4_or_pipeline_path"]:
            via = "p4"
        else:
            via = "ovs"
        LOG.info("intel vm-to-vm capability proven via samples (control_plane=%s)", via)
        return OperationResult(
            operation="check_vm_to_vm_forwarding_capability",
            vendor=VENDOR,
            status="ok",
            message="Intel VM-to-VM forwarding path discovered (sample-proven, control_plane=%s)" % via,
            executed=True,
            details={
                "capability_proven": True,
                "facts": facts,
                "status": "INTEL_VM_TO_VM_PROVEN",
                "control_plane": via,
            },
        )

    intel = cfg.get("intel") if isinstance(cfg.get("intel"), dict) else {}
    opi_required = intel.get("require_opi", True)
    opi_ep = (
        ((cfg.get("opi") or {}).get("endpoint") if isinstance(cfg.get("opi"), dict) else None)
        or ((cfg.get("dpu") or {}).get("opi_endpoint") if isinstance(cfg.get("dpu"), dict) else None)
        or cfg.get("opi_endpoint")
    )
    # p4_endpoint is an alternate to opi_endpoint, not an additional
    # requirement on top of it -- either one configured is enough to move
    # past "no control-plane endpoint at all", independent of whether an
    # OPI daemon or DPU Operator exists.
    p4_ep = (
        ((cfg.get("p4") or {}).get("endpoint") if isinstance(cfg.get("p4"), dict) else None)
        or ((cfg.get("dpu") or {}).get("p4_endpoint") if isinstance(cfg.get("dpu"), dict) else None)
        or cfg.get("p4_endpoint")
    )
    status = INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED
    errors = [status]
    if opi_required and not opi_ep and not p4_ep:
        errors.insert(0, FORWARDING_ERROR_OPI_ENDPOINT_REQUIRED)
        status_detail = INTEL_OPI_ENDPOINT_REQUIRED
    else:
        status_detail = INTEL_VM_TO_VM_DISCOVERY_REQUIRED

    LOG.info("intel vm-to-vm not proven (%s)", status_detail)
    return OperationResult(
        operation="check_vm_to_vm_forwarding_capability",
        vendor=VENDOR,
        status=STATUS_SKIPPED,
        message=INTEL_VM_TO_VM_MESSAGE,
        executed=True,
        details={
            "capability_proven": False,
            "facts": facts,
            "status": status,
            "readiness_status": status_detail,
            "errors": errors,
            "readiness": ready.details,
        },
    )
