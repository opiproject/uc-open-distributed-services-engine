#!/usr/bin/env python3
"""lab_state.py — stage result directory contract for labctl runs.

Every labctl run uses:

    tests/results/<vendor>/<run-id>/

Stage JSON is written atomically. Unknown facts stay null; missing
required prior stages fail with STAGE_MISSING_DEPENDENCY rather than
inventing state.

See docs/operations/lab-state-contract.md.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

STAGE_MISSING_DEPENDENCY = "STAGE_MISSING_DEPENDENCY"

# Canonical on-disk names (Prompt R6). Hyphens in the filename.
STAGE_FILES: Dict[str, str] = {
    "run-metadata": "run-metadata.json",
    "validate-config": "validate-config.json",
    "discover": "discover.json",
    "prepare-host": "prepare-host.json",
    "prepare-vfs": "prepare-vfs.json",
    "prepare-switchdev": "prepare-switchdev.json",
    "discover-representors": "representor-map.json",
    "prepare-ovs": "prepare-ovs.json",
    "create-vms": "create-vms.json",
    "verify-vms": "verify-vms.json",
    "attach-vfs": "attach-vfs.json",
    "configure-forwarding": "configure-forwarding.json",
    "configure-vm-ips": "configure-vm-ips.json",
    "run-traffic": "run-traffic.json",
    "validate-offload": "offload-validation.json",
    "collect-evidence": "collect-evidence.json",
    "cleanup": "cleanup.json",
}

# Older labctl underscore names still readable (and still written as aliases).
LEGACY_FILES: Dict[str, str] = {
    "validate-config": "validate_config.json",
    "prepare-host": "prepare_host.json",
    "prepare-vfs": "prepare_vfs.json",
    "prepare-switchdev": "prepare_switchdev.json",
    "discover-representors": "discover_representors.json",
    "prepare-ovs": "prepare_ovs.json",
    "create-vms": "create_vms.json",
    "attach-vfs": "attach_vfs.json",
    "configure-forwarding": "configure_forwarding.json",
    "configure-vm-ips": "configure_vm_ips.json",
    "run-traffic": "run_traffic.json",
    "validate-offload": "validate_offload.json",
    "collect-evidence": "collect_evidence.json",
}

# Documented graph of prior outputs each stage is expected to consume.
STAGE_DEPENDENCIES: Dict[str, List[str]] = {
    "discover": [],
    "validate-config": [],
    "prepare-host": ["validate-config"],
    "prepare-vfs": ["validate-config", "discover"],
    "prepare-switchdev": ["discover"],
    "discover-representors": ["discover"],
    "prepare-ovs": ["discover-representors"],
    "create-vms": ["validate-config"],
    "verify-vms": ["create-vms"],
    "attach-vfs": ["create-vms", "verify-vms", "discover"],
    "configure-forwarding": ["discover-representors", "prepare-ovs"],
    "configure-vm-ips": ["create-vms"],
    "run-traffic": ["configure-vm-ips", "configure-forwarding"],
    "validate-offload": ["run-traffic"],
    "collect-evidence": ["validate-offload"],
    "cleanup": [],
}

# labctl hard-fails these before running the stage (others warn via report).
HARD_DEPENDENCIES: Dict[str, List[str]] = {
    "configure-forwarding": ["discover-representors"],
}


def config_hash(config: Optional[Dict[str, Any]]) -> str:
    payload = json.dumps(config or {}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_path(run_dir: Path, stage: str) -> Path:
    name = STAGE_FILES.get(stage)
    if not name:
        name = "%s.json" % stage.replace("_", "-")
    return run_dir / name


def legacy_path(run_dir: Path, stage: str) -> Optional[Path]:
    name = LEGACY_FILES.get(stage)
    if not name:
        return None
    return run_dir / name


def atomic_write_json(path: Path, data: Any) -> Path:
    """Write JSON via a sibling .tmp then replace (atomic on POSIX and Win)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, indent=2, sort_keys=True, default=str)
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_stage(run_dir: Path, stage: str) -> Optional[Dict[str, Any]]:
    data = _read_json(canonical_path(run_dir, stage))
    if data is not None:
        return data
    legacy = legacy_path(run_dir, stage)
    if legacy is not None:
        data = _read_json(legacy)
        if data is not None:
            return data
    underscore = run_dir / ("%s.json" % stage.replace("-", "_"))
    if underscore != canonical_path(run_dir, stage):
        return _read_json(underscore)
    return None


def save_stage(
    run_dir: Path,
    stage: str,
    data: Dict[str, Any],
    *,
    config_hash_value: Optional[str] = None,
    dry_run: Optional[bool] = None,
    apply: Optional[bool] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Atomically write canonical + legacy alias. Additive metadata only."""
    payload = dict(data)
    if "timestamp" not in payload:
        payload["timestamp"] = timestamp or now_iso()
    if config_hash_value and "config_hash" not in payload:
        payload["config_hash"] = config_hash_value
    if dry_run is not None and "dry_run" not in payload:
        payload["dry_run"] = dry_run
    if apply is not None and "apply" not in payload:
        payload["apply"] = apply
    path = atomic_write_json(canonical_path(run_dir, stage), payload)
    legacy = legacy_path(run_dir, stage)
    if legacy is not None and legacy != path:
        atomic_write_json(legacy, payload)
    return path


def write_run_metadata(
    run_dir: Path,
    *,
    run_id: str,
    vendor: str,
    config_path: Optional[str] = None,
    config_hash_value: Optional[str] = None,
    dry_run: bool = True,
    apply: bool = False,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    existing = load_stage(run_dir, "run-metadata") or {}
    meta = {
        "stage": "run-metadata",
        "run_id": run_id,
        "vendor": vendor,
        "config_path": config_path,
        "config_hash": config_hash_value or existing.get("config_hash"),
        "dry_run": dry_run,
        "apply": apply,
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    return atomic_write_json(canonical_path(run_dir, "run-metadata"), meta)


def missing_dependencies(
    run_dir: Path,
    stage: str,
    *,
    hard: bool = False,
    graph: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    deps = (graph or (HARD_DEPENDENCIES if hard else STAGE_DEPENDENCIES)).get(stage) or []
    missing: List[str] = []
    for dep in deps:
        if load_stage(run_dir, dep) is None:
            missing.append(dep)
    return missing


def dependency_error(stage: str, missing: Sequence[str]) -> Dict[str, Any]:
    return {
        "stage": stage,
        "success": False,
        "error": STAGE_MISSING_DEPENDENCY,
        "missing_stages": list(missing),
        "message": (
            "%s: required prior stage output missing: %s"
            % (STAGE_MISSING_DEPENDENCY, ", ".join(missing))
        ),
    }


def load_all_stages(run_dir: Path) -> Dict[str, Optional[Dict[str, Any]]]:
    """Load every known stage. Keys include hyphen names and underscore aliases."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for stage in STAGE_FILES:
        data = load_stage(run_dir, stage)
        out[stage] = data
        out[stage.replace("-", "_")] = data
    out["representor_map"] = out.get("discover-representors")
    out["offload_validation"] = out.get("validate-offload")
    return out
