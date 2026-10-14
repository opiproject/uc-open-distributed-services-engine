#!/usr/bin/env python3
"""dpu_telemetry_collector.py

Vendor-neutral snapshot of Linux netdev, ethtool, ip, OVS, and tc
counters for ODSE. Skeleton: parsers are best-effort; missing tools
become errors on that section, not a process crash. Do not invent
counter values (OPI-6 / FN-16).

Usage:
    python3 dpu_telemetry_collector.py --pretty
    python3 dpu_telemetry_collector.py -i eth0 -i br-int -o snap.json --pretty
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = "odse-dpu-telemetry/v1alpha1"
DEFAULT_TIMEOUT_SECONDS = 15
SYSFS_NET = "/sys/class/net"

ETHTOOL_COUNTER_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_./:-]+)\s*:\s*(?P<value>[-+]?\d+)\s*$"
)
OVS_FIELD = re.compile(r"^(\S+)\s*:\s*(.*)$")
IP_HEADER = re.compile(r"^\d+:\s+([^:@]+)(?:@\S+)?:")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(
    args: Sequence[str], timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "command": " ".join(args),
        "available": False,
        "returncode": None,
        "stdout": None,
        "stderr": None,
        "error": None,
    }
    if not command_exists(args[0]):
        result["error"] = f"'{args[0]}' not found on PATH"
        return result
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result["available"] = True
        result["returncode"] = completed.returncode
        result["stdout"] = completed.stdout
        result["stderr"] = completed.stderr
        if completed.returncode != 0 and not (completed.stdout or "").strip():
            err = (completed.stderr or "").strip() or "no output"
            result["error"] = f"command exited {completed.returncode}: {err}"
    except subprocess.TimeoutExpired:
        result["error"] = f"command timed out after {timeout}s"
    except PermissionError as exc:
        result["error"] = f"permission denied: {exc}"
    except OSError as exc:
        result["error"] = f"OS error running command: {exc}"
    except Exception as exc:
        result["error"] = f"unexpected error: {exc}"
    return result


def read_sysfs(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
            return text if text else None
    except OSError:
        return None


def list_netdevs(sysfs_net: str) -> List[str]:
    try:
        names = sorted(os.listdir(sysfs_net))
    except OSError:
        return []
    return [n for n in names if n != "lo" and not n.startswith(".")]


def collect_sysfs_stats(sysfs_net: str, name: str) -> Optional[Dict[str, int]]:
    stats_dir = os.path.join(sysfs_net, name, "statistics")
    if not os.path.isdir(stats_dir):
        return None
    out: Dict[str, int] = {}
    try:
        for fname in os.listdir(stats_dir):
            raw = read_sysfs(os.path.join(stats_dir, fname))
            if raw is None:
                continue
            try:
                out[fname] = int(raw)
            except ValueError:
                continue
    except OSError:
        return None
    return out


def parse_ethtool_s(raw: Optional[str]) -> Dict[str, int]:
    counters: Dict[str, int] = {}
    if not raw:
        return counters
    for line in raw.splitlines():
        match = ETHTOOL_COUNTER_LINE.match(line)
        if not match:
            continue
        counters[match.group("name")] = int(match.group("value"))
    return counters


def parse_ip_s_link(raw: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Best-effort rx/tx bytes/packets from `ip -s link`."""
    by_name: Dict[str, Dict[str, Any]] = {}
    current: Optional[str] = None
    expect: Optional[str] = None
    if not raw:
        return by_name
    for line in raw.splitlines():
        header = IP_HEADER.match(line)
        if header:
            current = header.group(1)
            by_name[current] = {"rx": None, "tx": None, "raw_lines": []}
            expect = None
            continue
        if current is None:
            continue
        stripped = line.strip()
        by_name[current]["raw_lines"].append(stripped)
        if stripped.startswith("RX:"):
            expect = "rx"
            continue
        if stripped.startswith("TX:"):
            expect = "tx"
            continue
        if expect and re.match(r"^\d+", stripped):
            parts = stripped.split()
            # ip -s: bytes packets errors dropped ...
            if len(parts) >= 2:
                try:
                    by_name[current][expect] = {
                        "bytes": int(parts[0]),
                        "packets": int(parts[1]),
                    }
                except ValueError:
                    pass
            expect = None
    return by_name


