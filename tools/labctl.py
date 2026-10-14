#!/usr/bin/env python3
"""labctl.py — VM-to-VM single-DPU lab workflow orchestrator.

The single user-facing control point for the lab use case described in
config/README-lab-vm2vm.md: one server, one DPU/IPU, two SR-IOV VFs, two
KVM/QEMU VMs, DPU-side representors on OVS, traffic between the VMs, and
offload evidence collection. See
docs/architecture/forwarding-intent-model.md for why users should call
this tool instead of OVS/DAO/OPI/DOCA/tc/devlink/vendor SDKs directly.

Usage:
    python3 tools/labctl.py vm2vm <stage> --config <config-file> \\
        [--dry-run] [--apply] [--json] [--verbose] [--run-id ID]

Stages (see docs/operations/lab-runbook-vm2vm-single-dpu.md):
    discover, validate-config, prepare-host, prepare-vfs,
    prepare-switchdev, discover-representors, prepare-ovs, create-vms,
    attach-vfs, configure-forwarding, configure-vm-ips, run-traffic,
    validate-offload, collect-evidence, cleanup, run-all-safe

Default is dry-run. Destructive stages refuse to execute unless --apply
is passed AND the loaded config sets safety.dry_run_default: false AND
the apply readiness gate passes (APPLY_BLOCKED_BY_READINESS_GATE otherwise).
CLI --dry-run always wins. --force is cleanup-only (never forwarding setup).
labctl NEVER touches host.management_interface or host.preserve_interfaces,
regardless of flags (see plugins.base.vm2vm_config.preserved_interfaces /
validate_vm2vm_config).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from plugins import load_backend  # noqa: E402
from plugins.base.commands import command_exists, run  # noqa: E402
from plugins.base.errors import UnknownBackend  # noqa: E402
from plugins.base.lab_config import normalize_vendor  # noqa: E402
from plugins.base.remote import is_dpu_mode  # noqa: E402
from plugins.base.vm2vm_config import (  # noqa: E402
    build_forwarding_intent,
    config_dry_run,
    get_vm_by_vf_index,
    intel_backend_config,
    load_vm2vm_config,
    preserved_interfaces,
    public_vm2vm_view,
    representor_mapping_settings,
    validate_forwarding_section,
    validate_vm2vm_config,
)
from plugins.base.vm_vfio import (  # noqa: E402
    GUEST_SSH_REQUIRED,
    assignment_conflicts,
    hostdev_xml_snippet,
    iommu_vfio_readiness,
    render_libvirt_xml,
    resolve_vf_pci,
    split_pci_address,
    start_vms_requested,
    wait_for_boot_requested,
)
import apply_readiness_gate  # noqa: E402
import lab_state  # noqa: E402

LOG = logging.getLogger("labctl")

RESULTS_ROOT = ROOT / "tests" / "results"

STAGES = (
    "discover",
    "validate-config",
    "prepare-host",
    "prepare-vfs",
    "prepare-switchdev",
    "discover-representors",
    "prepare-ovs",
    "create-vms",
    "verify-vms",
    "attach-vfs",
    "configure-forwarding",
    "configure-vm-ips",
    "run-traffic",
    "validate-offload",
    "collect-evidence",
    "cleanup",
)

# Order used by run-all-safe. Matches docs/operations/lab-runbook-vm2vm-single-dpu.md.
RUN_ALL_SAFE_ORDER = (
    "discover",
    "validate-config",
    "prepare-host",
    "prepare-vfs",
    "prepare-switchdev",
    "discover-representors",
    "prepare-ovs",
    "create-vms",
    "verify-vms",
    "attach-vfs",
    "configure-forwarding",
    "configure-vm-ips",
    "run-traffic",
    "validate-offload",
    "collect-evidence",
)

DESTRUCTIVE_STAGES = frozenset(
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


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Run/result directory management (tests/results/<vendor>/<run-id>/<stage>.json)
# ---------------------------------------------------------------------------

def _vendor_results_dir(vendor_type: str) -> Path:
    return RESULTS_ROOT / vendor_type


def resolve_run_id(vendor_type: str, explicit: Optional[str]) -> str:
    """Explicit --run-id wins; otherwise reuse the vendor's last run, else start one.

    This lets every stage be independently runnable while still chaining
    naturally onto the most recent run for that vendor without the user
    having to repeat a timestamp on every command.
    """
    if explicit:
        return explicit
    latest_file = _vendor_results_dir(vendor_type) / ".latest"
    if latest_file.exists():
        existing = latest_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_dir_for(vendor_type: str, run_id: str) -> Path:
    return _vendor_results_dir(vendor_type) / run_id


def mark_latest(vendor_type: str, run_id: str) -> None:
    vdir = _vendor_results_dir(vendor_type)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / ".latest").write_text(run_id, encoding="utf-8")


def _stage_file(run_dir: Path, stage: str) -> Path:
    return run_dir / ("%s.json" % stage.replace("-", "_"))


def save_stage_json(
    run_dir: Path,
    stage: str,
    data: Dict[str, Any],
    *,
    config_hash_value: Optional[str] = None,
    dry_run: Optional[bool] = None,
    apply: Optional[bool] = None,
) -> Path:
    return lab_state.save_stage(
        run_dir,
        stage,
        data,
        config_hash_value=config_hash_value,
        dry_run=dry_run,
        apply=apply,
    )


def load_stage_json(run_dir: Path, stage: str) -> Optional[Dict[str, Any]]:
    return lab_state.load_stage(run_dir, stage)


# ---------------------------------------------------------------------------
# dry-run / apply gating (same shape as tools/vendorctl.py)
# ---------------------------------------------------------------------------

def _dry_run(args: argparse.Namespace, config: Optional[Dict[str, Any]]) -> bool:
    if getattr(args, "dry_run", False):
        return True
    if config_dry_run(config):
        return True
    if getattr(args, "apply", False):
        return False
    return True


def _apply_allowed(args: argparse.Namespace, config: Optional[Dict[str, Any]]) -> bool:
    return (not _dry_run(args, config)) and bool(getattr(args, "apply", False))


class StageContext:
    """Bundled state passed to every stage_* function."""

    def __init__(
        self,
        args: argparse.Namespace,
        config: Dict[str, Any],
        vendor_type: str,
        backend: Any,
        run_dir: Path,
        run_id: str,
    ) -> None:
        self.args = args
        self.config = config
        self.vendor_type = vendor_type
        self.backend = backend
        self.run_dir = run_dir
        self.run_id = run_id
        self.dry_run = _dry_run(args, config)
        self.apply_allowed = _apply_allowed(args, config)

    def load_prior(self, stage: str) -> Optional[Dict[str, Any]]:
        return load_stage_json(self.run_dir, stage)


def _backend_for(config: Dict[str, Any]):
    vendor_type = normalize_vendor((config.get("vendor") or {}).get("type"))
    kwargs: Dict[str, Any] = {}
    dpu = config.get("dpu") or {}
    if vendor_type == "intel-ipu":
        merged = intel_backend_config(config)
        merged.update(config)
        kwargs["config"] = merged
    if vendor_type == "marvell-octeon":
        kwargs["configured_dao_app"] = dpu.get("dao_app")
    kwargs["lab_config"] = config
    return load_backend(vendor_type, **kwargs), vendor_type


def _run_tool(argv: List[str], *, reason: str, timeout: float = 30.0):
    """Read-only helper-tool invocation (dpu_inventory.py, vf_to_representor_map.py, ...).

    Always executed (not gated by labctl's own dry_run) because these
    tools only discover/observe; they do not mutate host state.
    """
    return run(argv, dry_run=False, timeout=timeout, reason=reason)


def _parse_tool_json(cmd) -> Optional[Any]:
    if not cmd.executed or cmd.returncode not in (0, None) or not cmd.stdout:
        return None
    try:
        return json.loads(cmd.stdout)
    except ValueError:
        return None


def _opi_discover(config: Dict[str, Any], backend_result: Any) -> Dict[str, Any]:
    """Same GetDevices / GetInventory implementation the OPI HTTP gateway uses."""
    from plugins.base.opi_lifecycle import get_devices, get_inventory

    details = getattr(backend_result, "details", None)
    lspci = details.get("lspci") if isinstance(details, dict) else None
    functions = getattr(backend_result, "physical_functions", None) or []
    return {
        "get_devices": _jsonable(get_devices(config, physical_functions=functions)),
        "get_inventory": _jsonable(get_inventory(lspci_text=lspci if isinstance(lspci, str) else None)),
    }


def _opi_set_num_vfs(result: Any, *, vf_cnt: Any, dry_run: bool) -> Any:
    from plugins.base.opi_lifecycle import set_num_vfs_from_result

    return set_num_vfs_from_result(result, vf_cnt=vf_cnt, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_discover(ctx: StageContext) -> Dict[str, Any]:
    backend_result = ctx.backend.discover()
    cmd = _run_tool(
        [sys.executable, str(ROOT / "tools" / "inventory" / "dpu_inventory.py")],
        reason="labctl discover: dpu_inventory.py",
    )
    inventory = _parse_tool_json(cmd)
    return {
        "stage": "discover",
        "backend_result": _jsonable(backend_result),
        "dpu_inventory": inventory,
        "dpu_inventory_error": None if inventory is not None else (cmd.stderr or cmd.reason),
        "opi": _opi_discover(ctx.config, backend_result),
    }


def stage_validate_config(ctx: StageContext) -> Dict[str, Any]:
    issues = validate_vm2vm_config(ctx.config) + validate_forwarding_section(ctx.config)
    dpu_ovs = None
    nvidia = ctx.config.get("nvidia") or {}
    if nvidia.get("mode") == "dpu":
        from plugins.nvidia_bluefield.dpu_ovs import validate_dpu_ovs_environment

        dpu_ovs = _jsonable(validate_dpu_ovs_environment(ctx.config, dry_run=True))
    return {
        "stage": "validate-config",
        "valid": not issues,
        "issues": issues,
        "config": public_vm2vm_view(ctx.config),
        "preserved_interfaces": preserved_interfaces(ctx.config),
        "dpu_ovs_validation": dpu_ovs,
    }


def stage_prepare_host(ctx: StageContext) -> Dict[str, Any]:
    tools = {
        name: command_exists(name)
        for name in ("qemu-img", "virsh", "virt-install", "ssh", "ping", "iperf3")
    }
    missing = [name for name, present in tools.items() if not present]
    return {
        "stage": "prepare-host",
        "tools": tools,
        "message": (
            "host readiness check only; labctl does not invoke ansible-playbook. "
            "Run ansible/roles/host_base separately for full provisioning."
            if not missing
            else "missing tools on this host: %s -- see ansible/roles/host_base" % ", ".join(missing)
        ),
        "dry_run": ctx.dry_run,
    }


def stage_prepare_vfs(ctx: StageContext) -> Dict[str, Any]:
    dpu = ctx.config.get("dpu") or {}
    result = ctx.backend.configure_sriov(
        pf=dpu.get("pf_name"), numvfs=dpu.get("num_vfs"), dry_run=ctx.dry_run
    )
    return {
        "stage": "prepare-vfs",
        # pf_name/pf_pci/num_vfs recorded here (not just inside
        # backend_result.details, which vendor stubs don't reliably
        # populate) so tools/lab_cleanup.py can gate a destructive
        # sriov_numvfs=0 reset on a matching prior state file instead of
        # trusting the live --config alone (see
        # docs/operations/cleanup-and-rollback.md).
        "pf_name": dpu.get("pf_name"),
        "pf_pci": dpu.get("pf_pci"),
        "num_vfs": dpu.get("num_vfs"),
        "backend_result": _jsonable(result),
        "opi_set_num_vfs": _jsonable(
            _opi_set_num_vfs(result, vf_cnt=dpu.get("num_vfs"), dry_run=ctx.dry_run)
        ),
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
    }


def stage_prepare_switchdev(ctx: StageContext) -> Dict[str, Any]:
    dpu = ctx.config.get("dpu") or {}
    if not dpu.get("enable_switchdev"):
        return {
            "stage": "prepare-switchdev",
            "skipped": True,
            "message": "dpu.enable_switchdev is false; not requesting switchdev mode",
        }
    result = ctx.backend.configure_switchdev(pf=dpu.get("pf_name"))
    return {"stage": "prepare-switchdev", "backend_result": _jsonable(result), "dry_run": ctx.dry_run}


# config vendor.type (hyphenated, config/lab-*-vm2vm.example.yaml) ->
# vf_to_representor_map.py's internal vendor keys (same as
# tools/inventory/dpu_inventory.py's _VENDOR_PCI_IDS). Kept as a small
# local table rather than importing across the tools/ boundary.
_VENDOR_TYPE_TO_HINT = {
    "nvidia-bluefield": "nvidia_mellanox_bluefield",
    "intel-ipu": "intel_ipu",
    "marvell-octeon": "marvell_octeon",
    "amd-pensando": "amd_pensando",
}


def stage_discover_representors(ctx: StageContext) -> Dict[str, Any]:
    dpu = ctx.config.get("dpu") or {}
    vendor_cfg = ctx.config.get("vendor") or {}
    pf_name = dpu.get("pf_name")
    pf_pci = dpu.get("pf_pci")
    vendor_hint = _VENDOR_TYPE_TO_HINT.get(vendor_cfg.get("type"))
    settings = representor_mapping_settings(ctx.config)
    backend_result = ctx.backend.discover_representors(pf=pf_name)
    argv = [
        sys.executable,
        str(ROOT / "tools" / "topology-discovery" / "vf_to_representor_map.py"),
        "--json",
        "--location",
        settings["location"],
        "--config",
        ctx.args.config,
    ]
    if settings["location"] == "host":
        if pf_name:
            argv += ["--pf", pf_name]
        if pf_pci:
            argv += ["--pf-pci", pf_pci]
        if vendor_hint:
            argv += ["--vendor", vendor_hint]
    else:
        if pf_pci:
            argv += ["--pf-pci", pf_pci]
        if settings.get("model") == "dpdk" or settings.get("strategy") == "marvell-dpdk-representor":
            argv += ["--model", "dpdk", "--vendor", "marvell-octeon"]
    # location=="dpu" with remote_execution.dry_run_default=false makes this
    # subprocess run up to 3 *sequential* real SSH round trips to the DPU
    # (ip -d link show / devlink port show / ovs-vsctl show, each its own
    # fresh ssh connection -- see collect_dpu_samples_via_ssh() in
    # vf_to_representor_map.py). The default _run_tool timeout (30s) is
    # sized for a single local/quick call and is not nearly enough budget
    # for three of those in a row -- scale with dpu.command_timeout_seconds
    # (docs/operations/dpu-remote-execution.md) so operators on a slower
    # management link can raise one config knob instead of editing code.
    # A timeout that still fails at this generous budget is a genuine
    # reachability/hang problem worth surfacing, not a false alarm.
    if settings["location"] == "dpu":
        per_command_timeout = float(dpu.get("command_timeout_seconds") or 30)
        subprocess_timeout = per_command_timeout * 3 + 20.0
    else:
        subprocess_timeout = 30.0
    cmd = _run_tool(
        argv,
        reason="labctl discover-representors: vf_to_representor_map.py",
        timeout=subprocess_timeout,
    )
    mapping = _parse_tool_json(cmd)
    return {
        "stage": "discover-representors",
        "location": settings["location"],
        "strategy": settings["strategy"],
        "require_high_confidence": settings["require_high_confidence"],
        "backend_result": _jsonable(backend_result),
        "vf_to_representor_map": mapping,
        "vf_to_representor_map_error": None if mapping is not None else (cmd.stderr or cmd.reason),
    }


def stage_prepare_ovs(ctx: StageContext) -> Dict[str, Any]:
    ovs_cfg = ctx.config.get("ovs") or {}
    nvidia = ctx.config.get("nvidia") or {}
    if nvidia.get("mode") == "dpu":
        from plugins.nvidia_bluefield.dpu_ovs import (
            OVS_DOCA_MISSING,
            OVS_MISSING,
            validate_dpu_ovs_environment,
        )

        check = validate_dpu_ovs_environment(ctx.config, dry_run=True)
        status = (check.details or {}).get("dpu_ovs_status")
        if status == OVS_MISSING:
            return {
                "stage": "prepare-ovs",
                "refused": True,
                "reason": "DPU-side OVS is missing; will not configure host OVS instead",
                "dpu_ovs_validation": _jsonable(check),
                "dry_run": ctx.dry_run,
            }
        if status == OVS_DOCA_MISSING:
            return {
                "stage": "prepare-ovs",
                "refused": True,
                "reason": "OVS-DOCA required but not listed in datapath_types; enable fallback explicitly to continue",
                "dpu_ovs_validation": _jsonable(check),
                "dry_run": ctx.dry_run,
            }
    result = ctx.backend.configure_ovs_bridge(bridge=ovs_cfg.get("bridge"))
    return {
        "stage": "prepare-ovs",
        "backend_result": _jsonable(result),
        "bridge": ovs_cfg.get("bridge"),
        "lab_owned": True,
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
    }


def _split_pci_address(pci: str) -> Optional[Dict[str, str]]:
    """'0000:03:00.2' -> {'domain': '0000', 'bus': '03', 'slot': '00', 'function': '2'}."""
    return split_pci_address(pci)


def _mapping_from_prior(ctx: StageContext) -> Optional[Dict[str, Any]]:
    prior = ctx.load_prior("discover-representors") or {}
    mapping = prior.get("vf_to_representor_map")
    return mapping if isinstance(mapping, dict) else None


def _libvirt_xml_for_vm(vm: Dict[str, Any], *, include_vf_hostdev: bool = True) -> str:
    """Render qemu/libvirt-xml/vm-vfio-template.xml for one vms[] entry."""
    template_path = ROOT / "qemu" / "libvirt-xml" / "vm-vfio-template.xml"
    if not template_path.exists():
        raise FileNotFoundError(str(template_path))
    return render_libvirt_xml(
        template_path.read_text(encoding="utf-8"),
        vm,
        include_vf_hostdev=include_vf_hostdev,
    )


_UUID_COLLISION_RE = re.compile(r"already exists with uuid\s+([0-9a-fA-F-]{8,36})")


def _recover_from_define_uuid_collision(
    name: str,
    define_result,
    xml_path: Path,
    *,
    dry_run: bool,
) -> Dict[str, Any]:
    """`virsh define` fails with "already exists with uuid <X>" when a
    domain with this name is already persisted under a different UUID --
    typically a leftover from before labctl started rendering
    deterministic UUIDs (see deterministic_domain_uuid() in
    plugins/base/vm_vfio.py), or from an earlier partial run. `virsh
    destroy`/`shutdown` only stop a *running* domain; neither removes its
    persisted definition, so this collision does not go away on its own.

    Recovery is automatic ONLY when the stale domain is confirmed NOT
    running via a fresh `virsh domstate` query (never assumed): undefine
    the stale definition, then retry `virsh define` once. A domain that
    is running, or whose state could not be confirmed, is never touched
    -- that is live guest state, not proof of staleness, and labctl does
    not stop or undefine a VM the caller did not ask it to stop.
    """
    match = _UUID_COLLISION_RE.search(define_result.stderr or "")
    stale_uuid_note = " (%s)" % match.group(1) if match else ""
    info: Dict[str, Any] = {
        "diagnosis": "uuid_collision",
        "stale_uuid": match.group(1) if match else None,
        "auto_recovered": False,
    }
    domstate_cmd = run(
        ["virsh", "domstate", name],
        dry_run=dry_run,
        reason="labctl create-vms: virsh domstate %s (checking if the stale definition is safe to remove)" % name,
    )
    info["domstate_command"] = _jsonable(domstate_cmd)
    if not domstate_cmd.executed or domstate_cmd.returncode != 0:
        info["message"] = (
            "domain '%s' is already defined under a different uuid%s, and its "
            "current state could not be confirmed; not touching it "
            "automatically. Check it yourself (`virsh domstate %s`), then "
            "`virsh undefine %s` if it is not the domain you want, and re-run "
            "create-vms --apply." % (name, stale_uuid_note, name, name)
        )
        return info
    state = (domstate_cmd.stdout or "").strip().lower()
    info["stale_domain_state"] = state
    if state == "running":
        info["message"] = (
            "domain '%s' is already defined under a different uuid%s and is "
            "currently RUNNING; labctl will not stop or undefine a running "
            "domain automatically. If this running VM is not the one you "
            "intended, stop it yourself (`virsh shutdown %s` or `virsh destroy "
            "%s`), then `virsh undefine %s`, then re-run create-vms --apply."
            % (name, stale_uuid_note, name, name, name)
        )
        return info
    # Confirmed not running (shut off/paused/etc.) -- safe to undefine and retry.
    undefine_cmd = run(
        ["virsh", "undefine", name],
        dry_run=dry_run,
        reason="labctl create-vms: virsh undefine stale (confirmed not-running) %s" % name,
    )
    info["undefine_command"] = _jsonable(undefine_cmd)
    if not undefine_cmd.executed or undefine_cmd.returncode != 0:
        info["message"] = (
            "domain '%s' was confirmed not running (domstate=%r) but `virsh "
            "undefine` failed: %s" % (name, state, undefine_cmd.stderr or undefine_cmd.reason)
        )
        return info
    redefine_cmd = run(
        ["virsh", "define", str(xml_path)],
        dry_run=dry_run,
        reason="labctl create-vms: virsh define %s (retry after undefining stale not-running domain)" % name,
    )
    info["redefine_command"] = _jsonable(redefine_cmd)
    info["auto_recovered"] = bool(redefine_cmd.executed and redefine_cmd.returncode == 0)
    if info["auto_recovered"]:
        info["message"] = (
            "domain '%s' was defined under a stale uuid%s while shut off; "
            "labctl undefined it and re-defined it from the current config."
            % (name, stale_uuid_note)
        )
    else:
        info["message"] = (
            "undefined the stale domain '%s' but the retried `virsh define` "
            "still failed: %s" % (name, redefine_cmd.stderr or redefine_cmd.reason)
        )
    return info


def stage_create_vms(ctx: StageContext) -> Dict[str, Any]:
    vms = list(ctx.config.get("vms") or [])
    mapping = _mapping_from_prior(ctx)
    resolved: List[Dict[str, Any]] = []
    for vm in vms:
        row = dict(vm)
        row["vf_pci"] = resolve_vf_pci(vm, mapping)
        resolved.append(row)
    conflicts = assignment_conflicts(resolved)
    iommu = iommu_vfio_readiness()
    start = start_vms_requested(ctx.config)
    wait_boot = wait_for_boot_requested(ctx.config)
    xml_dir = ctx.run_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    planned: List[Dict[str, Any]] = []
    commands: List[Dict[str, Any]] = []
    for vm in resolved:
        entry: Dict[str, Any] = {
            "name": vm.get("name"),
            "vf_index": vm.get("vf_index"),
            "vf_pci": vm.get("vf_pci"),
        }
        if conflicts:
            entry["error"] = "; ".join(conflicts)
            planned.append(entry)
            continue
        include_vf = bool(vm.get("vf_pci"))
        try:
            xml = _libvirt_xml_for_vm(vm, include_vf_hostdev=include_vf)
        except FileNotFoundError as exc:
            entry["error"] = "libvirt template not found: %s (see qemu/images/README.md)" % exc
            planned.append(entry)
            continue
        except ValueError as exc:
            entry["error"] = str(exc)
            planned.append(entry)
            continue
        xml_path = xml_dir / ("%s.xml" % (vm.get("name") or "vm"))
        xml_path.write_text(xml, encoding="utf-8")
        entry["xml_path"] = str(xml_path)
        entry["xml_preview"] = xml[:400]
        entry["include_vf_hostdev"] = include_vf
        name = vm.get("name") or ""
        define = run(
            ["virsh", "define", str(xml_path)],
            dry_run=ctx.dry_run,
            reason="labctl create-vms: virsh define %s" % name,
        )
        commands.append(_jsonable(define))
        entry["define"] = _jsonable(define)
        entry["executed"] = define.executed
        define_failed = define.executed and define.returncode not in (0, None)
        if define_failed and _UUID_COLLISION_RE.search(define.stderr or ""):
            recovery = _recover_from_define_uuid_collision(name, define, xml_path, dry_run=ctx.dry_run)
            entry["define_recovery"] = recovery
            for key in ("domstate_command", "undefine_command", "redefine_command"):
                if recovery.get(key) is not None:
                    commands.append(recovery[key])
            if recovery.get("redefine_command") is not None:
                entry["define"] = recovery["redefine_command"]
            define_failed = not recovery.get("auto_recovered")
        if define_failed:
            entry["error"] = (
                entry.get("define_recovery", {}).get("message")
                or ("virsh define failed: %s" % (define.stderr or define.reason))
            )
            entry["start"] = None
            entry["wait_for_boot"] = False
            # Do not start a domain whose current definition labctl could
            # not confirm matches this render -- a stale/mismatched
            # definition reporting "started" is worse than no VM at all
            # (see docs/operations/lab-runbook-vm2vm-single-dpu.md).
            planned.append(entry)
            continue
        if start:
            start_cmd = run(
                ["virsh", "start", name],
                dry_run=ctx.dry_run,
                reason="labctl create-vms: virsh start %s" % name,
            )
            commands.append(_jsonable(start_cmd))
            entry["start"] = _jsonable(start_cmd)
        if wait_boot:
            entry["wait_for_boot"] = True
            entry["wait_for_boot_note"] = (
                "wait_for_boot requested; not waiting in dry-run. "
                "Apply path would poll virsh domstate / qemu-guest-agent."
            )
        else:
            entry["wait_for_boot"] = False
        planned.append(entry)
    return {
        "stage": "create-vms",
        "vms": planned,
        "commands": commands,
        "conflicts": conflicts,
        "iommu_vfio": iommu,
        "start_vms": start,
        "wait_for_boot": wait_boot,
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
    }


def _verify_one_vm(
    vm: Dict[str, Any],
    *,
    run_fn=run,
    command_exists_fn=command_exists,
) -> Dict[str, Any]:
    """Read-only per-VM check: is it actually running right now, and is
    it reachable over SSH on its management NIC with the key already
    provisioned into the guest image? Always calls run_fn for real
    (dry_run=False) -- nothing here mutates guest or host state, and a
    plan/preview would defeat the point of the check.

    Deliberately a single shot, not a poll loop: if the guest is still
    booting, re-run `labctl vm2vm verify-vms` after a few seconds rather
    than having this block for an unbounded amount of time.
    """
    name = vm.get("name")
    entry: Dict[str, Any] = {"name": name, "running": None, "ssh_reachable": None}
    if not name:
        entry["error"] = "vms[] entry missing name"
        return entry
    if not command_exists_fn("virsh"):
        entry["error"] = "virsh not found on PATH"
        return entry
    domstate_cmd = run_fn(
        ["virsh", "domstate", name],
        dry_run=False,
        reason="labctl verify-vms: virsh domstate %s" % name,
    )
    entry["domstate_command"] = _jsonable(domstate_cmd)
    if not domstate_cmd.executed:
        entry["message"] = "could not query domain state: %s" % (
            domstate_cmd.stderr or domstate_cmd.reason
        )
        return entry
    state = (domstate_cmd.stdout or "").strip().lower()
    entry["domstate"] = state
    running = domstate_cmd.returncode == 0 and state == "running"
    entry["running"] = running
    if not running:
        entry["message"] = "domain not confirmed running (domstate=%r); not probing SSH" % state
        return entry

    ssh_user = vm.get("ssh_user")
    mgmt_host = _vm_management_host(vm)
    entry["management_host"] = mgmt_host
    if not mgmt_host or not ssh_user:
        entry["code"] = GUEST_SSH_REQUIRED
        entry["message"] = (
            "%s: management_ip not set and virsh domifaddr found no address, "
            "or vms[].ssh_user missing; cannot probe SSH" % GUEST_SSH_REQUIRED
        )
        return entry
    if not command_exists_fn("ssh"):
        entry["error"] = "ssh not found on PATH"
        return entry
    probe_token = "odse-verify-vms-ok"
    identity_args = _vm_ssh_identity_args(vm)
    ssh_cmd = run_fn(
        [
            "ssh",
            *identity_args,
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            "%s@%s" % (ssh_user, mgmt_host),
            "echo", probe_token,
        ],
        dry_run=False,
        timeout=10,
        reason="labctl verify-vms: ssh %s" % name,
    )
    entry["ssh_command"] = _jsonable(ssh_cmd)
    reachable = bool(
        ssh_cmd.executed and ssh_cmd.returncode == 0 and probe_token in (ssh_cmd.stdout or "")
    )
    entry["ssh_reachable"] = reachable
    if not reachable:
        if identity_args:
            key_hint = "vms[].ssh_key_path (%s) was used explicitly" % vm.get("ssh_key_path")
        else:
            key_hint = (
                "no vms[].ssh_key_path is set, so this used ssh's own default identity "
                "resolution (ssh-agent / ~/.ssh/config / ~/.ssh/id_*) -- if `ssh -i "
                "<path> %s@%s` works by hand but this probe still fails, the key that "
                "actually works is not the default; set vms[].ssh_key_path to that "
                "path" % (ssh_user, mgmt_host)
            )
        entry["message"] = (
            "SSH probe failed (%s); the guest may still be booting (re-run this stage "
            "after a few seconds), or the SSH key was not actually authorized -- "
            "see docs/operations/vm-image-preparation.md" % key_hint
        )
    return entry


def stage_verify_vms(ctx: StageContext) -> Dict[str, Any]:
    """Read-only gate between create-vms and attach-vfs.

    `virsh list`/the `virsh start` result captured by create-vms can
    look like a VM is running when it actually is not (e.g. a stale
    domain from a previous run, or a start that raced with a disk
    error) -- this stage re-queries `virsh domstate` fresh right now
    and independently probes SSH on the management NIC using the key
    already baked into the guest image, instead of trusting a
    previously-captured command result. Always runs its checks for
    real; there is nothing to plan or mutate, matching discover /
    validate-offload / collect-evidence (--dry-run/--apply do not gate
    it).
    """
    vms = ctx.config.get("vms") or []
    results = [_verify_one_vm(vm) for vm in vms]
    all_ready = bool(results) and all(
        r.get("running") is True and r.get("ssh_reachable") is True for r in results
    )
    return {
        "stage": "verify-vms",
        "vms": results,
        "all_ready": all_ready,
        "dry_run": ctx.dry_run,
    }


def stage_attach_vfs(ctx: StageContext) -> Dict[str, Any]:
    vms = list(ctx.config.get("vms") or [])
    mapping = _mapping_from_prior(ctx)
    resolved: List[Dict[str, Any]] = []
    for vm in vms:
        row = dict(vm)
        row["vf_pci"] = resolve_vf_pci(vm, mapping)
        resolved.append(row)
    conflicts = assignment_conflicts(resolved)
    iommu = iommu_vfio_readiness()
    xml_dir = ctx.run_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    for vm in resolved:
        vf_pci = vm.get("vf_pci")
        entry: Dict[str, Any] = {
            "name": vm.get("name"),
            "vf_index": vm.get("vf_index"),
            "vf_pci": vf_pci,
        }
        if conflicts:
            entry["error"] = "; ".join(conflicts)
            results.append(entry)
            continue
        if not vf_pci:
            entry["skipped"] = True
            entry["message"] = (
                "vf_pci unknown; run discover-representors first (VFIO attach is "
                "not attempted without a confirmed PCI address)"
            )
            results.append(entry)
            continue
        try:
            snippet = hostdev_xml_snippet(str(vf_pci), vm.get("mac"))
        except ValueError as exc:
            entry["error"] = str(exc)
            results.append(entry)
            continue
        snippet_path = xml_dir / ("%s-vf.xml" % (vm.get("name") or "vm"))
        snippet_path.write_text(snippet, encoding="utf-8")
        entry["hostdev_xml_path"] = str(snippet_path)
        cmd = run(
            [
                "virsh",
                "attach-device",
                vm.get("name") or "",
                str(snippet_path),
                "--config",
                "--live",
            ],
            dry_run=ctx.dry_run,
            reason="labctl attach-vfs: virsh attach-device %s" % vm.get("name"),
        )
        entry["command"] = _jsonable(cmd)
        results.append(entry)
    return {
        "stage": "attach-vfs",
        "vms": results,
        "conflicts": conflicts,
        "iommu_vfio": iommu,
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
    }


def _mapping_row_for_vf(mapping: Optional[Dict[str, Any]], vf_index: Optional[int]) -> Optional[Dict[str, Any]]:
    if not mapping or not isinstance(mapping, dict) or vf_index is None:
        return None
    for entry in mapping.get("mappings") or mapping.get("vfs") or []:
        if isinstance(entry, dict) and entry.get("vf_index") == vf_index:
            return entry
    return None


def stage_configure_forwarding(ctx: StageContext) -> Dict[str, Any]:
    issues = validate_forwarding_section(ctx.config)
    if issues:
        return {"stage": "configure-forwarding", "success": False, "errors": issues}

    settings = representor_mapping_settings(ctx.config)
    intent = build_forwarding_intent(ctx.config)
    mapping = None
    prior = ctx.load_prior("discover-representors")
    if prior:
        mapping = prior.get("vf_to_representor_map")

    if settings["location"] == "dpu" and isinstance(mapping, dict) and mapping.get("location") == "host":
        return {
            "stage": "configure-forwarding",
            "success": False,
            "errors": [
                "nvidia DPU mode requires DPU-side representor mapping; "
                "host-side mapping was found. Re-run discover-representors "
                "with representor_mapping.location=dpu (not host-switchdev)."
            ],
            "dry_run": ctx.dry_run,
        }

    confidence_errors: List[str] = []
    for endpoint in intent.endpoints:
        if endpoint.representor and endpoint.vf_pci:
            continue
        vm = get_vm_by_vf_index(ctx.config, endpoint.vf_index) if endpoint.vf_index is not None else None
        if vm and not endpoint.vf_pci:
            endpoint.vf_pci = vm.get("vf_pci")
        entry = _mapping_row_for_vf(mapping if isinstance(mapping, dict) else None, endpoint.vf_index)
        if entry:
            endpoint.representor = endpoint.representor or entry.get("representor_netdev")
            endpoint.vf_pci = endpoint.vf_pci or entry.get("vf_pci") or entry.get("vf_pci_address")
            if settings["require_high_confidence"]:
                conf = entry.get("confidence") or entry.get("mapping_confidence")
                if conf == "high":
                    pass
                elif conf is None and entry.get("representor_netdev"):
                    pass
                else:
                    confidence_errors.append(
                        "vf_index=%s representor=%r confidence=%r (require_high_confidence=true)"
                        % (
                            endpoint.vf_index,
                            entry.get("dpu_representor_name") or entry.get("representor_netdev"),
                            conf,
                        )
                    )

    if confidence_errors:
        return {
            "stage": "configure-forwarding",
            "success": False,
            "errors": [
                "representor mapping is not high-confidence; refusing to configure forwarding"
            ]
            + confidence_errors,
            "intent": _jsonable(intent),
            "dry_run": ctx.dry_run,
        }

    result = ctx.backend.configure_forwarding(intent, dry_run=ctx.dry_run)
    payload = {
        "stage": "configure-forwarding",
        "intent": _jsonable(intent),
        "result": _jsonable(result),
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
        "location": settings["location"],
        "success": bool(getattr(result, "success", False)),
    }
    vendor_type = normalize_vendor((ctx.config.get("vendor") or {}).get("type"))
    if vendor_type == "intel-ipu" and not payload["success"]:
        payload["verdict"] = (getattr(result, "details", None) or {}).get("verdict") or "UNSUPPORTED"
    if vendor_type == "amd-pensando" and not payload["success"]:
        payload["verdict"] = (getattr(result, "details", None) or {}).get("verdict") or "INCONCLUSIVE"
    return payload


def _vm_ssh_identity_args(vm: Dict[str, Any]) -> List[str]:
    """`["-i", <path>]` if `vms[].ssh_key_path` is set (and not an
    unfilled `<PLACEHOLDER>` left over from an example config), else `[]`
    -- i.e. fall back to the ssh client's own default identity
    resolution (ssh-agent, `~/.ssh/config`, `~/.ssh/id_*`) exactly as
    before this field existed. Every ssh invocation against a *guest* VM
    (verify-vms, configure-vm-ips, run-traffic) goes through this, so
    setting `ssh_key_path` once covers all of them. This is a per-VM
    guest key, independent of `dpu.ssh_key_path` (plugins/base/remote.py
    ssh_params), which is for the DPU/IPU management OS instead.
    """
    key_path = vm.get("ssh_key_path")
    if not key_path or str(key_path).strip().startswith("<"):
        return []
    return ["-i", str(key_path)]


def _vm_management_host(vm: Dict[str, Any]) -> Optional[str]:
    """The address labctl should SSH to for this VM: vms[].management_ip, or
    a `virsh domifaddr` lookup. Never the VF ip_cidr (that is the address
    *being assigned to* the interface under test, not a way to reach it
    before this stage has run, and is on a different network than the
    management NIC). Read-only; never guesses or falls back to ip_cidr.
    """
    explicit = vm.get("management_ip")
    if explicit and not str(explicit).strip().startswith("<"):
        return explicit
    name = vm.get("name")
    if not name or not command_exists("virsh"):
        return None
    cmd = run(
        ["virsh", "domifaddr", name],
        dry_run=False,
        reason="labctl: virsh domifaddr %s" % name,
    )
    if not cmd.executed or cmd.returncode != 0 or not cmd.stdout:
        return None
    for line in cmd.stdout.splitlines():
        parts = line.split()
        # Typical row: Name  MAC  Protocol  Address(/prefix)
        if len(parts) >= 4 and parts[2] == "ipv4":
            return parts[3].split("/", 1)[0]
    return None


def stage_configure_vm_ips(ctx: StageContext) -> Dict[str, Any]:
    """SSH to each guest's management NIC and apply its VF static IP via
    guest-network-setup.sh.template.

    Assumes the guest is already SSH-reachable on its management NIC (via
    cloud-init, a prior console-based bootstrap, or virt-customize -- see
    docs/operations/vm-image-preparation.md). labctl does not bootstrap
    first-boot guest connectivity itself, and never uses vms[].ip_cidr
    (the VF address being assigned) as the SSH target.
    """
    vms = ctx.config.get("vms") or []
    template = ROOT / "qemu" / "images" / "guest-network-setup.sh.template"
    results: List[Dict[str, Any]] = []
    template_text = template.read_text(encoding="utf-8") if template.exists() else None
    for vm in vms:
        ip_cidr = vm.get("ip_cidr")
        ssh_user = vm.get("ssh_user")
        entry: Dict[str, Any] = {"name": vm.get("name"), "ip_cidr": ip_cidr}
        if template_text is None:
            entry["error"] = "guest-network-setup.sh.template not found (see qemu/images/README.md)"
            results.append(entry)
            continue
        mgmt_host = _vm_management_host(vm)
        if not mgmt_host:
            entry["code"] = GUEST_SSH_REQUIRED
            entry["error"] = (
                "%s: management_ip not set and virsh domifaddr found no address; "
                "set vms[].management_ip or ensure the domain is running with a DHCP lease"
                % GUEST_SSH_REQUIRED
            )
            results.append(entry)
            continue
        entry["management_host"] = mgmt_host
        cmd = run(
            [
                "ssh",
                *_vm_ssh_identity_args(vm),
                "%s@%s" % (ssh_user, mgmt_host),
                "bash", "-s", "--", ip_cidr or "",
            ],
            dry_run=ctx.dry_run,
            input_text=template_text,
            reason="labctl configure-vm-ips: ssh %s" % vm.get("name"),
        )
        entry["command"] = _jsonable(cmd)
        results.append(entry)
    return {"stage": "configure-vm-ips", "vms": results, "dry_run": ctx.dry_run}


def stage_run_traffic(ctx: StageContext) -> Dict[str, Any]:
    vms = ctx.config.get("vms") or []
    traffic = ctx.config.get("traffic") or {}
    if len(vms) != 2:
        return {"stage": "run-traffic", "error": "exactly 2 vms are required"}
    vm1, vm2 = vms[0], vms[1]
    ip2 = (vm2.get("ip_cidr") or "").split("/", 1)[0]
    mgmt1 = _vm_management_host(vm1)
    mgmt2 = _vm_management_host(vm2)
    if not mgmt1 or not mgmt2:
        return {
            "stage": "run-traffic",
            "error": (
                "management_host unknown for %s"
                % ", ".join(vm.get("name") for vm, host in ((vm1, mgmt1), (vm2, mgmt2)) if not host)
            ),
        }
    commands: List[Dict[str, Any]] = []
    traffic_type = traffic.get("type") or "ping and iperf3"
    if "ping" in traffic_type:
        cmd = run(
            [
                "ssh",
                *_vm_ssh_identity_args(vm1),
                "%s@%s" % (vm1.get("ssh_user"), mgmt1),
                "ping", "-c", "5", ip2,
            ],
            dry_run=ctx.dry_run,
            reason="labctl run-traffic: ping %s -> %s" % (vm1.get("name"), vm2.get("name")),
        )
        commands.append({"kind": "ping", "command": _jsonable(cmd)})
    if "iperf3" in traffic_type:
        server_cmd = run(
            [
                "ssh",
                *_vm_ssh_identity_args(vm2),
                "%s@%s" % (vm2.get("ssh_user"), mgmt2),
                "iperf3", "-s", "-1", "-D",
            ],
            dry_run=ctx.dry_run,
            reason="labctl run-traffic: start iperf3 server on %s" % vm2.get("name"),
        )
        commands.append({"kind": "iperf3-server", "command": _jsonable(server_cmd)})
        # iperf3 -t <duration_seconds> keeps the SSH command running for at
        # least that long server-side, plus SSH connect/auth and iperf3's
        # own startup/summary-print overhead on top. The default subprocess
        # timeout (commands.DEFAULT_TIMEOUT_SECONDS == 30s) is the same
        # value as the traffic.yaml default duration_seconds (30), so this
        # raced and lost every time: the client got cut off right as the
        # real test was finishing, reporting "timed_out": true with a
        # returncode of null even though real throughput was captured in
        # stdout. Give it headroom instead of reusing the bare duration.
        duration_seconds = float(traffic.get("duration_seconds") or 10)
        client_cmd = run(
            [
                "ssh",
                *_vm_ssh_identity_args(vm1),
                "%s@%s" % (vm1.get("ssh_user"), mgmt1),
                "iperf3",
                "-c",
                ip2,
                "-t",
                str(traffic.get("duration_seconds") or 10),
                "-P",
                str(traffic.get("iperf_parallel_streams") or 1),
            ],
            dry_run=ctx.dry_run,
            timeout=duration_seconds + 30.0,
            reason="labctl run-traffic: iperf3 client %s -> %s" % (vm1.get("name"), vm2.get("name")),
        )
        commands.append({"kind": "iperf3-client", "command": _jsonable(client_cmd)})
    return {"stage": "run-traffic", "commands": commands, "dry_run": ctx.dry_run}


def stage_validate_offload(ctx: StageContext) -> Dict[str, Any]:
    """Run the vendor backend's validate_offload() plus
    ovs_offload_checker.py, and link both back to the forwarding intent
    from the prior configure-forwarding stage (Prompt J: intent-to-evidence
    linking). `forwarding` in the returned dict is the single place a
    reader/report_generator.py can find what was *supposed* to be
    configured (name, endpoints, VF PCI addresses, representors, bridge,
    flows, offload requested/configured) next to what the checker
    *observed* (`offload_checker_report`).
    """
    backend_result = ctx.backend.validate_offload()
    forwarding = ctx.load_prior("configure-forwarding")
    intent = (forwarding or {}).get("intent") or {}
    result = (forwarding or {}).get("result") or {}
    endpoints = intent.get("endpoints") or []
    ovs_cfg = ctx.config.get("ovs") or {}
    # Prefer the bridge the backend actually planned against (intent.bridge,
    # set by labctl from ovs.bridge/dpu.preferred_datapath when the stage
    # ran); fall back to the raw config value if configure-forwarding has
    # not run yet.
    bridge = intent.get("bridge") or ovs_cfg.get("bridge")
    interfaces: List[str] = list(result.get("configured_ports") or [])
    vf_pci_addresses = [e.get("vf_pci") for e in endpoints if isinstance(e, dict) and e.get("vf_pci")]
    representors = [e.get("representor") for e in endpoints if isinstance(e, dict) and e.get("representor")]
    forwarding_result_path = str(_stage_file(ctx.run_dir, "configure-forwarding")) if forwarding else None

    argv = [sys.executable, str(ROOT / "tools" / "offload-checker" / "ovs_offload_checker.py"), "--format", "json"]
    if bridge:
        argv += ["--bridge", bridge]
    for iface in interfaces:
        argv += ["--interface", iface]
    if forwarding_result_path:
        argv += ["--forwarding-result", forwarding_result_path]
    if is_dpu_mode(ctx.config):
        argv += ["--location", "dpu", "--config", str(ctx.args.config)]
    # location=="dpu" makes ovs_offload_checker.py run up to
    # 4 + 2*len(interfaces) *sequential* real SSH round trips to the DPU
    # (ovs-vsctl show, dpctl/show, dpctl/dump-flows, ovs-ofctl dump-flows,
    # plus tc -s filter show + ethtool -S per interface -- see
    # _dpu_probe_section() in ovs_offload_checker.py). A fixed 45s budget
    # is nowhere near enough once each round trip pays the DPU's own
    # sudo hostname-resolution stall (docs/operations/dpu-remote-execution.md);
    # scale with dpu.command_timeout_seconds like discover-representors does.
    if is_dpu_mode(ctx.config):
        dpu = ctx.config.get("dpu") or {}
        per_command_timeout = float(dpu.get("command_timeout_seconds") or 30)
        probe_count = 4 + 2 * len(interfaces)
        subprocess_timeout = per_command_timeout * probe_count + 20.0
    else:
        subprocess_timeout = 45.0
    cmd = _run_tool(
        argv,
        reason="labctl validate-offload: ovs_offload_checker.py",
        timeout=subprocess_timeout,
    )
    checker_report = _parse_tool_json(cmd)
    return {
        "stage": "validate-offload",
        "backend_result": _jsonable(backend_result),
        "forwarding": {
            "name": intent.get("name"),
            "endpoints": endpoints,
            "vf_pci_addresses": vf_pci_addresses,
            "representors": representors,
            "bridge": bridge,
            "configured_ports": interfaces,
            "configured_flows": result.get("configured_flows"),
            "offload_requested": result.get("offload_requested"),
            "offload_configured": result.get("offload_configured"),
        },
        "offload_checker_report": checker_report,
        "offload_checker_error": None if checker_report is not None else (cmd.stderr or cmd.reason),
    }


def stage_collect_evidence(ctx: StageContext) -> Dict[str, Any]:
    validation = ctx.config.get("validation") or {}
    evidence: Dict[str, Any] = {}
    if validation.get("collect_vendor_telemetry"):
        evidence["telemetry"] = _jsonable(ctx.backend.collect_telemetry())
    if validation.get("collect_ovs_flows") or validation.get("collect_tc_filters") or validation.get(
        "collect_ethtool_stats"
    ):
        offload_prior = ctx.load_prior("validate-offload")
        evidence["offload_checker_report"] = (offload_prior or {}).get("offload_checker_report")
    if validation.get("collect_cpu_stats"):
        cmd = _run_tool(["cat", "/proc/stat"], reason="labctl collect-evidence: /proc/stat")
        evidence["cpu_stats"] = cmd.stdout if cmd.executed else None
        evidence["cpu_stats_error"] = None if cmd.executed else (cmd.stderr or cmd.reason)
    return {"stage": "collect-evidence", "evidence": evidence, "requested": validation}


def stage_cleanup(ctx: StageContext) -> Dict[str, Any]:
    """Thin delegate. Full teardown lives in tools/lab_cleanup.py.

    Kept as its own labctl stage so `run-all-safe` and single-command
    docs stay accurate; see docs/operations/cleanup-and-rollback.md.
    """
    lab_cleanup_path = ROOT / "tools" / "lab_cleanup.py"
    if not lab_cleanup_path.exists():
        backend_result = ctx.backend.cleanup()
        return {
            "stage": "cleanup",
            "message": "tools/lab_cleanup.py not present yet; called backend.cleanup() only",
            "backend_result": _jsonable(backend_result),
        }
    argv = [sys.executable, str(lab_cleanup_path), "--config", ctx.args.config]
    argv.append("--apply" if ctx.apply_allowed else "--dry-run")
    argv += ["--run-id", ctx.run_id]
    cmd = run(argv, dry_run=False, timeout=120.0, reason="labctl cleanup: tools/lab_cleanup.py")
    report = _parse_tool_json(cmd)
    backend_result = ctx.backend.cleanup()
    return {
        "stage": "cleanup",
        "lab_cleanup_report": report,
        "backend_result": _jsonable(backend_result),
        "lab_cleanup_stdout": None if report is not None else cmd.stdout,
        "lab_cleanup_stderr": cmd.stderr,
    }


STAGE_FUNCS = {
    "discover": stage_discover,
    "validate-config": stage_validate_config,
    "prepare-host": stage_prepare_host,
    "prepare-vfs": stage_prepare_vfs,
    "prepare-switchdev": stage_prepare_switchdev,
    "discover-representors": stage_discover_representors,
    "prepare-ovs": stage_prepare_ovs,
    "create-vms": stage_create_vms,
    "verify-vms": stage_verify_vms,
    "attach-vfs": stage_attach_vfs,
    "configure-forwarding": stage_configure_forwarding,
    "configure-vm-ips": stage_configure_vm_ips,
    "run-traffic": stage_run_traffic,
    "validate-offload": stage_validate_offload,
    "collect-evidence": stage_collect_evidence,
    "cleanup": stage_cleanup,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--config", required=True, help="VM2VM lab YAML (see config/README-lab-vm2vm.md).")
    shared.add_argument("--dry-run", action="store_true", help="Read-only (default). Wins over --apply.")
    shared.add_argument("--apply", action="store_true", help="Allow destructive stages (also needs config safety.dry_run_default: false and the apply readiness gate).")
    shared.add_argument(
        "--force",
        action="store_true",
        help="Skip the dry-run-plan check for cleanup only. Refused for forwarding/OVS setup.",
    )
    shared.add_argument("--json", action="store_true", help="Print JSON.")
    shared.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging.")
    shared.add_argument("--run-id", help="Reuse a specific run id under tests/results/<vendor>/. Default: reuse the vendor's latest run, or start a new one.")

    parser = argparse.ArgumentParser(
        prog="labctl",
        description="VM-to-VM single-DPU lab orchestrator. Default: dry-run, no mutations.",
    )
    sub = parser.add_subparsers(dest="domain", required=True)
    vm2vm = sub.add_parser("vm2vm", help="VM-to-VM single-DPU lab workflow")
    vm2vm_sub = vm2vm.add_subparsers(dest="stage", required=True)
    for stage in (*STAGES, "run-all-safe"):
        vm2vm_sub.add_parser(stage, parents=[shared], help="labctl vm2vm %s" % stage)

    import amd_discovery_workflow
    import intel_nf_workflow

    intel_nf = sub.add_parser("intel-nf", help="Intel network-function-offload first-run path (not VM-to-VM)")
    intel_sub = intel_nf.add_subparsers(dest="stage", required=True)
    for stage in intel_nf_workflow.INTEL_NF_STAGES:
        intel_sub.add_parser(stage, parents=[shared], help="labctl intel-nf %s" % stage)

    amd_disc = sub.add_parser("amd-discovery", help="AMD Pensando discovery-first workflow")
    amd_sub = amd_disc.add_subparsers(dest="stage", required=True)
    for stage in amd_discovery_workflow.AMD_DISCOVERY_STAGES:
        amd_sub.add_parser(stage, parents=[shared], help="labctl amd-discovery %s" % stage)
    return parser


def _run_alt_domain(args: argparse.Namespace, config: Dict[str, Any]) -> int:
    import amd_discovery_workflow
    import intel_nf_workflow

    dry = _dry_run(args, config)
    apply = _apply_allowed(args, config)
    if args.domain == "intel-nf":
        vendor_type = "intel-ipu"
        result = intel_nf_workflow.run_stage(args.stage, config, apply=apply, dry_run=dry)
    else:
        vendor_type = "amd-pensando"
        result = amd_discovery_workflow.run_stage(args.stage, config, apply=apply, dry_run=dry)
    run_id = resolve_run_id(vendor_type, args.run_id)
    run_dir = run_dir_for(vendor_type, run_id)
    lab_state.write_run_metadata(
        run_dir,
        run_id=run_id,
        vendor=vendor_type,
        config_path=str(args.config),
        config_hash_value=lab_state.config_hash(config),
        dry_run=dry,
        apply=apply,
    )
    stage_name = args.stage
    if args.domain == "amd-discovery":
        fname = amd_discovery_workflow.STAGE_FILES.get(stage_name)
        if fname:
            lab_state.atomic_write_json(run_dir / fname.replace(".md", ".json"), result)
            if fname.endswith(".md") and result.get("markdown"):
                (run_dir / fname).write_text(result["markdown"], encoding="utf-8")
        else:
            save_stage_json(run_dir, stage_name, result, dry_run=dry, apply=apply)
    else:
        save_stage_json(run_dir, "intel-nf-%s" % stage_name, result, dry_run=dry, apply=apply)
    envelope = {
        "cli": "odse-labctl/v1alpha1",
        "domain": args.domain,
        "stage": args.stage,
        "vendor": vendor_type,
        "run_id": run_id,
        "dry_run": dry,
        "apply": apply,
        "result": result,
    }
    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True, default=str))
    else:
        _print_text(args.stage, envelope)
    return 0


def _print_text(stage: str, envelope: Dict[str, Any]) -> None:
    print("stage: %s" % stage)
    print("dry_run: %s  apply: %s" % (envelope.get("dry_run"), envelope.get("apply")))
    print("run_id: %s" % envelope.get("run_id"))
    result = envelope.get("result") or {}
    if isinstance(result, dict):
        for key in ("valid", "success", "skipped", "message", "error"):
            if key in result:
                print("%s: %s" % (key, result[key]))


def _run_single_stage(
    stage: str,
    args: argparse.Namespace,
    config: Dict[str, Any],
    vendor_type: str,
    backend: Any,
    run_dir: Path,
    run_id: str,
) -> Dict[str, Any]:
    ctx = StageContext(args, config, vendor_type, backend, run_dir, run_id)
    cfg_hash = lab_state.config_hash(config)
    missing = lab_state.missing_dependencies(run_dir, stage, hard=True)
    if missing:
        result = lab_state.dependency_error(stage, missing)
        save_stage_json(
            run_dir,
            stage,
            result,
            config_hash_value=cfg_hash,
            dry_run=ctx.dry_run,
            apply=ctx.apply_allowed,
        )
        return {
            "stage": stage,
            "dry_run": ctx.dry_run,
            "apply": ctx.apply_allowed,
            "run_id": run_id,
            "result": result,
            "error": lab_state.STAGE_MISSING_DEPENDENCY,
        }
    if apply_readiness_gate.should_evaluate(stage, ctx.apply_allowed):
        gate = apply_readiness_gate.evaluate_apply_readiness(
            stage=stage,
            config=config,
            run_dir=run_dir,
            apply_requested=bool(
                getattr(args, "apply", False) and not getattr(args, "dry_run", False)
            ),
            force=bool(getattr(args, "force", False)),
        )
        if not gate["ok"]:
            result = apply_readiness_gate.gate_error_payload(stage, gate)
            # Do not overwrite a prior dry-run plan for this stage.
            lab_state.save_stage(
                run_dir,
                "apply-readiness-gate",
                result,
                config_hash_value=cfg_hash,
                dry_run=ctx.dry_run,
                apply=ctx.apply_allowed,
            )
            return {
                "stage": stage,
                "dry_run": ctx.dry_run,
                "apply": ctx.apply_allowed,
                "run_id": run_id,
                "result": result,
                "error": apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE,
            }
    if stage in DESTRUCTIVE_STAGES and not ctx.apply_allowed:
        LOG.warning("destructive stage %s held at dry-run", stage)
    func = STAGE_FUNCS[stage]
    try:
        result = func(ctx)
    except Exception as exc:  # stages must not crash the whole run
        LOG.exception("stage %s raised an unexpected error", stage)
        result = {"stage": stage, "error": "unexpected exception: %s" % exc}
    save_stage_json(
        run_dir,
        stage,
        result,
        config_hash_value=cfg_hash,
        dry_run=ctx.dry_run,
        apply=ctx.apply_allowed,
    )
    return {
        "stage": stage,
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
        "run_id": run_id,
        "result": result,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )

    try:
        config = load_vm2vm_config(args.config)
    except (OSError, ValueError) as exc:
        LOG.error("failed to load --config %s: %s", args.config, exc)
        return 1

    if args.domain in ("intel-nf", "amd-discovery"):
        return _run_alt_domain(args, config)

    if args.stage != "validate-config":
        issues = validate_vm2vm_config(config)
        if issues:
            LOG.error("config is invalid; run 'labctl vm2vm validate-config' for details: %s", issues)
            return 1

    try:
        backend, vendor_type = _backend_for(config)
    except UnknownBackend as exc:
        LOG.error("%s", exc)
        return 1

    run_id = resolve_run_id(vendor_type, args.run_id)
    run_dir = run_dir_for(vendor_type, run_id)
    lab_state.write_run_metadata(
        run_dir,
        run_id=run_id,
        vendor=vendor_type,
        config_path=str(args.config),
        config_hash_value=lab_state.config_hash(config),
        dry_run=_dry_run(args, config),
        apply=_apply_allowed(args, config),
    )

    if args.stage == "run-all-safe":
        LOG.info("run-all-safe: forcing dry-run for every stage regardless of --apply")
        args.apply = False
        args.dry_run = True
        outputs = []
        for stage in RUN_ALL_SAFE_ORDER:
            outputs.append(_run_single_stage(stage, args, config, vendor_type, backend, run_dir, run_id))
        mark_latest(vendor_type, run_id)
        envelope: Dict[str, Any] = {
            "cli": "odse-labctl/v1alpha1",
            "stage": "run-all-safe",
            "run_id": run_id,
            "vendor": vendor_type,
            "stages": outputs,
        }
        missing = any(
            s.get("error")
            in (
                lab_state.STAGE_MISSING_DEPENDENCY,
                apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE,
            )
            for s in outputs
        )
    else:
        LOG.info("stage plan: %s", args.stage)
        single = _run_single_stage(args.stage, args, config, vendor_type, backend, run_dir, run_id)
        mark_latest(vendor_type, run_id)
        envelope = {"cli": "odse-labctl/v1alpha1", "vendor": vendor_type, **single}
        missing = envelope.get("error") in (
            lab_state.STAGE_MISSING_DEPENDENCY,
            apply_readiness_gate.APPLY_BLOCKED_BY_READINESS_GATE,
        )

    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True, default=str))
    else:
        _print_text(args.stage, envelope)
    return 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
