"""AMD Pensando forwarding capability matrix / gap report."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from . import ionic, rdma
from .readiness import evaluate_amd_readiness

VENDOR = "amd"

AMD_READY_FOR_VM_TO_VM_DRY_RUN = "AMD_READY_FOR_VM_TO_VM_DRY_RUN"
AMD_READY_FOR_SRIOV_ONLY = "AMD_READY_FOR_SRIOV_ONLY"
AMD_READY_FOR_RDMA_ONLY = "AMD_READY_FOR_RDMA_ONLY"
AMD_DISCOVERY_ONLY = "AMD_DISCOVERY_ONLY"
AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH = "AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH"
AMD_NOT_READY = "AMD_NOT_READY"

VM_TO_VM_NOT_PROVEN = (
    "VM-to-VM DPU forwarding is not yet proven for this AMD Pensando "
    "configuration. Do not run configure-forwarding until a representor, "
    "switchdev, OVS, or vendor-supported control path is discovered."
)


def build_gap_report(
    config: Optional[Dict[str, Any]] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
    prior: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or {}
    samples = dict(samples or {})
    prior = dict(prior or {})
    ready = evaluate_amd_readiness(cfg, samples=samples)
    checks = dict((ready.details or {}).get("checks") or {})

    device = checks.get("device_detected")
    ionic_ifaces = list(checks.get("ionic_interfaces") or [])
    ionic_detected = bool(ionic_ifaces or checks.get("ionic_module"))
    rdma_detected = bool((checks.get("ionic_rdma") or {}).get("detected"))
    sriov_total = checks.get("sriov_totalvfs")
    vf_tested = bool(prior.get("vf_creation_tested") or checks.get("vf_creation_tested"))
    representors = list(checks.get("representors") or prior.get("representors") or [])
    switchdev = checks.get("switchdev") if "switchdev" in checks else prior.get("switchdev")
    ovs_path = checks.get("ovs_compatible") if "ovs_compatible" in checks else prior.get("ovs_compatible")
    vdpa = samples.get("vdpa")
    roce = rdma_detected
    dsc_mgmt = checks.get("dsc_management_configured")

    control_path = bool(representors) or switchdev is True or ovs_path is True or bool(
        samples.get("vendor_control_path")
    )
    vm2vm = control_path and ionic_detected
    vm2pod = False

    if device is False:
        final = AMD_NOT_READY
    elif vm2vm:
        final = AMD_READY_FOR_VM_TO_VM_DRY_RUN
    elif control_path is False and ionic_detected:
        final = AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH
    elif sriov_total and not control_path:
        final = AMD_READY_FOR_SRIOV_ONLY
    elif rdma_detected and not control_path:
        final = AMD_READY_FOR_RDMA_ONLY
    else:
        final = AMD_DISCOVERY_ONLY

    recommended = VM_TO_VM_NOT_PROVEN
    if final == AMD_READY_FOR_VM_TO_VM_DRY_RUN:
        recommended = "Run labctl vm2vm discover/configure-forwarding in dry-run only after reviewing this gap report."
    elif final == AMD_READY_FOR_RDMA_ONLY:
        recommended = "Ethernet VM-to-VM is not proven; RDMA/RoCE discovery only."
    elif final == AMD_READY_FOR_SRIOV_ONLY:
        recommended = "SR-IOV totals visible; do not assume representors."

    return {
        "vendor": "amd-pensando",
        "profile": (cfg.get("vendor") or {}).get("profile") or "amd-pensando-discovery-first",
        "final_status": final,
        "matrix": {
            "device_detected": device,
            "ionic_detected": ionic_detected,
            "ionic_interfaces": ionic_ifaces,
            "ionic_rdma_detected": rdma_detected,
            "sriov_total_vfs": sriov_total,
            "vf_creation_tested": vf_tested,
            "representors_detected": bool(representors),
            "switchdev_detected": bool(switchdev) if switchdev is not None else False,
            "ovs_path_detected": bool(ovs_path) if ovs_path is not None else False,
            "vdpa_detected": bool(vdpa) if vdpa is not None else False,
            "rdma_roce_path_detected": bool(roce),
            "dsc_management_path_detected": bool(dsc_mgmt),
            "vm_to_vm_forwarding_readiness": "ready" if vm2vm else "discovery-required",
            "vm_to_pod_readiness": "discovery-required" if not vm2pod else "ready",
        },
        "recommended_next_action": recommended,
        "vm_to_vm_note": None if vm2vm else VM_TO_VM_NOT_PROVEN,
        "readiness": ready.details,
    }


def render_gap_markdown(report: Dict[str, Any]) -> str:
    matrix = report.get("matrix") or {}
    lines = [
        "# AMD Pensando forwarding gap report",
        "",
        "- vendor: `%s`" % report.get("vendor"),
        "- profile: `%s`" % report.get("profile"),
        "- final_status: `%s`" % report.get("final_status"),
        "",
        "| item | value |",
        "|---|---|",
    ]
    labels = [
        ("device_detected", "Device detected"),
        ("ionic_detected", "ionic detected"),
        ("ionic_interfaces", "ionic interfaces"),
        ("ionic_rdma_detected", "ionic_rdma detected"),
        ("sriov_total_vfs", "SR-IOV total VFs"),
        ("vf_creation_tested", "VF creation tested or not tested"),
        ("representors_detected", "representors detected or not detected"),
        ("switchdev_detected", "switchdev detected or not detected"),
        ("ovs_path_detected", "OVS path detected or not detected"),
        ("vdpa_detected", "vDPA detected or not detected"),
        ("rdma_roce_path_detected", "RDMA/RoCE path detected or not detected"),
        ("dsc_management_path_detected", "DSC management path detected or not detected"),
        ("vm_to_vm_forwarding_readiness", "VM-to-VM forwarding readiness"),
        ("vm_to_pod_readiness", "VM-to-Pod readiness"),
    ]
    for key, label in labels:
        lines.append("| %s | `%s` |" % (label, matrix.get(key)))
    lines.extend(
        [
            "",
            "## Recommended next action",
            "",
            report.get("recommended_next_action") or "",
            "",
        ]
    )
    if report.get("vm_to_vm_note"):
        lines.extend([report["vm_to_vm_note"], ""])
    return "\n".join(lines)