def parse_ovs_list_records(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse `ovs-vsctl list <table>` stanzas into dicts."""
    records: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    if not raw:
        return records
    for line in raw.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        match = OVS_FIELD.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        current[key] = _parse_ovs_value(value)
    if current:
        records.append(current)
    return records


def _parse_ovs_value(value: str) -> Any:
    if value in ("[]", "{}"):
        return [] if value == "[]" else {}
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        parsed: Dict[str, Any] = {}
        if not inner:
            return parsed
        for part in inner.split(", "):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            k, v = k.strip(), v.strip()
            try:
                parsed[k] = int(v)
            except ValueError:
                parsed[k] = v
        return parsed
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def parse_dump_flow_lines(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.lower().startswith("flow dump"):
            lines.append(stripped)
    return lines


def collect_interfaces(
    names: List[str],
    sysfs_net: str,
    ip_stats: Dict[str, Dict[str, Any]],
    timeout: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in names:
        ethtool = run_command(["ethtool", "-S", name], timeout=timeout)
        ethtool_counters = parse_ethtool_s(ethtool.get("stdout"))
        if ethtool.get("error") and not ethtool_counters:
            ethtool_counters_out: Optional[Dict[str, int]] = None
        else:
            ethtool_counters_out = ethtool_counters
        rows.append(
            {
                "name": name,
                "sysfs_statistics": collect_sysfs_stats(sysfs_net, name),
                "ip_stats": ip_stats.get(name),
                "ethtool_s": ethtool_counters_out,
                "ethtool_error": ethtool.get("error"),
            }
        )
    return rows


def collect_tc_filters(names: List[str], timeout: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in names:
        result = run_command(
            ["tc", "-s", "filter", "show", "dev", name, "ingress"],
            timeout=timeout,
        )
        rows.append(
            {
                "interface": name,
                "command": result.get("command"),
                "error": result.get("error"),
                "stdout": result.get("stdout") if result.get("stdout") else None,
            }
        )
    return rows


def run_vendor_plugins(plugins: List[Any], document: Dict[str, Any]) -> Dict[str, Any]:
    ext: Dict[str, Any] = {}
    context = {"report": document, "interfaces": document.get("interfaces")}
    for plugin in plugins:
        name = getattr(plugin, "name", plugin.__class__.__name__)
        try:
            payload = plugin.collect(context) or {}
        except Exception as exc:
            payload = {"implemented": False, "error": str(exc)}
        ext[name] = payload
    return ext


def _planned(argv: Sequence[str], *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from plugins.base.remote import RemoteCommandRunner, ssh_params
    except Exception:
        return {
            "command": " ".join(argv),
            "available": False,
            "returncode": None,
            "stdout": None,
            "stderr": None,
            "error": "DPU telemetry planned (not executed)",
            "argv": list(argv),
            "planned": True,
        }
    params = ssh_params(config or {})
    if params.get("host") and params.get("user"):
        result = RemoteCommandRunner().run_ssh(
            params["host"],
            params["user"],
            list(argv),
            key_path=params.get("key_path"),
            port=int(params.get("port") or 22),
            dry_run=True,
            reason="dpu telemetry",
            sudo=bool(params.get("sudo")),
        )
        return {
            "command": " ".join(result.argv),
            "available": False,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": "DPU telemetry planned (not executed)",
            "argv": list(result.argv),
            "planned": True,
        }
    return {
        "command": " ".join(argv),
        "available": False,
        "returncode": None,
        "stdout": None,
        "stderr": None,
        "error": "DPU telemetry planned (not executed)",
        "argv": list(argv),
        "planned": True,
    }


def build_document(
    *,
    interface_filter: Optional[List[str]],
    sysfs_net: str,
    timeout: int,
    dump_flows_more: bool,
    plugins: List[Any],
    location: str = "host",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = None

    names = interface_filter if interface_filter else (
        [] if location == "dpu" else list_netdevs(sysfs_net)
    )
    if location == "dpu":
        ip_result = {
            "error": "host ip -s link is not DPU telemetry",
            "planned": True,
        }
        ip_stats: Dict[str, Dict[str, Any]] = {}
        iface_rows: List[Dict[str, Any]] = []
        br_result = _planned(["ovs-vsctl", "list", "bridge"], config=config)
        if_result = _planned(["ovs-vsctl", "list", "interface"], config=config)
        dp_show = _planned(["ovs-appctl", "dpctl/show"], config=config)
        dump_args = ["ovs-appctl", "dpctl/dump-flows"]
        if dump_flows_more:
            dump_args.append("-m")
        dump = _planned(dump_args, config=config)
        tc_rows: List[Dict[str, Any]] = [
            _planned(["tc", "-s", "filter", "show", "dev", name, "ingress"], config=config)
            for name in names
        ]
        note = (
            "DPU location: OVS/tc probes are planned over SSH. Host ovs-vsctl "
            "is not DPU evidence. Missing counters stay null."
        )
    else:
        ip_result = run_command(["ip", "-s", "link"], timeout=timeout)
        ip_stats = parse_ip_s_link(ip_result.get("stdout"))
        iface_rows = collect_interfaces(names, sysfs_net, ip_stats, timeout)
        br_result = run_command(["ovs-vsctl", "list", "bridge"], timeout=timeout)
        if_result = run_command(["ovs-vsctl", "list", "interface"], timeout=timeout)
        dp_show = run_command(["ovs-appctl", "dpctl/show"], timeout=timeout)
        dump_args = ["ovs-appctl", "dpctl/dump-flows"]
        if dump_flows_more:
            dump_args.append("-m")
        dump = run_command(dump_args, timeout=timeout)
        tc_rows = collect_tc_filters(names, timeout)
        note = (
            "Missing counters are null/omitted, never synthesized. "
            "vendor_extension plugins are placeholders until vendor tracks implement them."
        )

    bridges = parse_ovs_list_records(br_result.get("stdout"))
    ovs_ifaces = parse_ovs_list_records(if_result.get("stdout"))

    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": hostname,
        "location": location,
        "interfaces": iface_rows,
        "ovs_bridges": {
            "error": br_result.get("error"),
            "bridges": bridges,
            "interfaces": ovs_ifaces,
            "list_interface_error": if_result.get("error"),
            "dpctl_show": {
                "error": dp_show.get("error"),
                "stdout": dp_show.get("stdout"),
                "planned": br_result.get("planned"),
            },
        },
        "ovs_flows": {
            "error": dump.get("error"),
            "command": dump.get("command"),
            "flows": parse_dump_flow_lines(dump.get("stdout")),
            "planned": dump.get("planned"),
        },
        "tc_filters": tc_rows,
        "vendor_extension": {},
        "sources": {
            "ip_s_link_error": ip_result.get("error"),
        },
        "note": note,
    }
    document["vendor_extension"] = run_vendor_plugins(plugins, document)
    return document


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Linux/OVS/tc telemetry as JSON (vendor-neutral skeleton)."
    )
    parser.add_argument(
        "-i",
        "--interface",
        action="append",
        dest="interfaces",
        default=None,
        metavar="IFACE",
        help="Limit netdev/ethtool/tc to these names. Default: all in sysfs except lo.",
    )
    parser.add_argument("--sysfs-net", default=SYSFS_NET)
    parser.add_argument("-o", "--output", metavar="PATH")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--no-more", action="store_true", help="dpctl/dump-flows without -m")
    parser.add_argument("--plugin-dir", action="append", dest="plugin_dirs", default=[])
    parser.add_argument("--no-bundled-plugins", action="store_true")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--location",
        choices=("host", "dpu"),
        default="host",
        help="dpu: plan SSH OVS/tc probes; do not treat host ovs-vsctl as DPU evidence.",
    )
    parser.add_argument("--config", metavar="PATH", help="VM2VM lab YAML (DPU SSH planning).")
    return parser.parse_args(argv)


def _load_plugins(args: argparse.Namespace) -> List[Any]:
    # Package is named vendor_plugins, not "plugins": a bare "plugins"
    # package here collides with the repo-root plugins/ package under
    # pytest's default collection (see tools/topology-discovery/README.md
    # "Known limitations" for the full explanation).
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from vendor_plugins import load_plugins, plugin_dir_default

    dirs: List[Path] = []
    if not args.no_bundled_plugins:
        dirs.append(plugin_dir_default())
    for extra in args.plugin_dirs:
        dirs.append(Path(extra))
    return load_plugins(dirs)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        plugins = _load_plugins(args)
    except Exception as exc:
        print(f"warning: plugin load failed ({exc})", file=sys.stderr)
        plugins = []
    config: Optional[Dict[str, Any]] = None
    location = args.location
    if args.config:
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from plugins.base.vm2vm_config import load_vm2vm_config
        from plugins.base.remote import is_dpu_mode

        try:
            config = load_vm2vm_config(args.config)
            if location == "host" and is_dpu_mode(config):
                location = "dpu"
        except (OSError, ValueError) as exc:
            print(f"warning: could not load --config {args.config}: {exc}", file=sys.stderr)

    try:
        document = build_document(
            interface_filter=args.interfaces,
            sysfs_net=args.sysfs_net,
            timeout=args.timeout,
            dump_flows_more=not args.no_more,
            plugins=plugins,
            location=location,
            config=config,
        )
    except Exception as exc:
        print(f"error: telemetry collection failed: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(document, indent=2 if args.pretty else None)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.write("\n")
        except OSError as exc:
            print(f"error: could not write '{args.output}': {exc}", file=sys.stderr)
            return 1
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
