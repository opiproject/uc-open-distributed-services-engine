"""labctl amd-discovery stages."""

from __future__ import annotations

from typing import Any, Dict

from plugins.amd_pensando.capability_report import render_gap_markdown
from plugins.amd_pensando.discovery_workflow import STAGE_FUNCS, dry_run_create_vfs, generate_gap_report
from plugins.base.vm2vm_config import validate_vm2vm_config

AMD_DISCOVERY_STAGES = (
    "validate-config",
    "discover-device",
    "discover-ionic",
    "discover-rdma",
    "discover-sriov",
    "dry-run-create-vfs",
    "discover-representors",
    "discover-switchdev",
    "discover-ovs-feasibility",
    "collect-evidence",
    "generate-gap-report",
)

STAGE_FILES = {
    "discover-device": "amd-device-discovery.json",
    "discover-ionic": "amd-ionic-discovery.json",
    "discover-rdma": "amd-rdma-discovery.json",
    "discover-sriov": "amd-sriov-discovery.json",
    "discover-representors": "amd-representor-discovery.json",
    "discover-switchdev": "amd-switchdev-discovery.json",
    "discover-ovs-feasibility": "amd-ovs-feasibility.json",
    "generate-gap-report": "amd-gap-report.md",
}


def run_stage(
    stage: str,
    config: Dict[str, Any],
    *,
    apply: bool = False,
    dry_run: bool = True,
    samples: Dict[str, Any] = None,
) -> Dict[str, Any]:
    samples = samples or {}
    if stage == "validate-config":
        issues = validate_vm2vm_config(config)
        return {"stage": stage, "valid": not issues, "issues": issues}
    if stage == "dry-run-create-vfs":
        result = dry_run_create_vfs(config, apply=apply and not dry_run, samples=samples)
        return {"stage": stage, "result": _asdict(result), "apply": apply, "dry_run": dry_run or not apply}
    func = STAGE_FUNCS.get(stage)
    if not func:
        return {"stage": stage, "error": "unknown amd-discovery stage"}
    result = func(config, samples=samples)
    payload = {"stage": stage, "result": _asdict(result)}
    if stage == "generate-gap-report":
        report = generate_gap_report(config, samples=samples).details
        payload["markdown"] = render_gap_markdown(report)
        payload["report"] = report
    return payload


def _asdict(obj: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        return asdict(obj)
    return obj
