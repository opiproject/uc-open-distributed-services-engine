#!/usr/bin/env python3
"""Final readiness gate before labctl destructive --apply.

Destructive stages (prepare-vfs, prepare-ovs, create-vms, attach-vfs,
configure-forwarding, cleanup, …) must not mutate the host or DPU until
these checks pass. Failure is APPLY_BLOCKED_BY_READINESS_GATE.

--force may skip only the stage-specific dry-run-plan check, and only
for cleanup. It is refused for forwarding / OVS setup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import lab_state
from plugins.base.remote import is_dpu_mode, ssh_params
from plugins.base.vm2vm_config import (
    NVIDIA_DPU_PROFILE,
    effective_nvidia_profile,
    normalize_vendor,
    validate_nvidia_dpu_mode,
    validate_vm2vm_config,
)

APPLY_BLOCKED_BY_READINESS_GATE = "APPLY_BLOCKED_BY_READINESS_GATE"

DESTRUCTIVE_APPLY_STAGES = frozenset(
    {
        "prepare-host",
        "prepare-vfs",
        "prepare-switchdev",
        "prepare-ovs",
        "create-vms",
        "attach-vfs",
        "configure-forwarding",
        "cleanup",
    }
)

NETWORK_SETUP_STAGES = frozenset(
    {
        "prepare-vfs",
        "prepare-switchdev",
        "prepare-ovs",
        "attach-vfs",
        "configure-forwarding",
    }
)

FORCE_ALLOWED_STAGES = frozenset({"cleanup"})


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _nvidia_dpu_profile(config: Dict[str, Any]) -> bool:
    vendor = config.get("vendor") or {}
    if normalize_vendor(vendor.get("type")) != "nvidia-bluefield":
        return False
    profile = effective_nvidia_profile(config)
    nvidia = config.get("nvidia") if isinstance(config.get("nvidia"), dict) else {}
    return profile == NVIDIA_DPU_PROFILE or nvidia.get("mode") == "dpu"


def evaluate_apply_readiness(
    *,
    stage: str,
    config: Dict[str, Any],
    run_dir: Path,
    apply_requested: bool,
    force: bool = False,
) -> Dict[str, Any]:
    """Return a gate result. ``ok`` is True only when apply may proceed."""
    checks: List[Dict[str, Any]] = []

    checks.append(
        _check(
            "apply_flag",
            bool(apply_requested),
            "--apply was passed" if apply_requested else "user did not pass --apply",
        )
    )

    current_hash = lab_state.config_hash(config)
    validated = lab_state.load_stage(run_dir, "validate-config")
    meta = lab_state.load_stage(run_dir, "run-metadata")
    validated_hash = (validated or {}).get("config_hash")
    meta_hash = (meta or {}).get("config_hash")
    hash_ok = bool(
        validated
        and (
            (validated_hash and validated_hash == current_hash)
            or (not validated_hash and meta_hash == current_hash)
        )
    )
    if not validated:
        hash_detail = "validate-config.json missing for this run"
    elif validated_hash and validated_hash != current_hash:
        hash_detail = "config hash changed since validate-config"
    elif hash_ok:
        hash_detail = "config validated this run (hash unchanged)"
    else:
        hash_detail = "validate-config present but config hash could not be confirmed"
    checks.append(_check("config_validated", hash_ok, hash_detail))

    safety = config.get("safety") if isinstance(config.get("safety"), dict) else {}
    host = config.get("host") if isinstance(config.get("host"), dict) else {}
    mgmt_ok = (
        safety.get("forbid_management_interface_changes") is True
        and bool(host.get("management_interface"))
    )
    checks.append(
        _check(
            "management_interface_protected",
            mgmt_ok,
            "forbid_management_interface_changes=true and management_interface set"
            if mgmt_ok
            else "management interface is not protected",
        )
    )

    profile_issues = validate_vm2vm_config(config)
    checks.append(
        _check(
            "vendor_profile_valid",
            not profile_issues,
            "vendor profile valid" if not profile_issues else "; ".join(profile_issues),
        )
    )

    if _nvidia_dpu_profile(config):
        nvidia = config.get("nvidia") if isinstance(config.get("nvidia"), dict) else {}
        host_fwd = (
            config.get("host_forwarding_control")
            if isinstance(config.get("host_forwarding_control"), dict)
            else {}
        )
        remote = (
            config.get("remote_execution")
            if isinstance(config.get("remote_execution"), dict)
            else {}
        )
        params = ssh_params(config)
        dpu_issues = validate_nvidia_dpu_mode(config)
        ovs_ok = nvidia.get("ovs_location") == "dpu"
        rep_ok = nvidia.get("representor_location") == "dpu"
        host_ovs_off = (
            nvidia.get("allow_host_ovs_for_forwarding") is False
            and host_fwd.get("enabled") is False
        )
        remote_ok = (
            remote.get("enabled") is True
            and remote.get("target") == "dpu"
            and bool(params.get("host"))
            and bool(params.get("user"))
        )
        dpu_ok = (
            not dpu_issues and ovs_ok and rep_ok and host_ovs_off and remote_ok and is_dpu_mode(config)
        )
        parts = []
        if not ovs_ok:
            parts.append("nvidia.ovs_location must be dpu")
        if not rep_ok:
            parts.append("nvidia.representor_location must be dpu")
        if not host_ovs_off:
            parts.append("host OVS forwarding must be disabled")
        if not remote_ok:
            parts.append("DPU remote execution is not configured")
        parts.extend(dpu_issues)
        checks.append(
            _check(
                "nvidia_dpu_mode",
                dpu_ok,
                "NVIDIA DPU-mode placement and remote execution OK"
                if dpu_ok
                else "; ".join(parts) or "NVIDIA DPU-mode misconfigured",
            )
        )

    missing_priors = lab_state.missing_dependencies(run_dir, stage, hard=False)
    checks.append(
        _check(
            "prior_stage_outputs",
            not missing_priors,
            "required prior stage outputs present"
            if not missing_priors
            else "missing: " + ", ".join(missing_priors),
        )
    )

    skip_plan = bool(force and stage in FORCE_ALLOWED_STAGES)
    if force and stage in NETWORK_SETUP_STAGES:
        checks.append(
            _check(
                "force_not_allowed_for_forwarding",
                False,
                "--force is not allowed for forwarding/OVS setup",
            )
        )
    elif force and stage not in FORCE_ALLOWED_STAGES:
        checks.append(
            _check(
                "force_not_allowed",
                False,
                "--force is only allowed for cleanup, not for %s" % stage,
            )
        )

    prior = lab_state.load_stage(run_dir, stage)
    plan_ok = bool(prior and prior.get("dry_run") is True)
    if skip_plan:
        checks.append(
            _check(
                "dry_run_plan",
                True,
                "dry-run plan check skipped (--force cleanup)",
            )
        )
    else:
        checks.append(
            _check(
                "dry_run_plan",
                plan_ok,
                "prior dry-run plan exists for this stage"
                if plan_ok
                else "stage-specific dry-run plan missing (run this stage without --apply first)",
            )
        )

    failed = [c for c in checks if not c["ok"]]
    ok = not failed
    result: Dict[str, Any] = {
        "ok": ok,
        "stage": stage,
        "checks": checks,
        "failed_checks": [c["name"] for c in failed],
        "error": None if ok else APPLY_BLOCKED_BY_READINESS_GATE,
        "message": (
            "apply readiness gate passed"
            if ok
            else "%s: %s"
            % (
                APPLY_BLOCKED_BY_READINESS_GATE,
                "; ".join("%s (%s)" % (c["name"], c["detail"]) for c in failed),
            )
        ),
        "force": bool(force),
    }
    return result


def gate_error_payload(stage: str, gate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": stage,
        "success": False,
        "error": APPLY_BLOCKED_BY_READINESS_GATE,
        "message": gate.get("message"),
        "failed_checks": gate.get("failed_checks") or [],
        "checks": gate.get("checks") or [],
        "force": gate.get("force"),
    }


def should_evaluate(stage: str, apply_allowed: bool) -> bool:
    """Only gate when a destructive stage would actually mutate."""
    return stage in DESTRUCTIVE_APPLY_STAGES and bool(apply_allowed)
