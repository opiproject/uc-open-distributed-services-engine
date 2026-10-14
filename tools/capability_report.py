#!/usr/bin/env python3
"""capability_report — run discover() for every vendor backend.

Read-only. Does not configure SR-IOV, switchdev, OVS, or vDPA.
Unknown capabilities stay null; this tool never invents True.

Usage:
    python3 tools/capability_report.py --format markdown
    python3 tools/capability_report.py --format json
    python3 tools/capability_report.py --vendor nvidia-bluefield --format json
    python3 tools/capability_report.py --config config/lab-amd-vm2vm.example.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import load_backend  # noqa: E402
from plugins.base.errors import UnknownBackend  # noqa: E402
from plugins.base.models import DPUDevice, OperationResult  # noqa: E402

LOG = logging.getLogger("capability_report")

CLI_VENDORS = (
    "nvidia-bluefield",
    "intel-ipu",
    "marvell-octeon",
    "amd-pensando",
)

# JSON / markdown: unknown is null, never a guessed boolean.
UNKNOWN = None


@dataclass
class CapabilityRow:
    vendor: str
    detected: bool
    confidence: str
    devices: List[Dict[str, Any]]
    management_available: Optional[bool]
    sriov_available: Optional[bool]
    switchdev_available: Optional[bool]
    representors_available: Optional[bool]
    ovs_available: Optional[bool]
    ovs_hw_offload_available: Optional[bool]
    vdpa_available: Optional[bool]
    rdma_available: Optional[bool]
    opi_available: Optional[bool]
    telemetry_available: Optional[bool]
    recommended_next_step: str
    discovery_status: Optional[str] = None
    discovery_message: Optional[str] = None


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


def _device_summary(dev: DPUDevice) -> Dict[str, Any]:
    return {
        "id": dev.id,
        "vendor": dev.vendor,
        "model": dev.model,
        "pci_address": dev.pci_address,
        "management_address": dev.management_address,
        "firmware_version": dev.firmware_version,
    }


def _or_cap(result: OperationResult, attr: str) -> Optional[bool]:
    values: List[Optional[bool]] = []
    if result.capabilities is not None:
        values.append(getattr(result.capabilities, attr, None))
    for dev in result.devices:
        if dev.capabilities is not None:
            values.append(getattr(dev.capabilities, attr, None))
    if any(v is True for v in values):
        return True
    stated = [v for v in values if v is not None]
    if stated and all(v is False for v in stated) and len(stated) == len(values):
        return False
    return UNKNOWN


def _bool_detail(details: Dict[str, Any], *keys: str) -> Optional[bool]:
    for key in keys:
        if key not in details:
            continue
        value = details[key]
        if value is None:
            return UNKNOWN
        if isinstance(value, bool):
            return value
    return UNKNOWN


def _rdma_available(result: OperationResult) -> Optional[bool]:
    details = result.details or {}
    rdma = details.get("rdma")
    if isinstance(rdma, dict) and "detected" in rdma:
        return bool(rdma.get("detected"))
    return UNKNOWN


def _opi_available(result: OperationResult) -> Optional[bool]:
    cap = _or_cap(result, "opi_native")
    if cap is True:
        return True
    details = result.details or {}
    if details.get("opi_configured") is True:
        return True
    cfg = details.get("config")
    if isinstance(cfg, dict) and cfg.get("opi_endpoint"):
        return True
    return UNKNOWN


def _management_available(result: OperationResult) -> Optional[bool]:
    details = result.details or {}
    if details.get("redfish_configured") is True:
        return True
    if any(d.management_address for d in result.devices):
        return True
    cfg = details.get("config")
    if isinstance(cfg, dict) and (cfg.get("redfish_base") or cfg.get("imc_address")):
        return True
    cli = details.get("management_cli")
    if cli:
        # Configured but not executed — management *path* exists, not proven live.
        return True
    return UNKNOWN


def _telemetry_available(result: OperationResult) -> Optional[bool]:
    if result.telemetry:
        return True
    details = result.details or {}
    stats = details.get("stats_interfaces")
    if isinstance(stats, list) and stats:
        return True
    return UNKNOWN


def _switchdev_available(result: OperationResult) -> Optional[bool]:
    cap = _or_cap(result, "switchdev")
    if cap is not None:
        return cap
    for pf in result.physical_functions:
        mode = (pf.eswitch_mode or "").lower()
        if mode == "switchdev":
            return True
        if mode == "legacy":
            return False
    details = result.details or {}
    if "switchdev" in details and details["switchdev"] is None:
        return UNKNOWN
    return UNKNOWN


def _representors_available(result: OperationResult) -> Optional[bool]:
    if result.representors:
        return True
    details = result.details or {}
    if "representors" in details and details["representors"] is None:
        return UNKNOWN
    # Empty list after a real NVIDIA-style parse still means "none found", not "unsupported".
    if result.vendor == "nvidia" and result.status == "ok":
        return False
    return UNKNOWN


def _ovs_available(result: OperationResult) -> Optional[bool]:
    details = result.details or {}
    if "ovs_present" in details:
        return bool(details["ovs_present"])
    return UNKNOWN


def _ovs_hw_offload_available(result: OperationResult) -> Optional[bool]:
    cap = _or_cap(result, "ovs_hw_offload")
    if cap is True:
        return True
    # Explicit False from AMD is "not assumed / not claimed", not lab proof of absence.
    if cap is False and result.vendor == "amd":
        return UNKNOWN
    if cap is False:
        return False
    details = result.details or {}
    if details.get("ovs_hw_offload") is False and result.vendor == "amd":
        return UNKNOWN
    return _bool_detail(details, "ovs_hw_offload")


def _sriov_available(result: OperationResult) -> Optional[bool]:
    cap = _or_cap(result, "sriov")
    if cap is not None:
        return cap
    if result.virtual_functions:
        return True
    for pf in result.physical_functions:
        if pf.current_vfs and pf.current_vfs > 0:
            return True
    return UNKNOWN


def _vdpa_available(result: OperationResult) -> Optional[bool]:
    cap = _or_cap(result, "vdpa")
    if cap is not None:
        return cap
    details = result.details or {}
    if "vdpa" in details:
        return details["vdpa"] if isinstance(details["vdpa"], bool) else UNKNOWN
    return UNKNOWN


def _confidence(result: OperationResult, detected: bool) -> str:
    if not detected:
        return "none"
    pci = any(d.pci_address for d in result.devices)
    extra = bool(result.representors) or bool(result.virtual_functions)
    details = result.details or {}
    rdma = details.get("rdma") if isinstance(details.get("rdma"), dict) else {}
    ionic = bool(details.get("ionic_interfaces"))
    mlx5 = bool(details.get("mlx5_interfaces"))
    if pci and (extra or (isinstance(rdma, dict) and rdma.get("detected")) or (ionic and mlx5)):
        return "high"
    if pci or ionic or mlx5 or bool(details.get("dpdk_ports")):
        return "medium"
    if details.get("redfish_configured") or details.get("opi_configured"):
        return "low"
    if result.devices:
        return "medium"
    return "low"


def _detected(result: OperationResult) -> bool:
    if result.devices:
        return True
    details = result.details or {}
    if details.get("ionic_interfaces"):
        return True
    if details.get("mlx5_interfaces"):
        return True
    rdma = details.get("rdma")
    if isinstance(rdma, dict) and rdma.get("detected"):
        return True
    if details.get("dpdk_ports"):
        return True
    # Config-only Intel is not PCI detection.
    if details.get("redfish_configured") or details.get("opi_configured"):
        return True
    return False


def _next_step(vendor: str, row: CapabilityRow) -> str:
    if not row.detected:
        return (
            "No %s evidence on this host; skip vendor enablement until inventory matches."
            % vendor
        )
    if vendor == "nvidia-bluefield":
        if row.representors_available is True:
            return (
                "Representors observed; collect OVS-TC/offload evidence next "
                "(unknown is valid; do not claim offloaded:yes)."
            )
        if row.switchdev_available is not True:
            return (
                "Read eswitch mode (devlink); do not set switchdev unless VFs are unbound "
                "and the operator explicitly applies."
            )
        return "Inventory NVIDIA PFs; keep configure paths dry-run until apply is requested."
    if vendor == "intel-ipu":
        return (
            "Continue OPI/Redfish/DPU Operator path; do not copy NVIDIA switchdev/OVS-DOCA. "
            "Treat representors and vDPA as discovery-required."
        )
    if vendor == "marvell-octeon":
        return (
            "Confirm DAO/DPDK representors with documented CLI; kernel switchdev is not assumed. "
            "Generic ovs-vsctl presence is not DAO offload."
        )
    if vendor == "amd-pensando":
        return (
            "Continue ionic/RDMA inventory. Do not assume switchdev, representors, "
            "OVS hardware offload, or vDPA."
        )
    return "Keep discovery-first; do not enable unproven datapath features."


def row_from_discovery(cli_vendor: str, result: OperationResult) -> CapabilityRow:
    detected = _detected(result)
    row = CapabilityRow(
        vendor=cli_vendor,
        detected=detected,
        confidence=_confidence(result, detected),
        devices=[_device_summary(d) for d in result.devices],
        management_available=_management_available(result),
        sriov_available=_sriov_available(result),
        switchdev_available=_switchdev_available(result),
        representors_available=_representors_available(result),
        ovs_available=_ovs_available(result),
        ovs_hw_offload_available=_ovs_hw_offload_available(result),
        vdpa_available=_vdpa_available(result),
        rdma_available=_rdma_available(result),
        opi_available=_opi_available(result),
        telemetry_available=_telemetry_available(result),
        recommended_next_step="",
        discovery_status=result.status,
        discovery_message=result.message,
    )
    row.recommended_next_step = _next_step(cli_vendor, row)
    return row


def discover_vendor(cli_vendor: str) -> CapabilityRow:
    backend = load_backend(cli_vendor)
    try:
        result = backend.discover()
    except Exception as exc:  # pragma: no cover - defensive CLI path
        LOG.exception("discover failed for %s", cli_vendor)
        return CapabilityRow(
            vendor=cli_vendor,
            detected=False,
            confidence="none",
            devices=[],
            management_available=UNKNOWN,
            sriov_available=UNKNOWN,
            switchdev_available=UNKNOWN,
            representors_available=UNKNOWN,
            ovs_available=UNKNOWN,
            ovs_hw_offload_available=UNKNOWN,
            vdpa_available=UNKNOWN,
            rdma_available=UNKNOWN,
            opi_available=UNKNOWN,
            telemetry_available=UNKNOWN,
            recommended_next_step="Discovery raised an error; inspect logs. No capabilities inferred.",
            discovery_status="error",
            discovery_message=str(exc),
        )
    return row_from_discovery(cli_vendor, result)


def collect_report(vendors: Sequence[str]) -> List[CapabilityRow]:
    rows: List[CapabilityRow] = []
    for name in vendors:
        LOG.info("discovering %s", name)
        rows.append(discover_vendor(name))
    return rows


def render_json(rows: Iterable[CapabilityRow]) -> str:
    payload = {
        "cli": "odse-capability-report/v1alpha1",
        "note": (
            "true = proven on this host; false = proven absent where the plugin "
            "can say so; null = unknown / discovery-required. Dummy docs must not "
            "be treated as lab results."
        ),
        "vendors": [_jsonable(r) for r in rows],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _cell(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def render_markdown(rows: Sequence[CapabilityRow]) -> str:
    lines = [
        "# ODSE capability report",
        "",
        "Generated by `tools/capability_report.py` from live `discover()` calls.",
        "`null` means unknown / discovery-required. `yes` is only used when proven.",
        "",
        "| vendor | detected | confidence | devices | management | sriov | switchdev | representors | ovs | ovs_hw_offload | vdpa | rdma | opi | telemetry |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        n = len(row.devices)
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                row.vendor,
                _cell(row.detected),
                row.confidence,
                n,
                _cell(row.management_available),
                _cell(row.sriov_available),
                _cell(row.switchdev_available),
                _cell(row.representors_available),
                _cell(row.ovs_available),
                _cell(row.ovs_hw_offload_available),
                _cell(row.vdpa_available),
                _cell(row.rdma_available),
                _cell(row.opi_available),
                _cell(row.telemetry_available),
            )
        )
    lines.extend(["", "## Recommended next steps", ""])
    for row in rows:
        lines.append("### %s" % row.vendor)
        lines.append("")
        lines.append("- discovery_status: `%s`" % (row.discovery_status or "null"))
        if row.discovery_message:
            lines.append("- discovery_message: %s" % row.discovery_message)
        lines.append("- recommended_next_step: %s" % row.recommended_next_step)
        if row.devices:
            lines.append("- devices:")
            for dev in row.devices:
                lines.append(
                    "  - %s pci=%s model=%s"
                    % (dev.get("id"), dev.get("pci_address"), dev.get("model"))
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capability_report",
        description="Run vendor discover() and print an honest capability report.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "--vendor",
        action="append",
        choices=CLI_VENDORS,
        help="Limit to one vendor (repeatable). Default: all.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--config",
        help="If set, emit AMD Pensando forwarding gap report from this lab YAML (no discover()).",
    )
    parser.add_argument("--output", help="Write AMD gap report or discover output to this path.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --config, emit AMD gap JSON instead of markdown.",
    )
    return parser


def _amd_gap_from_config(config_path: str, *, as_json: bool, output: Optional[str]) -> int:
    from plugins.amd_pensando.capability_report import build_gap_report, render_gap_markdown
    from plugins.base.vm2vm_config import load_vm2vm_config

    config = load_vm2vm_config(config_path)
    report = build_gap_report(config)
    text = json.dumps(report, indent=2) if as_json else render_gap_markdown(report)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config:
        return _amd_gap_from_config(args.config, as_json=args.json or args.format == "json", output=args.output)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    vendors = list(args.vendor) if args.vendor else list(CLI_VENDORS)
    try:
        for name in vendors:
            load_backend(name)
    except UnknownBackend as exc:
        LOG.error("%s", exc)
        return 1
    rows = collect_report(vendors)
    if args.format == "json":
        sys.stdout.write(render_json(rows) + "\n")
    else:
        sys.stdout.write(render_markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
