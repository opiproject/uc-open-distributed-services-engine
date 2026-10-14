#!/usr/bin/env python3
"""preflight_readiness.py — read-only audit before a VM-to-VM DPU/IPU lab run.

Does not execute destructive commands (no eswitch set, no sriov_numvfs,
no virsh define, no SSH). It inspects the lab YAML, repository files,
and whether expected host tools *exist*.

Usage:
    python tools/preflight_readiness.py --config config/lab-nvidia-vm2vm.example.yaml --json
    python tools/preflight_readiness.py --config config/lab-nvidia-vm2vm.example.yaml --markdown
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.commands import command_exists  # noqa: E402
from plugins.base.lab_config import normalize_vendor  # noqa: E402
from plugins.base.vm2vm_config import (  # noqa: E402
    NVIDIA_DPU_PROFILE,
    load_vm2vm_config,
    preserved_interfaces,
    validate_forwarding_section,
    validate_nvidia_dpu_mode,
    validate_vm2vm_config,
)

LOG = logging.getLogger("preflight_readiness")

STATUS_READY = "READY"
STATUS_DRY_RUN_ONLY = "READY_FOR_DRY_RUN_ONLY"
STATUS_NOT_READY = "NOT_READY"

NVIDIA_DPU_PROFILE = "nvidia-bluefield-dpu-mode"


@dataclass
class CheckResult:
    category: str
    ok: bool
    blocking: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    readiness_status: str
    blocking_issues: List[str]
    warnings: List[str]
    recommended_next_actions: List[str]
    checked_files: List[str]
    checked_commands: List[str]
    checks: List[Dict[str, Any]]
    config_path: Optional[str] = None
    vendor: Optional[str] = None
    dry_run: bool = True
    note: str = (
        "Read-only audit. true/ok only when proven. Missing DPU-mode or "
        "remote execution is not treated as READY for --apply."
    )


def _placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text.startswith("<") and text.endswith(">")


def _exists(rel: str) -> bool:
    return (ROOT / rel).is_file()


def check_lab_config(config: Optional[Dict[str, Any]], path: Optional[Path]) -> CheckResult:
    if config is None:
        return CheckResult(
            "lab_config_validity",
            False,
            True,
            "config could not be loaded",
            {"path": str(path) if path else None},
        )
    issues = validate_vm2vm_config(config)
    issues.extend(validate_forwarding_section(config))
    placeholders = []
    dpu = config.get("dpu") or {}
    host = config.get("host") or {}
    for key, val in (
        ("host.name", host.get("name")),
        ("dpu.pf_name", dpu.get("pf_name")),
        ("dpu.pf_pci", dpu.get("pf_pci")),
    ):
        if _placeholder(val):
            placeholders.append(key)
    ok = not issues
    msg = "config valid" if ok else "; ".join(issues)
    if placeholders:
        msg = (msg + "; " if msg != "config valid" else "") + (
            "placeholder fields remain: %s (ok for dry-run, not for --apply)"
            % ", ".join(placeholders)
        )
    return CheckResult(
        "lab_config_validity",
        ok,
        bool(issues),
        msg or "config valid",
        {"issues": issues, "placeholders": placeholders},
    )


def check_vendor_profile(config: Dict[str, Any]) -> CheckResult:
    vendor = config.get("vendor") or {}
    vtype = normalize_vendor(vendor.get("type"))
    profile = vendor.get("profile")
    known = {
        "nvidia-bluefield",
        "intel-ipu",
        "marvell-octeon",
        "amd-pensando",
    }
    if vtype not in known:
        return CheckResult(
            "vendor_profile_validity",
            False,
            True,
            "unknown vendor.type %r" % vendor.get("type"),
            {"vendor": vendor},
        )
    return CheckResult(
        "vendor_profile_validity",
        True,
        False,
        "vendor.type=%s profile=%s" % (vtype, profile),
        {"vendor_type": vtype, "profile": profile},
    )


def nvidia_dpu_mode_ok(config: Dict[str, Any]) -> List[str]:
    return validate_nvidia_dpu_mode(config)


def check_nvidia_dpu_pinning(config: Dict[str, Any]) -> CheckResult:
    vendor = normalize_vendor((config.get("vendor") or {}).get("type"))
    if vendor != "nvidia-bluefield":
        return CheckResult(
            "nvidia_bluefield_dpu_mode_pinning",
            True,
            False,
            "not NVIDIA; DPU-mode pinning N/A",
            {"vendor": vendor},
        )
    errors = list(nvidia_dpu_mode_ok(config))
    profile_file = _exists("config/profiles/nvidia-bluefield-dpu-mode.yaml")
    if not profile_file:
        errors.append("missing config/profiles/nvidia-bluefield-dpu-mode.yaml")
    if errors:
        return CheckResult(
            "nvidia_bluefield_dpu_mode_pinning",
            False,
            True,
            "Invalid NVIDIA DPU-mode configuration: this profile requires "
            "DPU-side OVS/OVS-DOCA ownership. Host OVS must not own VF "
            "representors for forwarding. " + "; ".join(errors),
            {"errors": errors, "profile_file": profile_file},
        )
    return CheckResult(
        "nvidia_bluefield_dpu_mode_pinning",
        True,
        False,
        "NVIDIA profile pinned to DPU-side OVS/OVS-DOCA",
        {"profile": NVIDIA_DPU_PROFILE},
    )


def check_management_fields(config: Dict[str, Any]) -> CheckResult:
    dpu = config.get("dpu") or {}
    remote = config.get("remote_execution") or {}
    has_ip = bool(dpu.get("management_ip")) and not _placeholder(dpu.get("management_ip"))
    ssh_user = dpu.get("ssh_user")
    details = {
        "management_ip": dpu.get("management_ip"),
        "ssh_user": ssh_user,
        "ssh_port": dpu.get("ssh_port"),
        "remote_execution": bool(remote.get("enabled")),
        "placeholder_ip": _placeholder(dpu.get("management_ip")),
    }
    vendor = normalize_vendor((config.get("vendor") or {}).get("type"))
    if vendor == "nvidia-bluefield":
        ok = has_ip
        blocking = not has_ip
        msg = (
            "DPU management_ip present"
            if has_ip
            else "dpu.management_ip missing or placeholder; DPU-mode needs a real Arm BMC/OS address"
        )
        return CheckResult("dpu_management_reachability_fields", ok, blocking, msg, details)
    return CheckResult(
        "dpu_management_reachability_fields",
        True,
        False,
        "management_ip optional for this vendor until a remote path is required",
        details,
    )


def check_dpu_remote_execution() -> CheckResult:
    remote_mod = _exists("plugins/base/remote.py")
    remote_tool = _exists("tools/remote_exec.py")
    docs = _exists("docs/operations/dpu-remote-execution.md")
    ok = remote_mod and remote_tool
    return CheckResult(
        "dpu_side_command_execution_availability",
        ok,
        not ok,
        "RemoteCommandRunner present" if ok else "DPU remote execution not implemented (R2)",
        {"plugins/base/remote.py": remote_mod, "tools/remote_exec.py": remote_tool, "docs": docs},
    )


def check_dpu_ovs_validation() -> CheckResult:
    dpu_ovs = _exists("plugins/nvidia_bluefield/dpu_ovs.py")
    docs = _exists("docs/operations/nvidia-dpu-side-ovs-validation.md")
    host_ovs = _exists("plugins/nvidia_bluefield/ovs.py")
    ok = dpu_ovs
    return CheckResult(
        "dpu_side_ovs_ovs_doca_validation_support",
        ok,
        not ok,
        "DPU-side OVS validator present" if ok else "DPU-side OVS validation not implemented (R3)",
        {
            "plugins/nvidia_bluefield/dpu_ovs.py": dpu_ovs,
            "plugins/nvidia_bluefield/ovs.py": host_ovs,
            "docs": docs,
        },
    )


def check_vm_libvirt(config: Dict[str, Any]) -> CheckResult:
    template = _exists("qemu/libvirt-xml/vm-vfio-template.xml")
    prepare = _exists("qemu/images/prepare-vm-image.sh")
    virsh = command_exists("virsh")
    qemu = command_exists("qemu-system-x86_64") or command_exists("qemu-kvm")
    vms = config.get("vms") or []
    images = [vm.get("image") for vm in vms if isinstance(vm, dict)]
    image_placeholders = [i for i in images if _placeholder(i)]
    missing_images = [
        i for i in images if i and not _placeholder(i) and not Path(str(i)).exists()
    ]
    ok = template and prepare
    blocking = not template
    msg = "libvirt template and image helper present"
    if not virsh:
        msg += "; virsh not on PATH (dry-run still ok)"
    if image_placeholders:
        msg += "; VM image paths are placeholders"
    return CheckResult(
        "vm_image_and_libvirt_readiness",
        ok,
        blocking,
        msg,
        {
            "template": template,
            "prepare_script": prepare,
            "virsh": virsh,
            "qemu": qemu,
            "image_placeholders": image_placeholders,
            "missing_images": missing_images,
        },
    )


def check_vf_representor_mapping() -> CheckResult:
    tool = _exists("tools/topology-discovery/vf_to_representor_map.py")
    docs = _exists("docs/operations/vf-to-representor-mapping.md")
    dpu_docs = _exists("docs/operations/dpu-side-vf-to-representor-mapping.md")
    return CheckResult(
        "vf_to_representor_mapping_support",
        tool and dpu_docs,
        not tool,
        "host mapper and DPU-side nvidia-portname mapping present",
        {"tool": tool, "host_docs": docs, "dpu_docs": dpu_docs},
    )


def check_stage_state() -> CheckResult:
    labctl = _exists("tools/labctl.py")
    lab_state = _exists("tools/lab_state.py")
    return CheckResult(
        "stage_output_state_passing",
        labctl and lab_state,
        not labctl,
        "labctl stage JSON and lab_state contract present",
        {"labctl": labctl, "lab_state": lab_state},
    )


def check_cleanup() -> CheckResult:
    cleanup = _exists("tools/lab_cleanup.py")
    docs = _exists("docs/operations/cleanup-and-rollback.md")
    dpu_aware = False
    if cleanup:
        text = (ROOT / "tools/lab_cleanup.py").read_text(encoding="utf-8")
        dpu_aware = any(
            token in text
            for token in (
                "is_dpu_mode",
                "host_ovs_untouched",
                "plan_dpu_ovs_cleanup",
                "dpu_mode",
            )
        )
    return CheckResult(
        "cleanup_coverage",
        cleanup and dpu_aware,
        not cleanup,
        (
            "host cleanup present; DPU-side OVS cleanup planned for lab-owned objects"
            if dpu_aware
            else "host cleanup present; DPU-side OVS cleanup is R7"
        ),
        {"lab_cleanup": cleanup, "docs": docs, "dpu_side_cleanup": dpu_aware},
    )


def check_evidence() -> CheckResult:
    report = _exists("tools/report_generator.py")
    checker = _exists("tools/offload-checker/ovs_offload_checker.py")
    return CheckResult(
        "evidence_report_generation_coverage",
        report and checker,
        not report,
        "report_generator and offload-checker present",
        {"report_generator": report, "offload_checker": checker},
    )


def check_management_safety(config: Dict[str, Any]) -> CheckResult:
    safety = config.get("safety") or {}
    preserved = preserved_interfaces(config)
    forbid = safety.get("forbid_management_interface_changes") is True
    require_apply = safety.get("require_apply") is True
    dry = safety.get("dry_run_default") is True
    ok = forbid and require_apply
    return CheckResult(
        "management_interface_safety",
        ok,
        not ok,
        "management interface protected" if ok else "safety gates missing",
        {
            "forbid_management_interface_changes": forbid,
            "require_apply": require_apply,
            "dry_run_default": dry,
            "preserved_interfaces": sorted(preserved),
        },
    )


def check_vendor_specific_readiness(config: Dict[str, Any]) -> CheckResult:
    vendor = normalize_vendor((config.get("vendor") or {}).get("type"))
    if vendor == "marvell-octeon":
        from plugins.marvell_octeon.readiness import evaluate_marvell_readiness

        result = evaluate_marvell_readiness(config)
        status = (result.details or {}).get("readiness_status")
        blocking = status == "MARVELL_NOT_READY"
        ok = status in ("MARVELL_READY_FOR_DRY_RUN", "MARVELL_REPRESENTOR_DISCOVERY_REQUIRED")
        return CheckResult("marvell_dao_readiness", ok, blocking, str(status), result.details or {})
    if vendor == "intel-ipu":
        from plugins.intel_ipu.readiness import evaluate_intel_readiness

        result = evaluate_intel_readiness(config)
        status = (result.details or {}).get("readiness_status")
        blocking = status == "INTEL_NOT_READY"
        ok = status in (
            "INTEL_READY_FOR_OPI_DRY_RUN",
            "INTEL_VM_TO_VM_DISCOVERY_REQUIRED",
            "INTEL_OPI_ENDPOINT_REQUIRED",
        )
        return CheckResult("intel_opi_readiness", ok, blocking, str(status), result.details or {})
    if vendor == "amd-pensando":
        from plugins.amd_pensando.readiness import evaluate_amd_readiness

        result = evaluate_amd_readiness(config)
        status = (result.details or {}).get("readiness_status")
        blocking = status == "AMD_NOT_READY"
        ok = status != "AMD_NOT_READY"
        return CheckResult("amd_pensando_readiness", ok, blocking, str(status), result.details or {})
    return CheckResult(
        "vendor_specific_readiness",
        True,
        False,
        "no extra vendor gate (NVIDIA uses DPU-mode pinning above)",
        {},
    )


def audit(config: Dict[str, Any], config_path: Optional[Path] = None) -> PreflightReport:
    checked_files = [
        "tools/labctl.py",
        "tools/lab_cleanup.py",
        "tools/report_generator.py",
        "tools/topology-discovery/vf_to_representor_map.py",
        "plugins/base/remote.py",
        "plugins/nvidia_bluefield/dpu_ovs.py",
        "qemu/libvirt-xml/vm-vfio-template.xml",
        "config/profiles/nvidia-bluefield-dpu-mode.yaml",
        "docs/operations/dpu-remote-execution.md",
    ]
    checked_commands = ["virsh", "qemu-system-x86_64", "qemu-kvm", "ovs-vsctl", "ssh"]
    for name in checked_commands:
        command_exists(name)

    checks = [
        check_lab_config(config, config_path),
        check_vendor_profile(config),
        check_nvidia_dpu_pinning(config),
        check_management_fields(config),
        check_dpu_remote_execution(),
        check_dpu_ovs_validation(),
        check_vm_libvirt(config),
        check_vf_representor_mapping(),
        check_stage_state(),
        check_cleanup(),
        check_evidence(),
        check_management_safety(config),
        check_vendor_specific_readiness(config),
    ]

    blocking = [c.message for c in checks if c.blocking and not c.ok]
    warnings = [c.message for c in checks if (not c.ok) and not c.blocking]
    for c in checks:
        if c.ok and "placeholder" in c.message.lower():
            warnings.append(c.message)
        details = c.details or {}
        if details.get("placeholders"):
            warnings.append("placeholders: %s" % details["placeholders"])
        if details.get("dpu_docs") is False:
            warnings.append("DPU-side representor mapping not documented yet")
        if details.get("lab_state") is False:
            warnings.append("tools/lab_state.py not present yet")
        if details.get("dpu_side_cleanup") is False and details.get("lab_cleanup"):
            warnings.append("cleanup does not yet target DPU-side OVS")

    # de-dupe warnings
    seen = set()
    uniq_warn = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            uniq_warn.append(w)

    actions: List[str] = []
    vendor = normalize_vendor((config.get("vendor") or {}).get("type"))
    if any("DPU-mode" in m or "nvidia.mode" in m or "profile must be" in m for m in blocking):
        actions.append("Pin NVIDIA to nvidia-bluefield-dpu-mode (readiness prompt R1).")
    if any("remote execution" in m.lower() for m in blocking):
        actions.append("Add DPU RemoteCommandRunner (R2) before --apply.")
    if any("OVS validation not implemented" in m for m in blocking):
        actions.append("Add DPU-side OVS/OVS-DOCA validation (R3).")
    if any(c.details.get("placeholders") for c in checks):
        actions.append("Copy the example YAML to a gitignored lab file and replace <PLACEHOLDERS> from discovery.")
    if (config.get("safety") or {}).get("dry_run_default") is True:
        actions.append("Keep dry_run_default true until a written change window; do not --apply yet.")
    if not actions:
        actions.append("Run labctl vm2vm validate-config then discover in dry-run.")

    config_ok = checks[0].ok and checks[-1].ok
    apply_blockers = [c for c in checks if c.blocking and not c.ok]
    if not config_ok or any(
        c.category in ("lab_config_validity", "management_interface_safety", "vendor_profile_validity")
        and not c.ok
        for c in checks
    ):
        status = STATUS_NOT_READY
    elif apply_blockers:
        status = STATUS_DRY_RUN_ONLY
    elif (config.get("safety") or {}).get("dry_run_default") is True:
        status = STATUS_DRY_RUN_ONLY
    else:
        status = STATUS_READY

    return PreflightReport(
        readiness_status=status,
        blocking_issues=blocking,
        warnings=uniq_warn,
        recommended_next_actions=actions,
        checked_files=checked_files,
        checked_commands=checked_commands,
        checks=[asdict(c) for c in checks],
        config_path=str(config_path) if config_path else None,
        vendor=vendor,
        dry_run=True,
    )


def render_markdown(report: PreflightReport) -> str:
    lines = [
        "# Preflight readiness",
        "",
        "- status: `%s`" % report.readiness_status,
        "- vendor: `%s`" % (report.vendor or "null"),
        "- config: `%s`" % (report.config_path or "null"),
        "- dry_run: `%s`" % report.dry_run,
        "",
        report.note,
        "",
        "## Checks",
        "",
        "| category | ok | blocking | message |",
        "|---|---|---|---|",
    ]
    for c in report.checks:
        lines.append(
            "| %s | %s | %s | %s |"
            % (c["category"], c["ok"], c["blocking"], str(c["message"]).replace("|", "/"))
        )
    lines.extend(["", "## Blocking issues", ""])
    if report.blocking_issues:
        for item in report.blocking_issues:
            lines.append("- %s" % item)
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if report.warnings:
        for item in report.warnings:
            lines.append("- %s" % item)
    else:
        lines.append("- none")
    lines.extend(["", "## Recommended next actions", ""])
    for item in report.recommended_next_actions:
        lines.append("- %s" % item)
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preflight_readiness",
        description="Read-only readiness audit before a VM-to-VM DPU lab run.",
    )
    parser.add_argument("--config", required=True, help="odse-lab-vm2vm YAML")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    path = Path(args.config)
    try:
        config = load_vm2vm_config(path)
    except (OSError, ValueError) as exc:
        LOG.error("failed to load config: %s", exc)
        report = PreflightReport(
            readiness_status=STATUS_NOT_READY,
            blocking_issues=[str(exc)],
            warnings=[],
            recommended_next_actions=["Fix --config path and YAML syntax."],
            checked_files=[],
            checked_commands=[],
            checks=[],
            config_path=str(path),
        )
        if args.json and not args.markdown:
            sys.stdout.write(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(render_markdown(report))
        return 1
    report = audit(config, path)
    if args.json and not args.markdown:
        sys.stdout.write(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_markdown(report))
    if report.readiness_status == STATUS_NOT_READY:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
