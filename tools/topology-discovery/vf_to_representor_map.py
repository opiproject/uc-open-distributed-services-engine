#!/usr/bin/env python3
"""vf_to_representor_map.py

Skeleton discovery tool: map SR-IOV VFs to switchdev representors using
sysfs, `ip link`, `devlink`, and `ethtool` when those sources exist.

This is observation-only and conservative. Unknown fields are JSON
`null`. Vendor-specific naming is not used in the core matcher; optional
plugins may fill gaps. Parsing helpers below are pure functions so they
can be unit-tested without hardware.

Usage:
    python3 vf_to_representor_map.py
    python3 vf_to_representor_map.py --pretty
    python3 vf_to_representor_map.py --pf eth0 --output map.json --pretty
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "odse-vf-representor-map/v1alpha1"
DEFAULT_TIMEOUT_SECONDS = 10
SYSFS_NET_DEFAULT = "/sys/class/net"

# phys_port_name shapes seen in-tree (mlx5, ice, others). Conservative:
# only structured tokens, never a free-form interface name heuristic.
_PHYS_PORT_VF = re.compile(
    r"^(?:c(?P<controller>\d+))?p(?:f)?(?P<pf>\d+)vf(?P<vf>\d+)$",
    re.IGNORECASE,
)
# Subfunction (e.g. NVIDIA mlx5 "pf0sf0"). Distinct construct from a VF —
# an SF is not addressed by vf_index — so this is parsed but never fed
# into match_representor's VF matching.
_PHYS_PORT_SF = re.compile(
    r"^(?:c(?P<controller>\d+))?p(?:f)?(?P<pf>\d+)sf(?P<sf>\d+)$",
    re.IGNORECASE,
)
_PHYS_PORT_PF = re.compile(
    r"^(?:c(?P<controller>\d+))?p(?:f)?(?P<pf>\d+)$",
    re.IGNORECASE,
)

_DEVLINK_PORT_LINE = re.compile(r"^(?P<handle>\S+):\s*(?P<attributes>.*)$")
_DEVLINK_PORT_KV = re.compile(r"(\w+)\s+(\S+)")

_PCI_ADDR = re.compile(
    r"(?P<domain>[0-9a-fA-F]{4}):(?P<bus>[0-9a-fA-F]{2}):"
    r"(?P<slot>[0-9a-fA-F]{2})\.(?P<func>[0-9a-fA-F]+)$"
)

# Same shape used by tools/inventory/dpu_inventory.py's parse_lspci, kept
# independent so this tool has no import-time dependency on that module.
_LSPCI_PATTERN = re.compile(
    r"^(?P<slot>\S+)\s+(?P<class_name>.+?)\s+\[(?P<class_id>[0-9a-fA-F]{4})\]:\s+"
    r"(?P<description>.+?)\s+\[(?P<vendor_id>[0-9a-fA-F]{4}):(?P<device_id>[0-9a-fA-F]{4})\]"
    r"(?:\s+\(rev\s+(?P<revision>[0-9a-fA-F]+)\))?\s*$"
)

# Same conservative PCI/driver hints as tools/inventory/dpu_inventory.py.
_VENDOR_PCI_IDS = {
    "amd_pensando": {"1dd8"},
    "nvidia_mellanox_bluefield": {"15b3"},
    "marvell_octeon": {"177d"},
}
_DRIVER_HINTS = {
    "amd_pensando": {"ionic"},
    "nvidia_mellanox_bluefield": {"mlx5_core", "mlx5e"},
    "intel_ipu": {"ice", "idpf"},
    "marvell_octeon": {"octeontx2-nicvf", "octeon_ep", "octeon_ep_vf", "rvu_nicvf"},
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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
            return handle.read().strip() or None
    except OSError:
        return None


def listdir_sorted(path: str) -> List[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Unit-testable parsers
# ---------------------------------------------------------------------------

def normalize_pci_address(raw: Optional[str]) -> Optional[str]:
    """Return canonical `0000:bb:dd.f` or None if not a PCI address."""
    if not raw:
        return None
    text = raw.strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    match = _PCI_ADDR.search(text)
    if not match:
        return None
    return (
        f"{match.group('domain').lower()}:"
        f"{match.group('bus').lower()}:"
        f"{match.group('slot').lower()}."
        f"{match.group('func').lower()}"
    )


def parse_phys_port_name(name: Optional[str]) -> Dict[str, Any]:
    """Parse kernel `phys_port_name` into pf/vf/sf indices.

    Unknown or empty names yield null fields. Does not interpret vendor
    netdev names (eth0v1, enp3s0f0v1, …). Subfunctions (`pf0sf0`,
    NVIDIA-style) are recognized as kind='sf' with sf_index set, but are
    a distinct construct from a VF and are never matched to a vf_index
    by match_representor.
    """
    result: Dict[str, Any] = {
        "raw": name,
        "controller": None,
        "pf_index": None,
        "vf_index": None,
        "sf_index": None,
        "kind": None,
    }
    if not name:
        return result
    vf_match = _PHYS_PORT_VF.match(name.strip())
    if vf_match:
        result["kind"] = "vf"
        result["pf_index"] = int(vf_match.group("pf"))
        result["vf_index"] = int(vf_match.group("vf"))
        if vf_match.group("controller") is not None:
            result["controller"] = int(vf_match.group("controller"))
        return result
    sf_match = _PHYS_PORT_SF.match(name.strip())
    if sf_match:
        result["kind"] = "sf"
        result["pf_index"] = int(sf_match.group("pf"))
        result["sf_index"] = int(sf_match.group("sf"))
        if sf_match.group("controller") is not None:
            result["controller"] = int(sf_match.group("controller"))
        return result
    pf_match = _PHYS_PORT_PF.match(name.strip())
    if pf_match:
        result["kind"] = "pf"
        result["pf_index"] = int(pf_match.group("pf"))
        if pf_match.group("controller") is not None:
            result["controller"] = int(pf_match.group("controller"))
    return result


def parse_ethtool_i(raw: Optional[str]) -> Dict[str, Optional[str]]:
    info: Dict[str, Optional[str]] = {}
    if not raw:
        return info
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip()] = value.strip() or None
    return info


def parse_devlink_port_text(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse plain `devlink port show` lines into dicts."""
    ports: List[Dict[str, Any]] = []
    if not raw:
        return ports
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _DEVLINK_PORT_LINE.match(line)
        if not match:
            ports.append({"raw_line": line, "parse_error": True})
            continue
        attributes = dict(_DEVLINK_PORT_KV.findall(match.group("attributes")))
        ports.append(_normalize_devlink_port(match.group("handle"), attributes))
    return ports


