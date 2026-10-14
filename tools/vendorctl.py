#!/usr/bin/env python3
"""vendorctl — CLI entrypoint for ODSE vendor backends.

Default is dry-run / read-only. Destructive configure subcommands do not
apply changes unless --apply is passed (and even then current plugins are
stubs that do not execute destructive CLI).

Usage:
    python3 tools/vendorctl.py list-vendors --json
    python3 tools/vendorctl.py discover --vendor nvidia-bluefield \\
        --config config/lab.example.yaml
    python3 tools/vendorctl.py configure-sriov --vendor nvidia-bluefield \\
        --pf <PF> --num-vfs 4 --dry-run
    python3 tools/vendorctl.py configure-sriov --vendor nvidia-bluefield \\
        --pf <PF> --num-vfs 4 --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import load_backend  # noqa: E402
from plugins.base.errors import UnknownBackend  # noqa: E402
from plugins.base.lab_config import (  # noqa: E402
    config_dry_run,
    first_bridge,
    first_pf,
    intel_plugin_config,
    load_lab_config,
    public_lab_view,
    select_device,
)
from plugins.base.models import OperationResult  # noqa: E402

LOG = logging.getLogger("vendorctl")

CLI_VENDORS = (
    "nvidia-bluefield",
    "intel-ipu",
    "marvell-octeon",
    "amd-pensando",
)

DESTRUCTIVE_COMMANDS = frozenset(
    {
        "configure-sriov",
        "configure-switchdev",
        "configure-ovs",
        "configure-vdpa",
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


def _parse_ports(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _placeholder(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if text.startswith("<") and text.endswith(">"):
        return None
    return text


def _dry_run(args: argparse.Namespace, lab: Optional[Dict[str, Any]] = None) -> bool:
    """True unless --apply is set, config allows apply, and --dry-run is off."""
    if getattr(args, "dry_run", False):
        return True
    if config_dry_run(lab):
        return True
    if getattr(args, "apply", False):
        return False
    return True


def _apply_allowed(args: argparse.Namespace, lab: Optional[Dict[str, Any]] = None) -> bool:
    return (not _dry_run(args, lab)) and bool(getattr(args, "apply", False))


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only (default). Wins over --apply if both are set.",
    )
    shared.add_argument(
        "--apply",
        action="store_true",
        help="Allow destructive configure operations (still a plugin stub today).",
    )
    shared.add_argument("--json", action="store_true", help="Print JSON.")
    shared.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG logging.",
    )
    shared.add_argument(
        "--config",
        help="Lab YAML (example: config/lab.example.yaml). Does not apply changes.",
    )
    shared.add_argument(
        "--host",
        help="hosts[].id from the lab config (optional).",
    )

    parser = argparse.ArgumentParser(
        prog="vendorctl",
        description="ODSE vendor backend CLI. Default: dry-run, no mutations.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-vendors", parents=[shared], help="List supported CLI vendor names")

    def with_vendor(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[shared], help=help_text)
        p.add_argument(
            "--vendor",
            required=True,
            choices=CLI_VENDORS,
            help="Vendor plugin id",
        )
        return p

    with_vendor("discover", "Inventory / capability discovery")
    with_vendor("validate", "Validate prerequisites (read-only)")
    sriov = with_vendor("configure-sriov", "Plan/apply SR-IOV numvfs (destructive if --apply)")
    sriov.add_argument("--pf", help="PF netdev or PCI (or lab config pfs[].name)")
    sriov.add_argument("--num-vfs", type=int, dest="num_vfs", help="VF count (or expected_vfs)")
    sw = with_vendor("configure-switchdev", "Plan/apply eswitch mode (destructive if --apply)")
    sw.add_argument("--pf", help="PF netdev or PCI (or lab config pfs[].name)")
    with_vendor("discover-representors", "Discover VF representors")
    ovs = with_vendor("configure-ovs", "Plan/apply OVS bridge/ports (destructive if --apply)")
    ovs.add_argument("--bridge", help="Bridge name (or lab config bridges[].name)")
    ovs.add_argument(
        "--ports",
        help="Comma-separated representor netdevs; not guessed from PFs",
    )
    vdpa = with_vendor("configure-vdpa", "Plan/apply vDPA (destructive if --apply)")
    vdpa.add_argument("--vf", help="VF / vDPA target (or lab vdpa.vf)")
    with_vendor("telemetry", "Collect telemetry (omit missing; no fake zeros)")
    with_vendor("validate-offload", "Offload evidence (unknown is valid)")
    return parser


def _backend(vendor: str, device: Optional[Dict[str, Any]] = None):
    kwargs: Dict[str, Any] = {}
    if device and vendor == "intel-ipu":
        kwargs["config"] = intel_plugin_config(device)
    if device and vendor == "marvell-octeon":
        kwargs["configured_dao_app"] = device.get("dao_app")
    return load_backend(vendor, **kwargs)


def _apply_lab_defaults(args: argparse.Namespace, device: Optional[Dict[str, Any]]) -> None:
    pf = first_pf(device)
    bridge = first_bridge(device)
    vdpa = (device or {}).get("vdpa") if isinstance((device or {}).get("vdpa"), dict) else {}
    if not getattr(args, "pf", None):
        args.pf = _placeholder(pf.get("name")) or _placeholder(pf.get("pci"))
    if getattr(args, "num_vfs", None) is None:
        expected = pf.get("expected_vfs")
        args.num_vfs = expected if isinstance(expected, int) else None
    if not getattr(args, "bridge", None):
        args.bridge = _placeholder(bridge.get("name"))
    if getattr(args, "ports", None) is None:
        args.ports = None
    if not getattr(args, "vf", None):
        args.vf = _placeholder((vdpa or {}).get("vf"))
    args.datapath_type = _placeholder(bridge.get("datapath_type"))


def _run_backend(
    args: argparse.Namespace,
    *,
    device: Optional[Dict[str, Any]] = None,
) -> OperationResult:
    backend = _backend(args.vendor, device)
    cmd = args.command
    if cmd == "discover":
        return backend.discover()
    if cmd == "validate":
        return backend.validate_prerequisites()
    if cmd == "configure-sriov":
        return backend.configure_sriov(pf=args.pf, numvfs=args.num_vfs)
    if cmd == "configure-switchdev":
        return backend.configure_switchdev(pf=args.pf)
    if cmd == "discover-representors":
        return backend.discover_representors(pf=getattr(args, "pf", None))
    if cmd == "configure-ovs":
        ports = _parse_ports(args.ports)
        result = backend.configure_ovs_bridge(bridge=args.bridge)
        result.details["bridge"] = args.bridge
        result.details["ports"] = ports
        if getattr(args, "datapath_type", None):
            result.details["datapath_type"] = args.datapath_type
        return result
    if cmd == "configure-vdpa":
        return backend.configure_vdpa(vf=getattr(args, "vf", None))
    if cmd == "telemetry":
        return backend.collect_telemetry()
    if cmd == "validate-offload":
        return backend.validate_offload()
    raise ValueError("unknown command %r" % cmd)


def _blocked_destructive(args: argparse.Namespace) -> OperationResult:
    ports = _parse_ports(getattr(args, "ports", None))
    return OperationResult(
        operation=args.command.replace("-", "_"),
        vendor=args.vendor,
        status="skipped",
        message=(
            "destructive command blocked: pass --apply, set lab dry_run: false, "
            "and do not pass --dry-run. Plugin stubs still will not execute CLI "
            "until implementers opt in."
        ),
        destructive=True,
        executed=False,
        details={
            "apply": False,
            "dry_run": True,
            "pf": getattr(args, "pf", None),
            "num_vfs": getattr(args, "num_vfs", None),
            "bridge": getattr(args, "bridge", None),
            "ports": ports,
            "vf": getattr(args, "vf", None),
        },
    )


def _envelope(
    args: argparse.Namespace,
    result: Optional[OperationResult] = None,
    *,
    lab: Optional[Dict[str, Any]] = None,
    device: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dry = _dry_run(args, lab)
    body: Dict[str, Any] = {
        "cli": "odse-vendorctl/v1alpha1",
        "command": args.command,
        "dry_run": dry,
        "apply": _apply_allowed(args, lab),
        "verbose": bool(args.verbose),
        "config": getattr(args, "config", None),
    }
    if getattr(args, "vendor", None):
        body["vendor"] = args.vendor
    if args.command == "list-vendors":
        body["vendors"] = list(CLI_VENDORS)
    if lab is not None:
        body["lab"] = public_lab_view(lab, device)
    if result is not None:
        body["result"] = _jsonable(result)
    return body


def _print_text(envelope: Dict[str, Any]) -> None:
    print("command: %s" % envelope["command"])
    print("dry_run: %s  apply: %s" % (envelope["dry_run"], envelope["apply"]))
    if "vendors" in envelope:
        print("vendors:")
        for name in envelope["vendors"]:
            print("  - %s" % name)
        return
    print("vendor: %s" % envelope.get("vendor", ""))
    result = envelope.get("result") or {}
    print("status: %s" % result.get("status"))
    print("executed: %s  destructive: %s" % (result.get("executed"), result.get("destructive")))
    print("message: %s" % result.get("message", ""))
    if result.get("details"):
        print("details: %s" % json.dumps(result["details"], sort_keys=True))


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
    )

    if args.command == "list-vendors":
        envelope = _envelope(args, lab=None)
        if args.json:
            print(json.dumps(envelope, indent=2, sort_keys=True))
        else:
            _print_text(envelope)
        return 0

    lab: Optional[Dict[str, Any]] = None
    device: Optional[Dict[str, Any]] = None
    if getattr(args, "config", None):
        try:
            lab = load_lab_config(args.config)
        except (OSError, ValueError) as exc:
            LOG.error("failed to load --config: %s", exc)
            return 1
        device = select_device(lab, args.vendor, host_id=getattr(args, "host", None))
        if device is None:
            LOG.warning("no dpu_devices entry for vendor=%s host=%s", args.vendor, args.host)
        _apply_lab_defaults(args, device)

    try:
        _backend(args.vendor, device)
    except UnknownBackend as exc:
        LOG.error("%s", exc)
        return 1

    dry = _dry_run(args, lab)
    apply_ok = _apply_allowed(args, lab)
    LOG.info(
        "vendorctl command=%s vendor=%s dry_run=%s apply=%s config=%s",
        args.command,
        args.vendor,
        dry,
        apply_ok,
        getattr(args, "config", None),
    )
    if getattr(args, "apply", False) and config_dry_run(lab) and not getattr(args, "dry_run", False):
        LOG.warning("--apply ignored while lab config dry_run is true")

    if args.command in DESTRUCTIVE_COMMANDS and not apply_ok:
        LOG.warning("destructive op %s held at dry-run", args.command)
        result = _blocked_destructive(args)
        planned = _run_backend(args, device=device)
        planned.executed = False
        planned.details = dict(planned.details)
        planned.details["apply"] = False
        planned.details["dry_run"] = True
        planned.details["cli_block"] = result.message
        planned.details["lab"] = public_lab_view(lab, device)
        if args.command == "configure-ovs":
            planned.details["ports"] = _parse_ports(args.ports)
            planned.details["bridge"] = args.bridge
        result = planned
        result.message = "%s | %s" % (result.message, result.details["cli_block"])
    else:
        result = _run_backend(args, device=device)
        result.details = dict(result.details)
        result.details["apply"] = apply_ok
        result.details["dry_run"] = dry
        result.details["lab"] = public_lab_view(lab, device)
        if apply_ok and result.destructive:
            LOG.warning(
                "--apply set but vendor plugin is still a stub; executed=%s",
                result.executed,
            )

    envelope = _envelope(args, result, lab=lab, device=device)
    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        _print_text(envelope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
