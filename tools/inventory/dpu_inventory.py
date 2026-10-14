#!/usr/bin/env python3
"""dpu_inventory.py

Vendor-neutral inventory collector for Linux hosts and DPU/IPU-capable
nodes in the Open Distributed Services Engine (ODSE) project.

Collects PCI device, network interface, devlink, and Open vSwitch (OVS)
state from the local machine and produces a single, normalized JSON
inventory document, plus best-effort vendor hints for AMD Pensando,
NVIDIA Mellanox BlueField, Intel IPU, and Marvell OCTEON hardware.

This is intentionally a *skeleton* collector, not a finished inventory
system: parsing of tool output is best-effort and deliberately simple, and
every collection step is designed to degrade gracefully (never raise or
crash the whole run) when a command is missing, unsupported, unprivileged,
or returns unexpected output. See docs/architecture/api-model.md for the
longer-term vendor-neutral Inventory API this tool is a building block
for, and tools/inventory/README.md for usage details and known
limitations.

Usage:
    python3 dpu_inventory.py
    python3 dpu_inventory.py --pretty
    python3 dpu_inventory.py --output inventory.json --pretty
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "odse-inventory/v1alpha1"
DEFAULT_TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Safe command execution
# ---------------------------------------------------------------------------

def command_exists(name: str) -> bool:
    """Return True if `name` resolves to an executable on PATH."""
    return shutil.which(name) is not None


def run_command(
    args: List[str], timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """Run a command safely and return a normalized result dict.

    This never raises: a missing binary, a timeout, a permission error,
    or any other failure is captured in the returned dict instead of
    propagating, so a single missing/unprivileged tool never aborts the
    rest of the inventory run.
    """
    binary = args[0]
    result: Dict[str, Any] = {
        "command": " ".join(args),
        "available": False,
        "returncode": None,
        "stdout": None,
        "stderr": None,
        "error": None,
    }

    if not command_exists(binary):
        result["error"] = f"'{binary}' not found on PATH"
        return result

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result["available"] = True
        result["returncode"] = completed.returncode
        result["stdout"] = completed.stdout
        result["stderr"] = completed.stderr
        if completed.returncode != 0 and not completed.stdout:
            stderr_text = completed.stderr.strip() if completed.stderr else "no output"
            result["error"] = f"command exited {completed.returncode}: {stderr_text}"
    except subprocess.TimeoutExpired:
        result["error"] = f"command timed out after {timeout}s"
    except PermissionError as exc:
        result["error"] = f"permission denied: {exc}"
    except OSError as exc:
        result["error"] = f"OS error running command: {exc}"
    except Exception as exc:  # defensive catch-all; a collector must never crash the run
        result["error"] = f"unexpected error: {exc}"

    return result


# ---------------------------------------------------------------------------
# Parsers (best-effort; unparseable lines are preserved, not dropped)
# ---------------------------------------------------------------------------

_LSPCI_PATTERN = re.compile(
    r"^(?P<slot>\S+)\s+(?P<class_name>.+?)\s+\[(?P<class_id>[0-9a-fA-F]{4})\]:\s+"
    r"(?P<description>.+?)\s+\[(?P<vendor_id>[0-9a-fA-F]{4}):(?P<device_id>[0-9a-fA-F]{4})\]"
    r"(?:\s+\(rev\s+(?P<revision>[0-9a-fA-F]+)\))?\s*$"
)


def parse_lspci(raw: str) -> List[Dict[str, Any]]:
    """Parse `lspci -nn` output into a list of device dicts."""
    devices: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _LSPCI_PATTERN.match(line)
        if match:
            device = match.groupdict()
            device["vendor_id"] = device["vendor_id"].lower()
            device["device_id"] = device["device_id"].lower()
            devices.append(device)
        else:
            devices.append({"raw_line": line, "parse_error": True})
    return devices


_IP_LINK_HEADER_PATTERN = re.compile(
    r"^(?P<index>\d+):\s+(?P<name>[^:@]+)(?:@(?P<parent>\S+))?:\s+"
    r"<(?P<flags>[^>]*)>\s+mtu\s+(?P<mtu>\d+)"
)
_IP_LINK_MAC_PATTERN = re.compile(r"^\s*link/\S+\s+(?P<mac>[0-9a-fA-F:]{17})")


def parse_ip_link(raw: str) -> List[Dict[str, Any]]:
    """Parse `ip -d link show` output into a list of interface dicts."""
    interfaces: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for line in raw.splitlines():
        if not line.strip():
            continue
        header_match = _IP_LINK_HEADER_PATTERN.match(line)
        if header_match:
            if current is not None:
                interfaces.append(current)
            current = {
                "index": int(header_match.group("index")),
                "name": header_match.group("name"),
                "parent": header_match.group("parent"),
                "flags": [f for f in header_match.group("flags").split(",") if f],
                "mtu": int(header_match.group("mtu")),
                "mac_address": None,
                "details": [],
            }
            continue
        if current is None:
            continue
        stripped = line.strip()
        current["details"].append(stripped)
        mac_match = _IP_LINK_MAC_PATTERN.match(line)
        if mac_match and not current["mac_address"]:
            current["mac_address"] = mac_match.group("mac")

    if current is not None:
        interfaces.append(current)
    return interfaces


def parse_devlink_dev(raw: str) -> List[Dict[str, Any]]:
    """Parse `devlink dev show` output into a list of device handles."""
    return [{"handle": line.strip()} for line in raw.splitlines() if line.strip()]


_DEVLINK_PORT_LINE_PATTERN = re.compile(r"^(?P<handle>\S+):\s*(?P<attributes>.*)$")
_DEVLINK_PORT_KV_PATTERN = re.compile(r"(\w+)\s+(\S+)")


def parse_devlink_port(raw: str) -> List[Dict[str, Any]]:
    """Parse `devlink port show` output into a list of port dicts.

    devlink's plain-text output is "key value key value ..." per line,
    which this parser tokenizes with a simple alternating regex. This is
    a heuristic, not a full devlink output grammar; prefer `devlink -j`
    (JSON mode) upstream if a future version of this tool needs stricter
    parsing.
    """
    ports: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _DEVLINK_PORT_LINE_PATTERN.match(line)
        if not match:
            ports.append({"raw_line": line, "parse_error": True})
            continue
        attributes = dict(_DEVLINK_PORT_KV_PATTERN.findall(match.group("attributes")))
        ports.append({"handle": match.group("handle"), **attributes})
    return ports


def parse_ethtool_i(raw: str) -> Dict[str, str]:
    """Parse `ethtool -i <iface>` output into a flat key/value dict."""
    info: Dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip()] = value.strip()
    return info


def parse_ethtool_s(raw: str) -> Dict[str, Any]:
    """Parse `ethtool -S <iface>` output into a flat stat-name/value dict."""
    stats: Dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        try:
            stats[key.strip()] = int(value)
        except ValueError:
            stats[key.strip()] = value
    return stats


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_host_info() -> Dict[str, Any]:
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = None
    return {
        "hostname": hostname,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }


def list_netdev_names() -> List[str]:
    """Enumerate interface names from /sys/class/net (Linux-only, best-effort)."""
    try:
        return sorted(os.listdir("/sys/class/net"))
    except OSError:
        return []


def is_probable_pf(iface: str) -> bool:
    """Heuristic PF (physical function) detector.

    An interface is treated as a PF candidate if it exposes an SR-IOV
    control file in sysfs (`sriov_numvfs`). This is a heuristic, not a
    guarantee: some vendor drivers may not populate this file the same
    way, and some non-PF interfaces could theoretically expose a similar
    path. Cross-check against devlink/vendor tooling for anything
    automation- or security-critical.
    """
    sriov_path = f"/sys/class/net/{iface}/device/sriov_numvfs"
    return os.path.exists(sriov_path)


def collect_pci_devices() -> Dict[str, Any]:
    raw_result = run_command(["lspci", "-nn"])
    devices = parse_lspci(raw_result["stdout"]) if raw_result["stdout"] else []
    return {
        "source_command": "lspci -nn",
        "available": raw_result["available"],
        "error": raw_result["error"],
        "devices": devices,
    }


def collect_netdevs() -> Dict[str, Any]:
    ip_link_result = run_command(["ip", "-d", "link", "show"])
    ip_link_entries = (
        parse_ip_link(ip_link_result["stdout"]) if ip_link_result["stdout"] else []
    )
    ip_link_by_name = {entry["name"]: entry for entry in ip_link_entries}

    interfaces: List[Dict[str, Any]] = []
    for name in list_netdev_names():
        if name == "lo":
            # Loopback carries no vendor/PCI-relevant information for this
            # inventory's purposes and is deliberately skipped.
            continue

        entry: Dict[str, Any] = {
            "name": name,
            "ip_link": ip_link_by_name.get(name),
            "is_probable_pf": is_probable_pf(name),
        }

        ethtool_i_result = run_command(["ethtool", "-i", name])
        if ethtool_i_result["available"] and ethtool_i_result["stdout"]:
            entry["ethtool_info"] = parse_ethtool_i(ethtool_i_result["stdout"])
            entry["ethtool_info_error"] = None
        else:
            entry["ethtool_info"] = None
            entry["ethtool_info_error"] = ethtool_i_result["error"]

        if entry["is_probable_pf"]:
            ethtool_s_result = run_command(["ethtool", "-S", name])
            if ethtool_s_result["available"] and ethtool_s_result["stdout"]:
                entry["ethtool_stats"] = parse_ethtool_s(ethtool_s_result["stdout"])
                entry["ethtool_stats_error"] = None
            else:
                entry["ethtool_stats"] = None
                entry["ethtool_stats_error"] = ethtool_s_result["error"]
        else:
            entry["ethtool_stats"] = None
            entry["ethtool_stats_error"] = (
                "skipped: not a probable PF (no sriov_numvfs in sysfs)"
            )

        interfaces.append(entry)

    return {
        "source_command": "ip -d link show",
        "available": ip_link_result["available"],
        "error": ip_link_result["error"],
        "interfaces": interfaces,
    }


def collect_devlink_devices() -> Dict[str, Any]:
    raw_result = run_command(["devlink", "dev", "show"])
    devices = parse_devlink_dev(raw_result["stdout"]) if raw_result["stdout"] else []
    return {
        "source_command": "devlink dev show",
        "available": raw_result["available"],
        "error": raw_result["error"],
        "devices": devices,
    }


def collect_devlink_ports() -> Dict[str, Any]:
    raw_result = run_command(["devlink", "port", "show"])
    ports = parse_devlink_port(raw_result["stdout"]) if raw_result["stdout"] else []
    return {
        "source_command": "devlink port show",
        "available": raw_result["available"],
        "error": raw_result["error"],
        "ports": ports,
    }


def collect_ovs() -> Dict[str, Any]:
    ovs_installed = command_exists("ovs-vsctl")
    ovs_section: Dict[str, Any] = {"installed": ovs_installed}

    if not ovs_installed:
        not_found = "'ovs-vsctl' not found on PATH (OVS not detected)"
        ovs_section["vsctl_show"] = {"available": False, "error": not_found, "raw": None}
        ovs_section["dpctl_show"] = {"available": False, "error": not_found, "raw": None}
        return ovs_section

    vsctl_result = run_command(["ovs-vsctl", "show"])
    ovs_section["vsctl_show"] = {
        "available": vsctl_result["available"],
        "error": vsctl_result["error"],
        "raw": vsctl_result["stdout"],
    }

    if command_exists("ovs-appctl"):
        dpctl_result = run_command(["ovs-appctl", "dpctl/show"])
        ovs_section["dpctl_show"] = {
            "available": dpctl_result["available"],
            "error": dpctl_result["error"],
            "raw": dpctl_result["stdout"],
        }
    else:
        ovs_section["dpctl_show"] = {
            "available": False,
            "error": "'ovs-appctl' not found on PATH",
            "raw": None,
        }

    return ovs_section


# ---------------------------------------------------------------------------
# Vendor hint detection
#
# These hints are heuristic signals for AMD Pensando, NVIDIA Mellanox
# BlueField, Intel IPU, and Marvell OCTEON hardware, built from PCI vendor
# IDs, driver names, and lspci description strings. They are intended to
# help a human or downstream tool decide "which vendor track applies
# here," not as an authoritative hardware identification mechanism -- see
# docs/vendor-readiness/vendor-capability-matrix.md for why vendor
# capability claims in this project are deliberately conservative.
# ---------------------------------------------------------------------------

# PCI vendor IDs used for high-confidence hints. Intel (8086) is
# deliberately excluded here: it is far too broad a signal (it matches
# almost any Intel NIC, not specifically IPU platforms), so Intel IPU
# detection relies only on lower-confidence driver/description signals.
_VENDOR_PCI_IDS: Dict[str, set] = {
    "amd_pensando": {"1dd8"},  # Pensando Systems, Inc.
    "nvidia_mellanox_bluefield": {"15b3"},  # Mellanox Technologies / NVIDIA
    "marvell_octeon": {"177d"},  # Cavium, Inc. / Marvell
}

_DRIVER_HINTS: Dict[str, set] = {
    "amd_pensando": {"ionic"},
    "nvidia_mellanox_bluefield": {"mlx5_core", "mlx5e"},
    "intel_ipu": {"ice", "idpf"},  # heuristic only; also used by non-IPU Intel NICs
    "marvell_octeon": {"octeontx2-nicvf", "octeon_ep", "octeon_ep_vf", "rvu_nicvf"},
}

_DESCRIPTION_HINTS: Dict[str, List[str]] = {
    "amd_pensando": ["pensando"],
    "nvidia_mellanox_bluefield": ["bluefield", "mellanox"],
    "intel_ipu": ["ipu", "mount evans", "e2100"],
    "marvell_octeon": ["octeon", "cavium", "marvell"],
}

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def detect_vendor_hints(
    pci_devices: List[Dict[str, Any]], netdev_interfaces: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    hints: Dict[str, Dict[str, Any]] = {}

    def record(vendor_key: str, confidence: str, evidence: str) -> None:
        existing = hints.get(vendor_key)
        if existing is None:
            hints[vendor_key] = {
                "vendor": vendor_key,
                "confidence": confidence,
                "evidence": [evidence],
            }
            return
        existing["evidence"].append(evidence)
        if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[existing["confidence"]]:
            existing["confidence"] = confidence

    for device in pci_devices:
        vendor_id = (device.get("vendor_id") or "").lower()
        description = (device.get("description") or "").lower()
        slot = device.get("slot", "unknown-slot")

        for vendor_key, ids in _VENDOR_PCI_IDS.items():
            if vendor_id and vendor_id in ids:
                record(
                    vendor_key,
                    "high",
                    f"PCI vendor ID {vendor_id} matches known {vendor_key} range (slot {slot})",
                )

        for vendor_key, keywords in _DESCRIPTION_HINTS.items():
            for keyword in keywords:
                if keyword in description:
                    record(
                        vendor_key,
                        "medium",
                        f"lspci description contains '{keyword}' (slot {slot})",
                    )

    for interface in netdev_interfaces:
        ethtool_info = interface.get("ethtool_info") or {}
        driver = ethtool_info.get("driver", "")
        if not driver:
            continue
        for vendor_key, drivers in _DRIVER_HINTS.items():
            if driver in drivers:
                # Intel's driver names are shared with plenty of non-IPU
                # hardware, so this signal alone stays at "low" confidence.
                confidence = "low" if vendor_key == "intel_ipu" else "medium"
                record(
                    vendor_key,
                    confidence,
                    f"interface '{interface.get('name')}' uses driver '{driver}'",
                )

    return list(hints.values())


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def build_inventory() -> Dict[str, Any]:
    pci_devices = collect_pci_devices()
    netdevs = collect_netdevs()
    devlink_devices = collect_devlink_devices()
    devlink_ports = collect_devlink_ports()
    ovs = collect_ovs()
    vendor_hints = detect_vendor_hints(pci_devices["devices"], netdevs["interfaces"])

    return {
        "schema_version": SCHEMA_VERSION,
        "host": collect_host_info(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pci_devices": pci_devices,
        "netdevs": netdevs,
        "devlink_devices": devlink_devices,
        "devlink_ports": devlink_ports,
        "ovs": ovs,
        "vendor_hints": vendor_hints,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a vendor-neutral hardware/network inventory snapshot "
            "of the local Linux host and emit it as JSON."
        )
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Write JSON output to PATH instead of stdout.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output with indentation.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    try:
        inventory = build_inventory()
    except Exception as exc:  # last-resort safety net; collectors should not raise
        print(f"error: inventory collection failed unexpectedly: {exc}", file=sys.stderr)
        return 1

    indent = 2 if args.pretty else None
    text = json.dumps(inventory, indent=indent, sort_keys=False)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as output_file:
                output_file.write(text)
                output_file.write("\n")
        except OSError as exc:
            print(f"error: could not write output file '{args.output}': {exc}", file=sys.stderr)
            return 1
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