def parse_devlink_port_json(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse `devlink -j port show` JSON. Invalid JSON returns []."""
    if not raw or not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    port_map = payload.get("port")
    if not isinstance(port_map, dict):
        return []
    ports: List[Dict[str, Any]] = []
    for handle, attrs in port_map.items():
        if not isinstance(attrs, dict):
            continue
        flat = {str(k): v for k, v in attrs.items()}
        ports.append(_normalize_devlink_port(handle, flat))
    return ports


def _normalize_devlink_port(handle: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
    flavour = _as_str(attributes.get("flavour"))
    netdev = _as_str(attributes.get("netdev"))
    pci = None
    if handle.startswith("pci/"):
        parts = handle.split("/")
        if len(parts) >= 2:
            pci = normalize_pci_address(parts[1])
    return {
        "handle": handle,
        "netdev": netdev,
        "flavour": flavour,
        "pfnum": _as_int(attributes.get("pfnum")),
        "vfnum": _as_int(attributes.get("vfnum")),
        "sfnum": _as_int(attributes.get("sfnum")),
        "pci_address": pci,
        "attributes": attributes,
    }


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_lspci(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse `lspci -nn` output into device dicts (same shape as
    tools/inventory/dpu_inventory.py's parse_lspci). Unparseable lines
    are preserved as {"raw_line": ..., "parse_error": True}, never
    silently dropped.
    """
    devices: List[Dict[str, Any]] = []
    if not raw:
        return devices
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


def lspci_slot_to_pci_address(slot: Optional[str]) -> Optional[str]:
    """`lspci -nn` slots omit the domain when it is 0000 (e.g. '03:00.1'
    instead of '0000:03:00.1'). Normalize both forms; return None if slot
    does not look like a PCI address at all.
    """
    if not slot:
        return None
    text = slot.strip()
    if normalize_pci_address(text):
        return normalize_pci_address(text)
    return normalize_pci_address("0000:" + text)


def index_lspci_by_pci(devices: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_pci: Dict[str, Dict[str, Any]] = {}
    for device in devices:
        if device.get("parse_error"):
            continue
        pci = lspci_slot_to_pci_address(device.get("slot"))
        if pci:
            by_pci[pci] = device
    return by_pci


_IP_LINK_ATTR = re.compile(
    r"\b(?P<key>parentdev|portname|phys_port_name|switchid|phys_switch_id)"
    r"\s+(?P<value>\S+)"
)


def parse_ip_link_phys(raw: Optional[str]) -> Dict[str, Dict[str, Optional[str]]]:
    """Extract name -> phys_port_name / phys_switch_id from `ip -d link show`.

    Real `ip -d link show` packs attribute/value pairs onto the same
    physical line as `link/ether ...` (confirmed on real E2100 host-4
    hardware -- see example-output/host-4/Step1.txt and
    plugins/intel_ipu/discovery.py), not one attribute per line. Scan
    each interface's whole block for known tokens instead of requiring
    them at the start of their own line; this also still matches the
    one-attribute-per-line style some captures use. `switchid` and
    `phys_switch_id` are treated as the same attribute (iproute2 version
    difference).
    """
    by_name: Dict[str, Dict[str, Optional[str]]] = {}
    current: Optional[str] = None
    header = re.compile(r"^\d+:\s+([^:@]+)(?:@\S+)?:")
    if not raw:
        return by_name
    for line in raw.splitlines():
        header_match = header.match(line)
        if header_match:
            current = header_match.group(1)
            by_name[current] = {
                "phys_port_name": None,
                "phys_switch_id": None,
                "portname": None,
                "parentdev": None,
            }
            continue
        if current is None:
            continue
        for match in _IP_LINK_ATTR.finditer(line):
            key = match.group("key")
            value = match.group("value").strip() or None
            entry = by_name[current]
            if key == "parentdev" and not entry.get("parentdev"):
                entry["parentdev"] = value
            elif key == "phys_port_name" and not entry.get("phys_port_name"):
                entry["phys_port_name"] = value
            elif key == "portname" and not entry.get("portname"):
                entry["portname"] = value
                if not entry.get("phys_port_name"):
                    entry["phys_port_name"] = value
            elif key in ("switchid", "phys_switch_id") and not entry.get(
                "phys_switch_id"
            ):
                entry["phys_switch_id"] = value
    return by_name


def vendor_hint_from_ids(
    pci_vendor_id: Optional[str],
    driver: Optional[str],
    lspci_vendor_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a single conservative vendor hint, or None.

    lspci_vendor_id is a fallback signal only (e.g. when sysfs `vendor`
    is unreadable but `lspci -nn` still resolved a [vendor:device] id
    for the same PCI address) — it is checked last and after the driver
    hint so a stronger sysfs/driver signal always wins.
    """
    vid = (pci_vendor_id or "").lower().replace("0x", "")
    for vendor, ids in _VENDOR_PCI_IDS.items():
        if vid and vid in ids:
            return {
                "vendor": vendor,
                "confidence": "high",
                "evidence": [f"PCI vendor ID {vid}"],
            }
    driver_name = driver or ""
    for vendor, drivers in _DRIVER_HINTS.items():
        if driver_name in drivers:
            confidence = "low" if vendor == "intel_ipu" else "medium"
            return {
                "vendor": vendor,
                "confidence": confidence,
                "evidence": [f"ethtool driver '{driver_name}'"],
            }
    lvid = (lspci_vendor_id or "").lower().replace("0x", "")
    for vendor, ids in _VENDOR_PCI_IDS.items():
        if lvid and lvid in ids:
            return {
                "vendor": vendor,
                "confidence": "medium",
                "evidence": [f"lspci vendor ID {lvid} (sysfs vendor unavailable)"],
            }
    return None


def match_representor(
    vf_index: int,
    pf_pci: Optional[str],
    pf_phys_switch_id: Optional[str],
    devlink_ports: List[Dict[str, Any]],
    ip_phys: Dict[str, Dict[str, Optional[str]]],
) -> Dict[str, Any]:
    """Match a VF to a representor netdev.

    Preference: devlink flavour=pcivf + vfnum (+ PCI if present).
    Fallback: phys_port_name vf index + same phys_switch_id as the PF.
    No netdev-name guessing. On failure, `reason` and
    `troubleshooting_hints` explain *why*, distinguishing "no data
    source available" from "data available but no match found" so a
    user knows whether to install a tool or investigate the eswitch.
    """
    devlink_pcivf_ports = [p for p in devlink_ports if p.get("flavour") == "pcivf"]
    candidate_vfnums = sorted(
        {p.get("vfnum") for p in devlink_pcivf_ports if p.get("vfnum") is not None}
    )

    for port in devlink_pcivf_ports:
        if port.get("vfnum") != vf_index:
            continue
        port_pci = port.get("pci_address")
        if pf_pci and port_pci and port_pci != pf_pci:
            continue
        return {
            "representor_netdev": port.get("netdev"),
            "mapping_method": "devlink_pcivf",
            "mapping_confidence": "high" if port.get("netdev") else "medium",
            "devlink_handle": port.get("handle"),
            "reason": None,
            "troubleshooting_hints": [],
        }

    phys_port_candidates: List[str] = []
    for netdev, attrs in ip_phys.items():
        parsed = parse_phys_port_name(attrs.get("phys_port_name"))
        if parsed.get("kind") != "vf" or parsed.get("vf_index") != vf_index:
            continue
        phys_port_candidates.append(netdev)
        switch_id = attrs.get("phys_switch_id")
        if pf_phys_switch_id and switch_id and switch_id != pf_phys_switch_id:
            continue
        if pf_phys_switch_id and not switch_id:
            continue
        return {
            "representor_netdev": netdev,
            "mapping_method": "phys_port_name",
            "mapping_confidence": "medium" if pf_phys_switch_id else "low",
            "devlink_handle": None,
            "reason": None,
            "troubleshooting_hints": [],
        }

    reason, hints = _no_representor_reason(
        vf_index=vf_index,
        devlink_available=bool(devlink_ports),
        candidate_vfnums=candidate_vfnums,
        ip_phys_available=bool(ip_phys),
        phys_port_candidates=phys_port_candidates,
        pf_phys_switch_id=pf_phys_switch_id,
    )
    return {
        "representor_netdev": None,
        "mapping_method": None,
        "mapping_confidence": None,
        "devlink_handle": None,
        "reason": reason,
        "troubleshooting_hints": hints,
    }


def _no_representor_reason(
    *,
    vf_index: int,
    devlink_available: bool,
    candidate_vfnums: List[int],
    ip_phys_available: bool,
    phys_port_candidates: List[str],
    pf_phys_switch_id: Optional[str],
) -> Tuple[str, List[str]]:
    """Explain why match_representor found nothing for this VF. Never
    guesses a representor name; only diagnoses the absence of one.
    """
    if not devlink_available and not ip_phys_available:
        return (
            "no data source available: `devlink` and `ip -d link show` "
            "both returned no usable output",
            [
                "install iproute2 and iproute2-tc / devlink (part of iproute2) tools",
                "run as a user/context with CAP_NET_ADMIN, or with sudo",
                "confirm this is being run on the node that owns the eswitch "
                "(often the DPU OS for off-path models, not the hypervisor)",
            ],
        )
    if devlink_available and vf_index not in candidate_vfnums:
        return (
            "devlink port show returned pcivf ports, but none with vfnum=%d"
            % vf_index,
            [
                "confirm the PF actually has this many VFs (sriov_numvfs)",
                "confirm switchdev/eswitch mode is enabled on this PF "
                "(ansible/roles/switchdev_setup)",
                "re-run `devlink port show` manually and check for a "
                "flavour pcivf entry with this vfnum",
            ],
        )
    if phys_port_candidates and pf_phys_switch_id:
        return (
            "found phys_port_name candidate(s) %s for vf_index=%d, but "
            "phys_switch_id did not match the PF's switch id"
            % (phys_port_candidates, vf_index),
            [
                "confirm the candidate netdev and the PF are on the same "
                "eswitch (same phys_switch_id)",
                "the candidate may belong to a different PF/eswitch instance",
            ],
        )
    if not ip_phys_available:
        return (
            "devlink had no match for vf_index=%d, and `ip -d link show` "
            "returned no usable output for a phys_port_name fallback" % vf_index,
            ["install iproute2", "run with sufficient privileges to see phys_port_name"],
        )
    return (
        "no devlink pcivf port and no phys_port_name of the form "
        "pf<N>vf<%d> / p<N>vf<%d> found" % (vf_index, vf_index),
        [
            "confirm switchdev/eswitch mode is enabled for this PF",
            "confirm the representor exists at all: `ip -d link show` on "
            "the node that owns the eswitch",
            "some vendors (see docs/operations/vf-to-representor-mapping.md) "
            "require a vendor-specific discovery path not covered by core "
            "sysfs/ip/devlink parsing",
        ],
    )


def empty_mapping() -> Dict[str, Any]:
    return {
        "pf_netdev": None,
        "pf_pci_address": None,
        "vf_index": None,
        "vf_pci_address": None,
        "vf_netdev": None,
        "representor_netdev": None,
        "dpu_host_identity": None,
        "vendor_hint": None,
        "mapping_method": None,
        "mapping_confidence": None,
        "phys_port_name": None,
        "phys_switch_id": None,
        "devlink_handle": None,
        "lspci_description": None,
        "no_representor_reason": None,
        "troubleshooting_hints": [],
    }


# ---------------------------------------------------------------------------
# Discovery (sysfs + commands)
# ---------------------------------------------------------------------------

def collect_devlink_ports(timeout: int) -> List[Dict[str, Any]]:
    json_result = run_command(["devlink", "-j", "port", "show"], timeout=timeout)
    ports = parse_devlink_port_json(json_result.get("stdout"))
    if ports:
        return ports
    text_result = run_command(["devlink", "port", "show"], timeout=timeout)
    return parse_devlink_port_text(text_result.get("stdout"))


def pci_vendor_id(sysfs_pci_path: str) -> Optional[str]:
    raw = read_sysfs(os.path.join(sysfs_pci_path, "vendor"))
    if not raw:
        return None
    return raw.lower().replace("0x", "")


def vf_netdev_from_virtfn(virtfn_path: str) -> Optional[str]:
    names = listdir_sorted(os.path.join(virtfn_path, "net"))
    if not names:
        return None
    return names[0]


def discover_mappings(
    *,
    sysfs_net: str,
    pf_filter: Optional[List[str]],
    pf_pci_filter: Optional[str],
    vendor_override: Optional[str],
    timeout: int,
) -> Dict[str, Any]:
    ip_result = run_command(["ip", "-d", "link", "show"], timeout=timeout)
    ip_phys = parse_ip_link_phys(ip_result.get("stdout"))
    devlink_ports = collect_devlink_ports(timeout)
    lspci_result = run_command(["lspci", "-nn"], timeout=timeout)
    lspci_devices = parse_lspci(lspci_result.get("stdout"))
    lspci_by_pci = index_lspci_by_pci(lspci_devices)

    pf_pci_filter_norm = normalize_pci_address(pf_pci_filter) if pf_pci_filter else None

    sources = {
        "sysfs_net": sysfs_net,
        "ip_link": {
            "available": ip_result.get("available"),
            "error": ip_result.get("error"),
        },
        "devlink_port": {"count": len(devlink_ports)},
        "lspci": {
            "available": lspci_result.get("available"),
            "error": lspci_result.get("error"),
            "count": len(lspci_by_pci),
        },
    }

    def with_vendor_override(hint: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if hint is not None or not vendor_override:
            return hint
        return {
            "vendor": vendor_override,
            "confidence": "user_specified",
            "evidence": ["--vendor flag"],
        }

    mappings: List[Dict[str, Any]] = []
    ifaces = listdir_sorted(sysfs_net)
    for pf_name in ifaces:
        if pf_filter is not None and pf_name not in pf_filter:
            continue
        pf_dir = os.path.join(sysfs_net, pf_name)
        numvfs_path = os.path.join(pf_dir, "device", "sriov_numvfs")
        if not os.path.exists(numvfs_path):
            continue
        device_link = os.path.join(pf_dir, "device")
        try:
            pf_pci_path = os.path.realpath(device_link)
        except OSError:
            pf_pci_path = device_link
        pf_pci = normalize_pci_address(os.path.basename(pf_pci_path))
        if pf_pci_filter_norm and pf_pci != pf_pci_filter_norm:
            continue
        pf_switch = (ip_phys.get(pf_name) or {}).get("phys_switch_id")
        ethtool = run_command(["ethtool", "-i", pf_name], timeout=timeout)
        ethtool_info = parse_ethtool_i(ethtool.get("stdout"))
        pf_lspci = lspci_by_pci.get(pf_pci or "")
        hint = with_vendor_override(
            vendor_hint_from_ids(
                pci_vendor_id(pf_pci_path),
                ethtool_info.get("driver"),
                lspci_vendor_id=(pf_lspci or {}).get("vendor_id"),
            )
        )

        virtfns = [
            name
            for name in listdir_sorted(os.path.join(pf_dir, "device"))
            if name.startswith("virtfn")
        ]
        if not virtfns:
            # PF with sriov_numvfs but no virtfn yet — still emit a null VF row.
            row = empty_mapping()
            row["pf_netdev"] = pf_name
            row["pf_pci_address"] = pf_pci
            row["vendor_hint"] = hint
            row["lspci_description"] = (pf_lspci or {}).get("description")
            mappings.append(row)
            continue

        for virtfn in virtfns:
            index_raw = virtfn[len("virtfn") :]
            vf_index = _as_int(index_raw)
            virtfn_path = os.path.realpath(os.path.join(pf_dir, "device", virtfn))
            vf_pci = normalize_pci_address(os.path.basename(virtfn_path))
            vf_netdev = vf_netdev_from_virtfn(virtfn_path)
            vf_lspci = lspci_by_pci.get(vf_pci or "") or pf_lspci
            match = match_representor(
                vf_index if vf_index is not None else -1,
                pf_pci,
                pf_switch,
                devlink_ports,
                ip_phys,
            )
            repr_name = match.get("representor_netdev")
            repr_phys = ip_phys.get(repr_name or "") or {}
            row = empty_mapping()
            row.update(
                {
                    "pf_netdev": pf_name,
                    "pf_pci_address": pf_pci,
                    "vf_index": vf_index,
                    "vf_pci_address": vf_pci,
                    "vf_netdev": vf_netdev,
                    "representor_netdev": repr_name,
                    "dpu_host_identity": None,
                    "vendor_hint": hint,
                    "mapping_method": match.get("mapping_method"),
                    "mapping_confidence": match.get("mapping_confidence"),
                    "phys_port_name": repr_phys.get("phys_port_name"),
                    "phys_switch_id": repr_phys.get("phys_switch_id") or pf_switch,
                    "devlink_handle": match.get("devlink_handle"),
                    "lspci_description": (vf_lspci or {}).get("description"),
                    "no_representor_reason": match.get("reason"),
                    "troubleshooting_hints": match.get("troubleshooting_hints") or [],
                }
            )
            mappings.append(row)

    return {
        "sources": sources,
        "mappings": mappings,
        "ip_phys": ip_phys,
        "devlink_ports": devlink_ports,
    }


def apply_plugins(
    plugins: List[Any],
    document: Dict[str, Any],
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """raw carries parsed-but-not-serialized discovery data (full ip_phys /
    devlink_ports, not just the per-mapping summary already in `document`)
    so a plugin can look up context a single mapping row does not carry
    (e.g. an SF's phys_port_name has no vf_index to key off of). raw is
    never written into the final document.
    """
    context = {"report": document, "raw": raw or {}}
    plugin_section: Dict[str, Any] = {
        "loaded": [getattr(p, "name", p.__class__.__name__) for p in plugins],
        "results": {},
    }
    mappings = document.get("mappings") or []
    annotated: List[Dict[str, Any]] = []
    for mapping in mappings:
        current = mapping
        for plugin in plugins:
            try:
                current = plugin.annotate_mapping(current, context) or current
            except Exception:
                continue
        annotated.append(current)
    document["mappings"] = annotated
    for plugin in plugins:
        name = getattr(plugin, "name", plugin.__class__.__name__)
        try:
            extra = plugin.extra_sections(context) or {}
        except Exception as exc:
            extra = {"error": f"plugin raised: {exc}"}
        plugin_section["results"][name] = extra
    document["plugins"] = plugin_section
    return document


def build_document(
    *,
    sysfs_net: str,
    pf_filter: Optional[List[str]],
    pf_pci_filter: Optional[str] = None,
    vendor_override: Optional[str] = None,
    timeout: int,
    plugins: List[Any],
    mode: str,
) -> Dict[str, Any]:
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = None

    discovered = discover_mappings(
        sysfs_net=sysfs_net,
        pf_filter=pf_filter,
        pf_pci_filter=pf_pci_filter,
        vendor_override=vendor_override,
        timeout=timeout,
    )
    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "local_host": {
            "hostname": hostname,
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "note": (
            "dpu_host_identity is null in core discovery: this process "
            "cannot tell an off-path DPU OS from the hypervisor. A vendor "
            "plugin may set it. representor_netdev is null unless devlink "
            "flavour=pcivf or phys_port_name matched conservatively."
        ),
        "sources": discovered["sources"],
        "mappings": discovered["mappings"],
        "plugins": {"loaded": [], "results": {}},
    }
    raw = {"ip_phys": discovered.get("ip_phys"), "devlink_ports": discovered.get("devlink_ports")}
    return apply_plugins(plugins, document, raw=raw)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_optional_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def _command_as_dict(cmd: Any) -> Dict[str, Any]:
    return {
        "argv": getattr(cmd, "argv", None),
        "executed": getattr(cmd, "executed", False),
        "dry_run": getattr(cmd, "dry_run", True),
        "returncode": getattr(cmd, "returncode", None),
        "reason": getattr(cmd, "reason", None),
    }


def collect_dpu_samples_via_ssh(
    config: Dict[str, Any], *, dry_run: bool = True
) -> Tuple[Dict[str, Optional[str]], List[Dict[str, Any]]]:
    """Plan or run DPU-side ip/devlink/ovs probes. Default dry_run=True."""
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from plugins.base.remote import RemoteCommandRunner, ssh_params

    params = ssh_params(config)
    runner = RemoteCommandRunner()
    host, user = params.get("host"), params.get("user")
    samples: Dict[str, Optional[str]] = {
        "ip_link": None,
        "devlink_port": None,
        "ovs": None,
    }
    commands: List[Dict[str, Any]] = []
    if not host or not user:
        return samples, commands
    probes = (
        ("ip_link", ["ip", "-d", "link", "show"]),
        ("devlink_port", ["devlink", "port", "show"]),
        ("ovs", ["ovs-vsctl", "show"]),
    )
    command_timeout = params.get("command_timeout_seconds") or 30.0
    for key, argv in probes:
        result = runner.run_ssh(
            host,
            user,
            argv,
            key_path=params.get("key_path"),
            port=int(params.get("port") or 22),
            dry_run=dry_run,
            timeout=command_timeout,
            sudo=bool(params.get("sudo")),
            reason="dpu representor mapping: %s" % " ".join(argv),
        )
        commands.append(_command_as_dict(result))
        if result.executed:
            samples[key] = result.stdout
    return samples, commands


def build_dpu_document(
    *,
    config: Optional[Dict[str, Any]],
    ip_link: Optional[str],
    devlink_port: Optional[str],
    ovs_show: Optional[str] = None,
    commands: Optional[List[Dict[str, Any]]] = None,
    pf_pci: Optional[str] = None,
) -> Dict[str, Any]:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from plugins.base.vm2vm_config import (
        host_vf_pci_by_index,
        representor_mapping_settings,
        vf_indexes_from_config,
    )
    from plugins.nvidia_bluefield.discovery import map_dpu_vf_representors

    settings = representor_mapping_settings(config)
    dpu = (config or {}).get("dpu") or {}
    pci = pf_pci or dpu.get("pf_pci")
    if pci and str(pci).startswith("<"):
        pci = None
    document = map_dpu_vf_representors(
        ip_link=ip_link,
        devlink_port=devlink_port,
        vf_indexes=vf_indexes_from_config(config),
        pf_pci=pci,
        host_vf_pci=host_vf_pci_by_index(config),
        ovs_show=ovs_show,
        strategy=settings.get("strategy") or "nvidia-portname",
    )
    document["mode"] = "discovery"
    document["timestamp"] = datetime.now(timezone.utc).isoformat()
    document["require_high_confidence"] = settings.get("require_high_confidence")
    document["commands"] = commands or []
    document["sources"] = {
        "ip_-d_link_show": bool(ip_link),
        "devlink_port_show": bool(devlink_port),
        "ovs_vsctl_show": bool(ovs_show),
        "location": "dpu",
    }
    return document


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover SR-IOV VF to representor mappings (JSON). "
            "Unknown fields are null. Default mode is discovery."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("discovery",),
        default="discovery",
        help="Run mode. Only 'discovery' is implemented (default).",
    )
    parser.add_argument(
        "--pf",
        action="append",
        dest="pfs",
        default=None,
        metavar="IFACE",
        help="Limit discovery to this PF netdev. Repeatable. Default: all SR-IOV PFs.",
    )
    parser.add_argument(
        "--pf-pci",
        dest="pf_pci",
        default=None,
        metavar="PCI_ADDRESS",
        help="Limit discovery to the PF at this PCI address (0000:bb:dd.f). "
        "Combines with --pf (both must match when both are given).",
    )
    parser.add_argument(
        "--vendor",
        dest="vendor",
        default=None,
        metavar="VENDOR",
        help="Vendor hint override (e.g. nvidia_mellanox_bluefield, "
        "intel_ipu, marvell_octeon, amd_pensando). Used only as a fallback "
        "when PCI-ID/driver heuristics found nothing; confidence is "
        "reported as 'user_specified', never silently promoted to 'high'.",
    )
    parser.add_argument(
        "--sysfs-net",
        default=SYSFS_NET_DEFAULT,
        help=f"sysfs net class path (default {SYSFS_NET_DEFAULT}).",
    )
    parser.add_argument("-o", "--output", metavar="PATH", help="Write JSON to PATH.")
    parser.add_argument("--pretty", action="store_true", help="Indent JSON.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Explicit no-op: output is always JSON today. Accepted for "
        "consistency with other ODSE tools and forward compatibility.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 3 if any discovered VF has no representor_netdev "
        "mapping (representor_netdev is null). Does not affect PFs with "
        "zero VFs or the JSON document itself.",
    )
    parser.add_argument(
        "--plugin-dir",
        action="append",
        dest="plugin_dirs",
        default=[],
        metavar="DIR",
        help="Extra directory of vendor plugin modules (subclassing "
        "VendorPlugin). Repeatable.",
    )
    parser.add_argument(
        "--no-bundled-plugins",
        action="store_true",
        help="Skip the bundled vendor_plugins/ scaffolds next to this tool.",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="VM-to-VM lab YAML. Required for --location dpu unless sample files are given.",
    )
    parser.add_argument(
        "--location",
        choices=("dpu", "host"),
        default=None,
        help="Where representors live. Default: dpu when nvidia.mode=dpu, else host.",
    )
    parser.add_argument(
        "--model",
        choices=("netdev", "dpdk"),
        default=None,
        help="netdev (Linux representors) or dpdk (Marvell DAO ports). Default from config.",
    )
    parser.add_argument(
        "--devbind-file",
        metavar="PATH",
        help="Fixture `dpdk-devbind.py -s` text (Marvell DPDK mapping).",
    )
    parser.add_argument(
        "--dao-port-list-file",
        metavar="PATH",
        help="Fixture DAO/DPDK representor list with explicit vf_index.",
    )
    parser.add_argument(
        "--ip-link-file",
        metavar="PATH",
        help="Fixture/sample `ip -d link show` text (skips DPU SSH).",
    )
    parser.add_argument(
        "--devlink-file",
        metavar="PATH",
        help="Fixture/sample `devlink port show` text (skips DPU SSH).",
    )
    parser.add_argument(
        "--ovs-show-file",
        metavar="PATH",
        help="Optional fixture/sample `ovs-vsctl show` text.",
    )
    return parser.parse_args(argv)


def _load_plugins(args: argparse.Namespace) -> List[Any]:
    # Package is named vendor_plugins (not "plugins") on purpose: a bare
    # "plugins" package here would collide with the repo-root plugins/
    # package under pytest's default collection (pytest imports any
    # __init__.py-bearing directory it walks past under its bare name),
    # breaking `from plugins.base... import ...` in other test modules
    # whenever both are collected in the same pytest session.
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
    config: Optional[Dict[str, Any]] = None
    location = args.location
    if args.config:
        root = _repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from plugins.base.vm2vm_config import load_vm2vm_config, representor_mapping_settings

        try:
            config = load_vm2vm_config(args.config)
        except (OSError, ValueError) as exc:
            print(f"error: failed to load --config {args.config}: {exc}", file=sys.stderr)
            return 1
        if location is None:
            location = representor_mapping_settings(config)["location"]
        if getattr(args, "model", None) is None:
            args.model = representor_mapping_settings(config).get("model")
    if location is None:
        location = "host"
    model = getattr(args, "model", None) or "netdev"
    vendor_hint = (args.vendor or "").replace("_", "-")
    if config and not vendor_hint:
        vendor_hint = str(((config.get("vendor") or {}).get("type") or ""))

    if model == "dpdk" or vendor_hint in ("marvell-octeon", "marvell"):
        root = _repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from plugins.marvell_octeon.dpdk_representor_map import map_dpdk_representors, mapping_document

        samples = {
            "dpdk_devbind": _read_optional_file(getattr(args, "devbind_file", None)),
            "dao_port_list": _read_optional_file(getattr(args, "dao_port_list_file", None)),
        }
        result = map_dpdk_representors(config=config, samples=samples)
        document = mapping_document(result)
        text = json.dumps(document, indent=2 if args.pretty else None, sort_keys=False)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            except OSError as exc:
                print(f"error: could not write '{args.output}': {exc}", file=sys.stderr)
                return 1
        else:
            print(text)
        if args.strict:
            unmapped = [
                m
                for m in document.get("mappings") or []
                if m.get("vf_index") is not None and not m.get("representor_name")
            ]
            if unmapped:
                print(
                    f"error: --strict: {len(unmapped)} VF(s) have no DPDK representor",
                    file=sys.stderr,
                )
                return 3
        return 0

    if location == "dpu":
        ip_link = _read_optional_file(args.ip_link_file)
        devlink_port = _read_optional_file(args.devlink_file)
        ovs_show = _read_optional_file(args.ovs_show_file)
        commands: List[Dict[str, Any]] = []
        if ip_link is None and devlink_port is None:
            if config is None:
                print(
                    "error: --location dpu requires --config or --ip-link-file/--devlink-file",
                    file=sys.stderr,
                )
                return 2
            remote = (config.get("remote_execution") or {}) if config else {}
            dry_run = bool(remote.get("dry_run_default", True))
            samples, commands = collect_dpu_samples_via_ssh(config, dry_run=dry_run)
            ip_link = samples.get("ip_link")
            devlink_port = samples.get("devlink_port")
            ovs_show = ovs_show or samples.get("ovs")
        try:
            document = build_dpu_document(
                config=config,
                ip_link=ip_link,
                devlink_port=devlink_port,
                ovs_show=ovs_show,
                commands=commands,
                pf_pci=args.pf_pci,
            )
        except Exception as exc:
            print(f"error: DPU representor mapping failed: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            plugins = _load_plugins(args)
        except Exception as exc:
            print(f"warning: plugin load failed ({exc}); continuing", file=sys.stderr)
            plugins = []

        try:
            document = build_document(
                sysfs_net=args.sysfs_net,
                pf_filter=args.pfs,
                pf_pci_filter=args.pf_pci,
                vendor_override=args.vendor,
                timeout=args.timeout,
                plugins=plugins,
                mode=args.mode,
            )
        except Exception as exc:
            print(f"error: discovery failed unexpectedly: {exc}", file=sys.stderr)
            return 1
        document["location"] = "host"

    text = json.dumps(document, indent=2 if args.pretty else None, sort_keys=False)
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

    if args.strict:
        unmapped = [
            m
            for m in document.get("mappings") or []
            if m.get("vf_index") is not None and not m.get("representor_netdev")
        ]
        if unmapped:
            print(
                f"error: --strict: {len(unmapped)} VF(s) have no representor_netdev "
                "(see no_representor_reason / troubleshooting_hints per row)",
                file=sys.stderr,
            )
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
