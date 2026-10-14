#!/usr/bin/env python3
"""Cross-vendor readiness matrix / go-no-go report.

Usage:
    python tools/vendor_readiness_matrix.py --configs config/*.yaml --output tests/results/vendor-readiness-matrix.md
    python tools/vendor_readiness_matrix.py --configs config/*.yaml --json --output tests/results/vendor-readiness-matrix.json
"""

from __future__ import annotations

import argparse
import json
import sys
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando.capability_report import build_gap_report  # noqa: E402
from plugins.base.lab_config import normalize_vendor  # noqa: E402
from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402
from plugins.intel_ipu.readiness import evaluate_intel_readiness  # noqa: E402
from plugins.marvell_octeon.readiness import evaluate_marvell_readiness  # noqa: E402

GO_FOR_HARDWARE_DRY_RUN = "GO_FOR_HARDWARE_DRY_RUN"
GO_FOR_DISCOVERY_ONLY = "GO_FOR_DISCOVERY_ONLY"
BLOCKED_MISSING_CONTROL_PATH = "BLOCKED_MISSING_CONTROL_PATH"
BLOCKED_MISSING_REMOTE_EXECUTION = "BLOCKED_MISSING_REMOTE_EXECUTION"
BLOCKED_MISSING_VENDOR_RUNTIME = "BLOCKED_MISSING_VENDOR_RUNTIME"
NOT_READY = "NOT_READY"


def _row_nvidia(config: Dict[str, Any], path: str) -> Dict[str, Any]:
    nvidia = config.get("nvidia") or {}
    remote = config.get("remote_execution") or {}
    dpu = config.get("dpu") or {}
    profile = (config.get("vendor") or {}).get("profile") or "nvidia-bluefield-dpu-mode"
    blockers = []
    if nvidia.get("mode") != "dpu" or nvidia.get("ovs_location") != "dpu":
        blockers.append("NVIDIA DPU-mode placement incomplete")
    if not remote.get("enabled") or not dpu.get("management_ip") or not dpu.get("ssh_user"):
        blockers.append("DPU remote execution fields incomplete")
    status = GO_FOR_HARDWARE_DRY_RUN if not blockers else BLOCKED_MISSING_REMOTE_EXECUTION
    if blockers and nvidia.get("mode") != "dpu":
        status = NOT_READY
    return {
        "vendor": "nvidia-bluefield",
        "profile": profile,
        "intended_first_use_case": "VM-to-VM DPU-side OVS/OVS-DOCA",
        "control_model": "labctl -> nvidia backend -> DPU-side OVS/OVS-DOCA -> BlueField E-Switch",
        "ovs_location": nvidia.get("ovs_location") or "dpu",
        "representor_location": nvidia.get("representor_location") or "dpu",
        "operator_api_dependency": "none (DPU OVS)",
        "vm_to_vm_readiness": "dry-run" if status == GO_FOR_HARDWARE_DRY_RUN else "blocked",
        "vm_to_pod_readiness": "discovery-required",
        "offload_validation_readiness": "dpu-side planned",
        "cleanup_readiness": "dpu-side planned",
        "status": status,
        "blockers": blockers,
        "recommended_next_action": "Review first-run NVIDIA DPU-mode runbook; keep --apply gated.",
        "config": path,
    }


def _row_marvell(config: Dict[str, Any], path: str) -> Dict[str, Any]:
    ready = evaluate_marvell_readiness(config)
    st = (ready.details or {}).get("readiness_status")
    blockers = []
    status = GO_FOR_DISCOVERY_ONLY
    if st == "MARVELL_READY_FOR_DRY_RUN":
        status = GO_FOR_HARDWARE_DRY_RUN
    elif st in ("MARVELL_DAO_MISSING", "MARVELL_DPDK_MISSING"):
        status = BLOCKED_MISSING_VENDOR_RUNTIME
        blockers.append(st)
    elif st == "MARVELL_NOT_READY":
        status = NOT_READY
        blockers.append(st)
    else:
        blockers.append(st)
    marvell = config.get("marvell") or {}
    return {
        "vendor": "marvell-octeon",
        "profile": (config.get("vendor") or {}).get("profile") or "marvell-octeon-dao-ovs-offload",
        "intended_first_use_case": "DAO/DPDK OVS-offload discovery",
        "control_model": "DAO OVS-offload / DPDK representors",
        "ovs_location": marvell.get("ovs_location") or "dpu",
        "representor_location": "dpu (dpdk)",
        "operator_api_dependency": "DAO/DPDK runtime",
        "vm_to_vm_readiness": "discovery-required" if status != GO_FOR_HARDWARE_DRY_RUN else "dry-run if DAO proven",
        "vm_to_pod_readiness": "discovery-required",
        "offload_validation_readiness": "marvell_dao_evidence skeleton",
        "cleanup_readiness": "refuses untracked DPDK unbind",
        "status": status,
        "blockers": blockers,
        "recommended_next_action": "Fill DAO binary/hugepages from hardware discovery; do not copy NVIDIA OVS.",
        "config": path,
    }


