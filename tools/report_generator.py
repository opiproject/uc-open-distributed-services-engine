#!/usr/bin/env python3
"""report_generator.py — build a standardized result report for one
VM-to-VM single-DPU lab run (see docs/test-plan/result-report-template.md).

Reads the JSON files `tools/labctl.py` writes under one run directory
(`tests/results/<vendor>/<run-id>/`) and renders them into a single
human-readable report plus a machine-readable JSON twin. Never invents
data: any stage file that is missing, unparsable, or lacks a field is
rendered as "not available" (or `null` in JSON), not as a guess.

Usage:
    python3 tools/report_generator.py --input-dir tests/results/nvidia-bluefield/<run-id>/
    python3 tools/report_generator.py --input-dir tests/results/nvidia-bluefield/<run-id>/ \\
        --output result-summary.md

Writes:
    <output>              (default: <input-dir>/result-summary.md)
    <output with .json>   (default: <input-dir>/result-summary.json)

See also:
    docs/test-plan/result-report-template.md — the section-by-section spec
    docs/test-plan/forwarding-validation.md  — the five claims this report
        must keep separate (forwarding configured / traffic works / offload
        requested / offload probably active / offload confirmed)
    docs/test-plan/offload-validation.md     — how to read the offload
        checker evidence this report quotes
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import lab_state  # noqa: E402

SCHEMA_VERSION = "odse-result-report/v1alpha1"

VERDICTS = (
    "PASS_FUNCTIONAL_ONLY",
    "PASS_OFFLOAD_PROBABLE",
    "PASS_OFFLOAD_CONFIRMED",
    "FAIL_CONNECTIVITY",
    "FAIL_SETUP",
    "INCONCLUSIVE",
)

# labctl.py's save_stage_json() naming: "<stage>.json" with '-' -> '_'.
_STAGE_FILES = {
    "discover": "discover.json",
    "validate_config": "validate_config.json",
    "prepare_host": "prepare_host.json",
    "prepare_vfs": "prepare_vfs.json",
    "prepare_switchdev": "prepare_switchdev.json",
    "discover_representors": "discover_representors.json",
    "prepare_ovs": "prepare_ovs.json",
    "create_vms": "create_vms.json",
    "attach_vfs": "attach_vfs.json",
    "configure_forwarding": "configure_forwarding.json",
    "configure_vm_ips": "configure_vm_ips.json",
    "run_traffic": "run_traffic.json",
    "validate_offload": "validate_offload.json",
    "collect_evidence": "collect_evidence.json",
    "cleanup": "cleanup.json",
}


# ---------------------------------------------------------------------------
# Small, defensive accessors -- every helper below returns None instead of
# raising or guessing when data is missing or the wrong shape.
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def g(d: Any, *keys: str, default: Any = None) -> Any:
    """Dig through nested dicts; return `default` at the first miss."""
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def load_stages(input_dir: Path) -> Dict[str, Optional[Dict[str, Any]]]:
    stages = lab_state.load_all_stages(input_dir)
    # Historical report keys (underscore) plus any leftover legacy filenames.
    for name, fname in _STAGE_FILES.items():
        if stages.get(name) is None:
            stages[name] = _load_json(input_dir / fname)
    # tools/lab_cleanup.py writes its own, richer evidence file independent
    # of labctl.py's thin cleanup.json delegate wrapper (see
    # docs/operations/cleanup-and-rollback.md); prefer it when present.
    stages["cleanup_summary"] = _load_json(input_dir / "cleanup" / "cleanup-summary.json")
    return stages


# ---------------------------------------------------------------------------
# Section builders. Each returns an ordered dict of field name -> value.
# `None` means "not available from the stage JSON we have" -- never a guess.
# ---------------------------------------------------------------------------

def section_test_metadata(stages: Dict[str, Any], input_dir: Path, run_id: Optional[str], vendor: Optional[str]) -> Dict[str, Any]:
    discover = stages.get("discover")
    return {
        "run_id": run_id or g(discover, "backend_result", "details", "run_id"),
        "vendor": vendor,
        "input_dir": str(input_dir),
        "report_generated_at": datetime.now(timezone.utc).isoformat(),
        "discover_timestamp": g(discover, "dpu_inventory", "timestamp"),
        "stages_present": sorted(name for name, data in stages.items() if data is not None and name != "cleanup_summary"),
        "stages_missing": sorted(name for name, data in stages.items() if data is None and name != "cleanup_summary"),
    }


def section_server_details(stages: Dict[str, Any]) -> Dict[str, Any]:
    host_info = g(stages.get("discover"), "dpu_inventory", "host") or {}
    config_view = g(stages.get("validate_config"), "config") or {}
    return {
        "host_name": config_view.get("host_name"),
        "management_interface": config_view.get("management_interface"),
        "hostname": host_info.get("hostname"),
        "platform": host_info.get("platform"),
    }


def section_vendor_dpu_details(stages: Dict[str, Any]) -> Dict[str, Any]:
    config_view = g(stages.get("validate_config"), "config") or {}
    vendor_hints = g(stages.get("discover"), "dpu_inventory", "vendor_hints") or []
    return {
        "vendor_type": config_view.get("vendor_type"),
        "pf_name": config_view.get("pf_name"),
        "pf_pci": config_view.get("pf_pci"),
        "num_vfs": config_view.get("num_vfs"),
        "preferred_datapath": config_view.get("preferred_datapath"),
        "vendor_hints_from_inventory": vendor_hints,
    }


def _matching_netdev(stages: Dict[str, Any], pf_name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not pf_name:
        return None
    interfaces = g(stages.get("discover"), "dpu_inventory", "netdevs", "interfaces") or []
    for iface in interfaces:
        if isinstance(iface, dict) and iface.get("name") == pf_name:
            return iface
    return None


def section_firmware_driver_details(stages: Dict[str, Any]) -> Dict[str, Any]:
    config_view = g(stages.get("validate_config"), "config") or {}
    pf_name = config_view.get("pf_name")
    netdev = _matching_netdev(stages, pf_name) or {}
    fields: Dict[str, Any] = {
        "pf_name": pf_name,
        "driver": netdev.get("driver"),
        "firmware_version": netdev.get("firmware_version") or netdev.get("firmware-version"),
        "bus_info": netdev.get("bus_info"),
    }
    if not netdev:
        fields["note"] = (
            "no netdev entry named %r in discover.dpu_inventory.netdevs.interfaces "
            "(pf_name not set, discover stage not run, or PF not visible on this host)" % pf_name
        )
    return fields


def section_kernel_os_details(stages: Dict[str, Any]) -> Dict[str, Any]:
    host_info = g(stages.get("discover"), "dpu_inventory", "host") or {}
    return {
        "system": host_info.get("system"),
        "kernel_release": host_info.get("release"),
        "machine": host_info.get("machine"),
        "platform": host_info.get("platform"),
    }


def section_pf_vf_representor_mapping(stages: Dict[str, Any]) -> Dict[str, Any]:
    mapping = stages.get("discover_representors")
    mappings = g(mapping, "vf_to_representor_map", "mappings") or []
    return {
        "mapping_method_seen": sorted({m.get("mapping_method") for m in mappings if isinstance(m, dict) and m.get("mapping_method")}),
        "mappings": [
            {
                "vf_index": m.get("vf_index"),
                "vf_pci": m.get("vf_pci"),
                "representor_netdev": m.get("representor_netdev"),
                "mapping_method": m.get("mapping_method"),
                "no_representor_reason": m.get("no_representor_reason"),
            }
            for m in mappings
            if isinstance(m, dict)
        ],
        "error": g(mapping, "vf_to_representor_map_error"),
    }


def section_ovs_bridge_configuration(stages: Dict[str, Any]) -> Dict[str, Any]:
    validate_offload = stages.get("validate_offload")
    configure_forwarding = stages.get("configure_forwarding")
    prepare_ovs = stages.get("prepare_ovs")
    bridge = g(validate_offload, "forwarding", "bridge") or g(configure_forwarding, "intent", "bridge")
    return {
        "bridge": bridge,
        "configured_ports": g(validate_offload, "forwarding", "configured_ports")
        or g(configure_forwarding, "result", "configured_ports")
        or [],
        "prepare_ovs_backend_result": g(prepare_ovs, "backend_result"),
    }


def section_vm_definitions(stages: Dict[str, Any]) -> Dict[str, Any]:
    create_vms = g(stages.get("create_vms"), "vms") or []
    endpoints = g(stages.get("configure_forwarding"), "intent", "endpoints") or []
    endpoints_by_vm = {e.get("vm"): e for e in endpoints if isinstance(e, dict) and e.get("vm")}
    vms = []
    for vm in create_vms:
        if not isinstance(vm, dict):
            continue
        endpoint = endpoints_by_vm.get(vm.get("name"), {})
        vms.append(
            {
                "name": vm.get("name"),
                "vf_index": vm.get("vf_index"),
                "vf_pci": endpoint.get("vf_pci"),
                "representor": endpoint.get("representor"),
                "mac": endpoint.get("mac"),
                "created": vm.get("executed"),
                "error": vm.get("error"),
            }
        )
    return {"vms": vms}


def section_vm_interface_configuration(stages: Dict[str, Any]) -> Dict[str, Any]:
    attach_vfs = g(stages.get("attach_vfs"), "vms") or []
    configure_ips = g(stages.get("configure_vm_ips"), "vms") or []
    ips_by_name = {v.get("name"): v for v in configure_ips if isinstance(v, dict)}
    vms = []
    for entry in attach_vfs:
        if not isinstance(entry, dict):
            continue
        ip_entry = ips_by_name.get(entry.get("name"), {})
        vms.append(
            {
                "name": entry.get("name"),
                "vf_pci": entry.get("vf_pci"),
                "vf_attach_skipped": entry.get("skipped"),
                "vf_attach_message": entry.get("message"),
                "ip_cidr": ip_entry.get("ip_cidr"),
                "management_host": ip_entry.get("management_host"),
                "ip_configure_error": ip_entry.get("error"),
            }
        )
    return {"vms": vms}


def section_forwarding_configuration(stages: Dict[str, Any]) -> Dict[str, Any]:
    """Prompt J item 4: keep the five forwarding claims side by side.

    See docs/test-plan/forwarding-validation.md section 1 for why these
    fields are never collapsed into a single boolean.
    """
    configure_forwarding = stages.get("configure_forwarding")
    intent = g(configure_forwarding, "intent") or {}
    result = g(configure_forwarding, "result") or {}
    assessment = g(stages.get("validate_offload"), "offload_checker_report", "assessment") or {}

    offload_requested = result.get("offload_requested")
    if offload_requested is None:
        offload_requested = g(intent, "offload", "requested")
    offload_confirmed_flag = result.get("offload_confirmed")
    status = assessment.get("status")

    if offload_confirmed_flag is True and status == "probable_offloaded":
        confirmed_or_probable = "confirmed (vendor-corroborated; see backend evidence_files)"
    elif status in ("probable_offloaded", "probable_partial"):
        confirmed_or_probable = "probable (%s, confidence=%s) -- not confirmed" % (status, assessment.get("confidence"))
    elif status == "software_only":
        confirmed_or_probable = "not offloaded (software_only, confidence=%s)" % assessment.get("confidence")
    elif status == "unknown":
        confirmed_or_probable = "unknown (no dpctl/tc/ethtool evidence collected)"
    else:
        confirmed_or_probable = "not available (validate-offload stage not run)"

    return {
        "user_intent": {"name": intent.get("name"), "type": intent.get("type"), "mode": intent.get("mode")},
        "backend_selected": result.get("vendor"),
        "control_entity": result.get("control_entity"),
        "forwarding_entity": result.get("forwarding_entity"),
        "configured_ports": result.get("configured_ports") or [],
        "configured_flows": result.get("configured_flows") or [],
        "offload_requested": offload_requested,
        "offload_configured": result.get("offload_configured"),
        "offload_confirmed_or_probable": confirmed_or_probable,
        "result_success": result.get("success"),
        "result_message": result.get("message"),
    }


def _traffic_commands(stages: Dict[str, Any]) -> List[Dict[str, Any]]:
    commands = g(stages.get("run_traffic"), "commands") or []
    out = []
    for entry in commands:
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("command") or {}
        out.append(
            {
                "kind": entry.get("kind"),
                "argv": cmd.get("argv"),
                "executed": cmd.get("executed"),
                "returncode": cmd.get("returncode"),
                "dry_run": cmd.get("dry_run"),
                "stdout_tail": (cmd.get("stdout") or "")[-500:] or None,
                "stderr_tail": (cmd.get("stderr") or "")[-500:] or None,
            }
        )
    return out


def section_traffic_test_results(stages: Dict[str, Any]) -> Dict[str, Any]:
    run_traffic = stages.get("run_traffic")
    return {
        "error": g(run_traffic, "error"),
        "dry_run": g(run_traffic, "dry_run"),
        "commands": _traffic_commands(stages),
    }


def section_ovs_flow_evidence(stages: Dict[str, Any]) -> Dict[str, Any]:
    report = g(stages.get("validate_offload"), "offload_checker_report") or {}
    return {
        "ovs_vsctl_show": report.get("ovs_vsctl_show"),
        "dpctl_show": report.get("dpctl_show"),
        "dpctl_dump_flows": report.get("dpctl_dump_flows"),
        "ofctl_dump_flows": report.get("ofctl_dump_flows"),
    }


def section_tc_filter_evidence(stages: Dict[str, Any]) -> Dict[str, Any]:
    report = g(stages.get("validate_offload"), "offload_checker_report") or {}
    return {"tc_filters": report.get("tc_filters")}


def section_ethtool_counter_evidence(stages: Dict[str, Any]) -> Dict[str, Any]:
    report = g(stages.get("validate_offload"), "offload_checker_report") or {}
    return {"ethtool_stats": report.get("ethtool_stats"), "ip_link_stats": report.get("ip_link_stats")}


def section_cpu_usage_comparison(stages: Dict[str, Any]) -> Dict[str, Any]:
    report = g(stages.get("validate_offload"), "offload_checker_report") or {}
    return {
        "cpu_snapshot": report.get("cpu_snapshot"),
        "duration_section": report.get("duration"),
        "collect_evidence_cpu_stats": g(stages.get("collect_evidence"), "evidence", "cpu_stats"),
    }


def section_offload_confidence_assessment(stages: Dict[str, Any]) -> Dict[str, Any]:
    assessment = g(stages.get("validate_offload"), "offload_checker_report", "assessment") or {}
    return {
        "status": assessment.get("status"),
        "confidence": assessment.get("confidence"),
        "evidence": assessment.get("evidence") or [],
        "warnings": assessment.get("warnings") or [],
        "limitations": assessment.get("limitations") or [],
    }


def _collect_errors(stages: Dict[str, Any]) -> List[str]:
    """Best-effort scan for explicit error/failure markers across all stage
    JSON. Never inspects free-text stdout/stderr for "success" -- only
    fields the stages themselves already flag as problems.
    """
    found: List[str] = []

    def note(stage: str, msg: str) -> None:
        found.append("[%s] %s" % (stage, msg))

    vc = stages.get("validate_config")
    if vc and not vc.get("valid"):
        for issue in vc.get("issues") or []:
            note("validate-config", issue)

    for stage_name in ("prepare_vfs", "prepare_switchdev", "prepare_ovs", "configure_forwarding", "validate_offload"):
        data = stages.get(stage_name)
        backend_result = g(data, "backend_result") or g(data, "result")
        if isinstance(backend_result, dict) and backend_result.get("status") == "error":
            note(stage_name.replace("_", "-"), backend_result.get("message") or "backend_result status=error")
        if isinstance(data, dict) and data.get("errors"):
            for err in data["errors"]:
                note(stage_name.replace("_", "-"), str(err))

    for stage_name in ("create_vms", "attach_vfs", "configure_vm_ips"):
        data = stages.get(stage_name)
        for entry in g(data, "vms") or []:
            if isinstance(entry, dict) and entry.get("error"):
                note(stage_name.replace("_", "-"), "%s: %s" % (entry.get("name"), entry.get("error")))

    run_traffic = stages.get("run_traffic")
    if g(run_traffic, "error"):
        note("run-traffic", run_traffic["error"])
    for cmd in _traffic_commands(stages):
        if cmd.get("executed") and cmd.get("returncode") not in (None, 0):
            note("run-traffic", "%s exited %s" % (cmd.get("kind"), cmd.get("returncode")))

    offload_error = g(stages.get("validate_offload"), "offload_checker_error")
    if offload_error:
        note("validate-offload", "offload checker did not run: %s" % offload_error)

    mapping_error = g(stages.get("discover_representors"), "vf_to_representor_map_error")
    if mapping_error:
        note("discover-representors", mapping_error)

    cleanup_summary = stages.get("cleanup_summary")
    for name, entry in (g(cleanup_summary, "stages") or {}).items():
        if isinstance(entry, dict) and entry.get("error"):
            note("cleanup:%s" % name, entry["error"])

    return found


def section_failures_and_anomalies(stages: Dict[str, Any]) -> Dict[str, Any]:
    return {"errors": _collect_errors(stages)}


def section_cleanup_result(stages: Dict[str, Any]) -> Dict[str, Any]:
    summary = stages.get("cleanup_summary")
    cleanup_stage = stages.get("cleanup")
    if summary is None and cleanup_stage is None:
        return {"ran": False, "note": "no cleanup.json or cleanup/cleanup-summary.json in this run directory"}
    source = summary or g(cleanup_stage, "lab_cleanup_report") or {}
    stage_names = list((source.get("stages") or {}).keys()) if isinstance(source, dict) else []
    return {
        "ran": True,
        "dry_run": source.get("dry_run") if isinstance(source, dict) else None,
        "apply": source.get("apply") if isinstance(source, dict) else None,
        "stages_run": stage_names,
        "rollback_risks": source.get("rollback_risks") if isinstance(source, dict) else None,
    }


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def compute_verdict(stages: Dict[str, Any]) -> Dict[str, Any]:
    """Derive one of VERDICTS plus a short human-readable reason.

    Deliberately conservative: anything short of executed, successful
    traffic commands with real offload-checker evidence stays
    INCONCLUSIVE or FAIL_*, never a guessed PASS_OFFLOAD_* verdict.
    """
    validate_config = stages.get("validate_config")
    if validate_config is not None and not validate_config.get("valid"):
        return {"verdict": "FAIL_SETUP", "reason": "validate-config reported issues: %s" % validate_config.get("issues")}

    for stage_name in ("prepare_vfs", "prepare_switchdev", "prepare_ovs"):
        data = stages.get(stage_name)
        backend_result = g(data, "backend_result")
        if isinstance(backend_result, dict) and backend_result.get("status") == "error":
            return {
                "verdict": "FAIL_SETUP",
                "reason": "%s backend_result status=error: %s" % (stage_name.replace("_", "-"), backend_result.get("message")),
            }

    for stage_name in ("create_vms", "attach_vfs"):
        data = stages.get(stage_name)
        errors = [e for e in (g(data, "vms") or []) if isinstance(e, dict) and e.get("error")]
        if errors:
            return {
                "verdict": "FAIL_SETUP",
                "reason": "%s reported errors: %s" % (stage_name.replace("_", "-"), [e.get("error") for e in errors]),
            }

    run_traffic = stages.get("run_traffic")
    if run_traffic is None:
        return {"verdict": "INCONCLUSIVE", "reason": "run-traffic stage has not been run yet"}
    if run_traffic.get("error"):
        return {"verdict": "FAIL_SETUP", "reason": "run-traffic could not start: %s" % run_traffic["error"]}

    commands = _traffic_commands(stages)
    if not commands:
        return {"verdict": "INCONCLUSIVE", "reason": "run-traffic produced no commands (traffic.type not recognized?)"}
    if not any(c.get("executed") for c in commands):
        return {"verdict": "INCONCLUSIVE", "reason": "run-traffic ran in dry-run mode; no traffic was actually sent"}

    failed = [c for c in commands if c.get("executed") and c.get("returncode") not in (None, 0)]
    if failed:
        return {
            "verdict": "FAIL_CONNECTIVITY",
            "reason": "traffic command(s) failed: %s" % [(c.get("kind"), c.get("returncode")) for c in failed],
        }

    assessment = g(stages.get("validate_offload"), "offload_checker_report", "assessment") or {}
    status = assessment.get("status")
    result = g(stages.get("configure_forwarding"), "result") or {}

    if status in ("probable_offloaded", "probable_partial"):
        if result.get("offload_confirmed") is True and status == "probable_offloaded":
            return {
                "verdict": "PASS_OFFLOAD_CONFIRMED",
                "reason": "traffic succeeded; assessment=%s and backend reports offload_confirmed=true" % status,
            }
        return {
            "verdict": "PASS_OFFLOAD_PROBABLE",
            "reason": "traffic succeeded; offload checker assessment=%s confidence=%s" % (status, assessment.get("confidence")),
        }

    return {
        "verdict": "PASS_FUNCTIONAL_ONLY",
        "reason": (
            "traffic succeeded; offload checker assessment=%s -- no hardware offload evidence" % status
            if status
            else "traffic succeeded; validate-offload stage was not run or produced no assessment"
        ),
    }


def section_next_actions(verdict: str, stages: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    if verdict == "FAIL_SETUP":
        actions.append("Re-run the failing prepare-*/create-vms/attach-vfs stage with --verbose and fix the reported error before retrying traffic.")
    elif verdict == "INCONCLUSIVE":
        actions.append("Re-run `labctl.py vm2vm run-traffic --config <config> --apply` (with safety.dry_run_default: false) to get real traffic evidence.")
    elif verdict == "FAIL_CONNECTIVITY":
        actions.append("Inspect the failed traffic command's stderr; confirm VM guest IPs, routing, and that the OVS/representor path from prepare-ovs is actually up.")
    elif verdict == "PASS_FUNCTIONAL_ONLY":
        actions.append("Traffic works but no offload evidence was found; re-run `validate-offload` after warming up flows, or confirm offload.requested is true in the forwarding config.")
    elif verdict in ("PASS_OFFLOAD_PROBABLE", "PASS_OFFLOAD_CONFIRMED"):
        actions.append("Archive this run directory as evidence; consider a longer --duration validate-offload pass for a CPU-usage before/after comparison.")
    errors = _collect_errors(stages)
    if errors:
        actions.append("Review the Failures and anomalies section (%d item(s)) even though the verdict above may still be a PASS." % len(errors))
    cleanup = section_cleanup_result(stages)
    if not cleanup.get("ran"):
        actions.append("Run `labctl.py vm2vm cleanup` (or `tools/lab_cleanup.py`) before reusing this hardware for another run.")
    return actions


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_SECTION_TITLES = [
    ("test_metadata", "1. Test metadata"),
    ("server_details", "2. Server details"),
    ("vendor_dpu_details", "3. Vendor and DPU/IPU details"),
    ("firmware_driver_details", "4. Firmware and driver details"),
    ("kernel_os_details", "5. Kernel and OS details"),
    ("pf_vf_representor_mapping", "6. PF/VF/representor mapping"),
    ("ovs_bridge_configuration", "7. OVS bridge configuration"),
    ("vm_definitions", "8. VM definitions"),
    ("vm_interface_configuration", "9. VM interface configuration"),
    ("forwarding_configuration", "10. Forwarding Configuration"),
    ("traffic_test_results", "11. Traffic test results"),
    ("ovs_flow_evidence", "12. OVS flow evidence"),
    ("tc_filter_evidence", "13. TC filter evidence"),
    ("ethtool_counter_evidence", "14. ethtool counter evidence"),
    ("cpu_usage_comparison", "15. CPU usage comparison"),
    ("offload_confidence_assessment", "16. Offload confidence assessment"),
    ("failures_and_anomalies", "17. Failures and anomalies"),
    ("cleanup_result", "18. Cleanup result"),
]


def build_report(input_dir: Path, run_id: Optional[str], vendor: Optional[str]) -> Dict[str, Any]:
    stages = load_stages(input_dir)
    if not vendor:
        # tests/results/<vendor>/<run-id>/ -- best-effort from the path.
        try:
            vendor = input_dir.resolve().parent.name
        except OSError:
            vendor = None
    if not run_id:
        run_id = input_dir.resolve().name

    sections: Dict[str, Any] = {
        "test_metadata": section_test_metadata(stages, input_dir, run_id, vendor),
        "server_details": section_server_details(stages),
        "vendor_dpu_details": section_vendor_dpu_details(stages),
        "firmware_driver_details": section_firmware_driver_details(stages),
        "kernel_os_details": section_kernel_os_details(stages),
        "pf_vf_representor_mapping": section_pf_vf_representor_mapping(stages),
        "ovs_bridge_configuration": section_ovs_bridge_configuration(stages),
        "vm_definitions": section_vm_definitions(stages),
        "vm_interface_configuration": section_vm_interface_configuration(stages),
        "forwarding_configuration": section_forwarding_configuration(stages),
        "traffic_test_results": section_traffic_test_results(stages),
        "ovs_flow_evidence": section_ovs_flow_evidence(stages),
        "tc_filter_evidence": section_tc_filter_evidence(stages),
        "ethtool_counter_evidence": section_ethtool_counter_evidence(stages),
        "cpu_usage_comparison": section_cpu_usage_comparison(stages),
        "offload_confidence_assessment": section_offload_confidence_assessment(stages),
        "failures_and_anomalies": section_failures_and_anomalies(stages),
        "cleanup_result": section_cleanup_result(stages),
    }
    verdict = compute_verdict(stages)
    next_actions = section_next_actions(verdict["verdict"], stages)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "final_verdict": verdict["verdict"],
        "final_verdict_reason": verdict["reason"],
        "next_actions": next_actions,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _fmt_scalar(value: Any) -> str:
    if value is None:
        return "_not available_"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)) and not value:
        return "_none_"
    return str(value)


def _render_value(value: Any, lines: List[str], indent: int) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            lines.append(pad + "- _none_")
            return
        for key, sub in value.items():
            if isinstance(sub, (dict, list)) and sub:
                lines.append(pad + "- **%s**:" % key)
                _render_value(sub, lines, indent + 1)
            else:
                lines.append(pad + "- **%s**: %s" % (key, _fmt_scalar(sub)))
    elif isinstance(value, list):
        if not value:
            lines.append(pad + "- _none_")
            return
        for item in value:
            if isinstance(item, dict):
                summary = ", ".join(
                    "%s=%s" % (k, _fmt_scalar(v)) for k, v in item.items() if not isinstance(v, (dict, list))
                )
                lines.append(pad + "- " + (summary or "_entry_"))
                nested = {k: v for k, v in item.items() if isinstance(v, (dict, list)) and v}
                if nested:
                    _render_value(nested, lines, indent + 1)
            else:
                lines.append(pad + "- %s" % _fmt_scalar(item))
    else:
        lines.append(pad + "- %s" % _fmt_scalar(value))


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    meta = report["sections"]["test_metadata"]
    lines.append("# VM-to-VM single-DPU lab result — %s / %s" % (meta.get("vendor") or "unknown-vendor", meta.get("run_id") or "unknown-run"))
    lines.append("")
    lines.append("Generated: %s by `tools/report_generator.py`." % report["generated_at"])
    lines.append("")
    lines.append(
        "This report separates *forwarding configured*, *traffic works*, "
        "*offload requested*, *offload probably active*, and *offload "
        "confirmed* as five independent claims (see "
        "`docs/test-plan/forwarding-validation.md`). Do not read a PASS "
        "verdict below as hardware-offload proof unless section 16 says so."
    )
    lines.append("")

    for key, title in _SECTION_TITLES:
        lines.append("## %s" % title)
        lines.append("")
        _render_value(report["sections"].get(key) or {}, lines, 0)
        lines.append("")

    lines.append("## 19. Final verdict")
    lines.append("")
    lines.append("**%s**" % report["final_verdict"])
    lines.append("")
    lines.append(report["final_verdict_reason"])
    lines.append("")

    lines.append("## 20. Next actions")
    lines.append("")
    for action in report["next_actions"] or ["None -- no further action recommended."]:
        lines.append("- %s" % action)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a standardized result-summary.md / result-summary.json for one VM-to-VM lab run.",
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Run directory, e.g. tests/results/<vendor>/<run-id>/ (as written by tools/labctl.py).",
    )
    parser.add_argument(
        "--output",
        help="Path for the markdown report (default: <input-dir>/result-summary.md). "
        "The JSON twin is written next to it with a .json extension.",
    )
    parser.add_argument("--run-id", help="Override the run id shown in the report (default: input-dir's own name).")
    parser.add_argument("--vendor", help="Override the vendor shown in the report (default: input-dir's parent name).")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print("error: --input-dir %s is not a directory" % input_dir, file=sys.stderr)
        return 1

    report = build_report(input_dir, args.run_id, args.vendor)

    output_md = Path(args.output) if args.output else (input_dir / "result-summary.md")
    output_json = output_md.with_suffix(".json")

    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    print("wrote %s" % output_md)
    print("wrote %s" % output_json)
    print("final_verdict: %s" % report["final_verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
