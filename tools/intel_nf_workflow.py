"""labctl intel-nf stages — Intel network-function-offload first-run path."""

from __future__ import annotations

from typing import Any, Dict, Optional

from plugins.intel_ipu.dpu_operator import probe_operator
from plugins.intel_ipu.network_function import (
    DPU_OPERATOR_REQUIRED,
    OPI_ENDPOINT_REQUIRED,
    plan_deploy_network_function,
    validate_network_function_config,
)
from plugins.intel_ipu.opi import OpiClient
from plugins.intel_ipu.readiness import evaluate_intel_readiness

INTEL_NF_STAGES = (
    "validate-config",
    "discover",
    "validate-opi",
    "validate-dpu-operator",
    "deploy-network-function-dry-run",
    "deploy-network-function",
    "run-traffic",
    "collect-evidence",
    "cleanup",
)


def run_stage(stage: str, config: Dict[str, Any], *, apply: bool = False, dry_run: bool = True) -> Dict[str, Any]:
    if stage == "validate-config":
        issues = validate_network_function_config(config)
        return {"stage": stage, "valid": not issues, "issues": issues}
    if stage == "discover":
        ready = evaluate_intel_readiness(config)
        return {"stage": stage, "backend_result": _asdict(ready), "first_run_path": "intel-network-function-offload"}
    if stage == "validate-opi":
        ready = evaluate_intel_readiness(config)
        status = (ready.details or {}).get("readiness_status")
        ok = status != "INTEL_OPI_ENDPOINT_REQUIRED"
        return {
            "stage": stage,
            "ok": ok,
            "error": None if ok else OPI_ENDPOINT_REQUIRED,
            "readiness": ready.details,
        }
    if stage == "validate-dpu-operator":
        probe = probe_operator(config)
        ready = evaluate_intel_readiness(config)
        status = (ready.details or {}).get("readiness_status")
        required = bool((config.get("intel") or {}).get("require_dpu_operator"))
        ok = (not required) or status != "INTEL_DPU_OPERATOR_NOT_FOUND"
        return {
            "stage": stage,
            "ok": ok,
            "error": None if ok else DPU_OPERATOR_REQUIRED,
            "probe": _asdict(probe),
        }
    if stage == "deploy-network-function-dry-run":
        result = plan_deploy_network_function(config, apply=False, dry_run=True)
        return {"stage": stage, "result": _asdict(result), "dry_run": True}
    if stage == "deploy-network-function":
        result = plan_deploy_network_function(config, apply=apply, dry_run=dry_run or not apply)
        return {"stage": stage, "result": _asdict(result), "dry_run": dry_run or not apply, "apply": apply}
    if stage == "run-traffic":
        return {
            "stage": stage,
            "skipped": True,
            "message": "intel-nf traffic is a placeholder; no VM-to-VM OVS path",
        }
    if stage == "collect-evidence":
        client = OpiClient.from_config(
            {
                "opi_endpoint": (config.get("opi") or {}).get("endpoint"),
                "opi_mode": "mock",
            }
        )
        tel = client.get_telemetry("intel-nf")
        return {"stage": stage, "telemetry": _asdict(tel)}
    if stage == "cleanup":
        return {
            "stage": stage,
            "result": _asdict(
                plan_deploy_network_function(config, apply=False, dry_run=True)
            ),
            "message": "intel-nf cleanup planned; no cluster delete",
            "dry_run": True,
        }
    return {"stage": stage, "error": "unknown intel-nf stage"}


def _asdict(obj: Any) -> Any:
    if hasattr(obj, "__dict__") and not isinstance(obj, dict):
        from dataclasses import asdict, is_dataclass

        if is_dataclass(obj):
            return asdict(obj)
    return obj