def _row_intel(config: Dict[str, Any], path: str) -> Dict[str, Any]:
    ready = evaluate_intel_readiness(config)
    st = (ready.details or {}).get("readiness_status")
    blockers = []
    status = GO_FOR_DISCOVERY_ONLY
    if st == "INTEL_READY_FOR_OPI_DRY_RUN":
        status = GO_FOR_DISCOVERY_ONLY
    elif st == "INTEL_OPI_ENDPOINT_REQUIRED":
        status = GO_FOR_DISCOVERY_ONLY
        blockers.append(st)
    elif st == "INTEL_NOT_READY":
        status = NOT_READY
        blockers.append(st)
    else:
        blockers.append(st)
    intel = config.get("intel") or {}
    return {
        "vendor": "intel-ipu",
        "profile": (config.get("vendor") or {}).get("profile") or "intel-ipu-opi-dpu-operator",
        "intended_first_use_case": "network-function-offload (intel-nf), not VM-to-VM OVS",
        "control_model": "OPI / DPU Operator",
        "ovs_location": "not assumed",
        "representor_location": "discovery-required",
        "operator_api_dependency": "OPI endpoint; optional DPU Operator",
        "vm_to_vm_readiness": "discovery-required",
        "vm_to_pod_readiness": "discovery-required",
        "offload_validation_readiness": "not OVS-TC",
        "cleanup_readiness": "intel-nf skeleton",
        "status": status,
        "blockers": blockers,
        "recommended_next_action": "Use labctl intel-nf; do not assume NVIDIA representors. vm_to_vm=%s"
        % intel.get("vm_to_vm_forwarding_default", "discovery-required"),
        "config": path,
    }


def _row_amd(config: Dict[str, Any], path: str) -> Dict[str, Any]:
    report = build_gap_report(config)
    final = report.get("final_status")
    blockers = []
    if final in ("AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH", "AMD_DISCOVERY_ONLY", "AMD_READY_FOR_SRIOV_ONLY", "AMD_READY_FOR_RDMA_ONLY"):
        status = GO_FOR_DISCOVERY_ONLY
        if final == "AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH":
            status = BLOCKED_MISSING_CONTROL_PATH
        blockers.append(final)
    elif final == "AMD_READY_FOR_VM_TO_VM_DRY_RUN":
        status = GO_FOR_HARDWARE_DRY_RUN
    else:
        status = NOT_READY
        blockers.append(final)
    return {
        "vendor": "amd-pensando",
        "profile": (config.get("vendor") or {}).get("profile") or "amd-pensando-discovery-first",
        "intended_first_use_case": "ionic/SR-IOV/representor discovery",
        "control_model": "discovery-first; no assumed OVS",
        "ovs_location": "not assumed",
        "representor_location": "discovery-required",
        "operator_api_dependency": "none",
        "vm_to_vm_readiness": (report.get("matrix") or {}).get("vm_to_vm_forwarding_readiness"),
        "vm_to_pod_readiness": (report.get("matrix") or {}).get("vm_to_pod_readiness"),
        "offload_validation_readiness": "ionic stats are not offload proof",
        "cleanup_readiness": "no assumed teardown",
        "status": status,
        "blockers": blockers,
        "recommended_next_action": report.get("recommended_next_action"),
        "config": path,
    }


def evaluate_config(path: Path) -> Optional[Dict[str, Any]]:
    try:
        config = load_vm2vm_config(path)
    except (OSError, ValueError):
        return None
    vendor = normalize_vendor((config.get("vendor") or {}).get("type"))
    if vendor == "nvidia-bluefield":
        return _row_nvidia(config, str(path))
    if vendor == "marvell-octeon":
        return _row_marvell(config, str(path))
    if vendor == "intel-ipu":
        return _row_intel(config, str(path))
    if vendor == "amd-pensando":
        return _row_amd(config, str(path))
    return None


def build_matrix(paths: List[Path]) -> Dict[str, Any]:
    rows = []
    for path in paths:
        if not path.is_file():
            continue
        if "schema" in path.name:
            continue
        row = evaluate_config(path)
        if row:
            rows.append(row)
    return {
        "schema_version": "odse-vendor-readiness-matrix/v1alpha1",
        "note": "Unknown capabilities are discovery-required. Do not overclaim.",
        "rows": rows,
    }


def render_markdown(matrix: Dict[str, Any]) -> str:
    lines = [
        "# Cross-vendor readiness matrix",
        "",
        matrix.get("note") or "",
        "",
        "| vendor | profile | first use case | control model | OVS loc | representor loc | API | VM-to-VM | VM-to-Pod | offload | cleanup | status | blockers | next action |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in matrix.get("rows") or []:
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | `%s` | %s | %s |"
            % (
                row.get("vendor"),
                row.get("profile"),
                row.get("intended_first_use_case"),
                row.get("control_model"),
                row.get("ovs_location"),
                row.get("representor_location"),
                row.get("operator_api_dependency"),
                row.get("vm_to_vm_readiness"),
                row.get("vm_to_pod_readiness"),
                row.get("offload_validation_readiness"),
                row.get("cleanup_readiness"),
                row.get("status"),
                "; ".join(row.get("blockers") or []) or "none",
                row.get("recommended_next_action"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-vendor go/no-go readiness matrix")
    parser.add_argument("--configs", nargs="+", required=True, help="YAML paths or globs")
    parser.add_argument("--output", help="Write markdown or JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    paths: List[Path] = []
    for item in args.configs:
        matched = glob(item)
        if matched:
            paths.extend(Path(p) for p in matched)
        else:
            paths.append(Path(item))
    matrix = build_matrix(paths)
    if args.json:
        text = json.dumps(matrix, indent=2)
    else:
        text = render_markdown(matrix)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
