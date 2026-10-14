"""Marvell OCTEON discovery (read-only).

Collects lspci -nn, ip -d link, optional dpdk-devbind.py -s, optional
ovs-vsctl show, and DAO-related env/binaries (existence only).

Hints: Marvell, OCTEON, cn10k, cnxk. Kernel switchdev is not assumed.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..base.commands import command_exists, run
from ..base.models import (
    STATUS_OK,
    STATUS_SKIPPED,
    CommandResult,
    DPUDevice,
    OperationResult,
    PhysicalFunction,
    VendorCapability,
)

LOG = logging.getLogger(__name__)
VENDOR = "marvell"

MARVELL_HINTS = ("marvell", "octeon", "cn10k", "cnxk")

_PCI = re.compile(
    r"(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_IP_HEADER = re.compile(r"^\d+:\s+([^:@]+)(?:@\S+)?:")
PROBE_TIMEOUT = 15.0

DAO_ENV_KEYS = (
    "DAO_PATH",
    "DAO_HOME",
    "DAO_INSTALL",
    "DAO_VERSION",
    "OCTEON_SDK",
    "OCTEON_PATH",
    "RTE_SDK",
    "DPDK_SDK",
    "MARVELL_DAO",
)

DAO_BINARIES = (
    "dpdk-devbind.py",
    "dpdk-devbind",
    "dao-ovs",
    "dao_ovs",
    "ovs-vsctl",
)


def looks_like_marvell(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(hint in lower for hint in MARVELL_HINTS)


def parse_lspci_nn(raw: Optional[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        line = line.strip()
        if not line or not looks_like_marvell(line):
            continue
        pci_match = _PCI.search(line)
        rows.append(
            {
                "pci_address": pci_match.group("pci").lower() if pci_match else None,
                "description": line,
                "model": _model_from_lspci(line),
            }
        )
    return rows


def parse_ip_d_link(raw: Optional[str]) -> List[str]:
    names: List[str] = []
    if not raw:
        return names
    for line in raw.splitlines():
        header = _IP_HEADER.match(line)
        if header and header.group(1) != "lo":
            names.append(header.group(1))
    return names


def scan_dao_environment(
    environ: Optional[Mapping[str, str]] = None,
    *,
    command_exists_fn=None,
) -> Dict[str, Any]:
    """Record DAO/DPDK env vars and whether known binaries exist. No exec."""
    env = environ if environ is not None else os.environ
    found_env = {k: env[k] for k in DAO_ENV_KEYS if env.get(k)}
    exists = command_exists_fn or command_exists
    binaries = {name: bool(exists(name)) for name in DAO_BINARIES}
    return {
        "env": found_env,
        "binaries": binaries,
        "dao_env_present": bool(found_env),
        "dpdk_devbind": binaries.get("dpdk-devbind.py") or binaries.get("dpdk-devbind"),
    }


def build_inventory(
    *,
    lspci: Optional[str] = None,
    ip_link: Optional[str] = None,
    dpdk_devbind: Optional[str] = None,
    ovs: Optional[str] = None,
    dao_scan: Optional[Mapping[str, Any]] = None,
    dpdk_ports: Optional[List[Dict[str, Any]]] = None,
) -> OperationResult:
    from . import dpdk as dpdk_mod

    rows = parse_lspci_nn(lspci)
    ports = dpdk_ports if dpdk_ports is not None else dpdk_mod.parse_devbind(dpdk_devbind)
    scan = dict(dao_scan or {})
    devices: List[DPUDevice] = []
    pfs: List[PhysicalFunction] = []
    for row in rows:
        pci = row["pci_address"]
        cap = VendorCapability(
            vendor=VENDOR,
            sriov=None,
            switchdev=None,
            vdpa=None,
            ovs_hw_offload=None,
            notes="OCTEON hint from lspci; DAO/DPDK not assumed running",
        )
        dev = DPUDevice(
            id=("marvell-%s" % pci) if pci else None,
            vendor=VENDOR,
            model=row["model"],
            serial_number=None,
            firmware_version=None,
            pci_address=pci,
            capabilities=cap,
            vendor_extensions={"lspci": row["description"]},
        )
        devices.append(dev)
        if pci:
            bind = next((p for p in ports if (p.get("pci_address") or "").lower() == pci), None)
            pfs.append(
                PhysicalFunction(
                    id="pf-%s" % pci,
                    dpu_device_id=dev.id,
                    pci_address=pci,
                    netdev=(bind or {}).get("iface"),
                    eswitch_mode=None,
                )
            )

    if not devices and not ports and not scan.get("dao_env_present") and not ovs:
        status = STATUS_SKIPPED
        message = "no Marvell/OCTEON/cn10k/cnxk hints and no DAO/DPDK/OVS evidence"
    else:
        status = STATUS_OK
        message = "Marvell discovery; kernel switchdev not assumed; DAO CLI not executed"

    return OperationResult(
        operation="discover",
        vendor=VENDOR,
        status=status,
        message=message,
        executed=True,
        destructive=False,
        devices=devices,
        physical_functions=pfs,
        representors=[],
        capabilities=VendorCapability(
            vendor=VENDOR,
            switchdev=None,
            ovs_hw_offload=None,
            notes="DAO/DPDK representors likely; generic OVS ≠ DAO offload",
        ),
        details={
            "dpdk_ports": ports,
            "dao": scan,
            "ovs_present": bool(ovs),
            "ip_netdevs": parse_ip_d_link(ip_link),
            "ovs_show_bytes": len(ovs) if ovs else 0,
        },
    )


def collect_probes(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    environ: Optional[Mapping[str, str]] = None,
    command_exists_fn=None,
    runner=None,
) -> Dict[str, Any]:
    from . import dpdk as dpdk_mod

    exists = command_exists_fn or command_exists
    if samples is not None:
        scan = scan_dao_environment(
            samples.get("environ") or environ, command_exists_fn=exists
        )
        return {
            "lspci": samples.get("lspci"),
            "ip_link": samples.get("ip_link"),
            "dpdk_devbind": samples.get("dpdk_devbind"),
            "ovs": samples.get("ovs"),
            "dao_scan": samples.get("dao_scan") or scan,
            "dpdk_ports": dpdk_mod.parse_devbind(samples.get("dpdk_devbind")),
            "commands": [],
        }

    commands: List[CommandResult] = []
    run_fn = runner or run

    def probe(argv: Sequence[str], *, need: str) -> CommandResult:
        if not exists(need):
            result = CommandResult(argv=list(argv), executed=False, reason="command not found")
            commands.append(result)
            return result
        result = run_fn(list(argv), dry_run=dry_run, timeout=PROBE_TIMEOUT)
        commands.append(result)
        return result

    lspci = probe(["lspci", "-nn"], need="lspci")
    ip_link = probe(["ip", "-d", "link", "show"], need="ip")
    bind_cmd = "dpdk-devbind.py" if exists("dpdk-devbind.py") else "dpdk-devbind"
    dpdk_out = probe([bind_cmd, "-s"], need=bind_cmd)
    ovs = probe(["ovs-vsctl", "show"], need="ovs-vsctl")
    scan = scan_dao_environment(environ, command_exists_fn=exists)
    return {
        "lspci": lspci.stdout,
        "ip_link": ip_link.stdout,
        "dpdk_devbind": dpdk_out.stdout,
        "ovs": ovs.stdout,
        "dao_scan": scan,
        "dpdk_ports": dpdk_mod.parse_devbind(dpdk_out.stdout),
        "commands": commands,
    }


def discover(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    **kwargs: Any,
) -> OperationResult:
    packed = collect_probes(samples=samples, dry_run=dry_run, **kwargs)
    result = build_inventory(
        lspci=packed.get("lspci"),
        ip_link=packed.get("ip_link"),
        dpdk_devbind=packed.get("dpdk_devbind"),
        ovs=packed.get("ovs"),
        dao_scan=packed.get("dao_scan"),
        dpdk_ports=packed.get("dpdk_ports"),
    )
    result.commands = list(packed.get("commands") or [])
    return result


def _model_from_lspci(line: str) -> Optional[str]:
    if ":" not in line:
        return None
    desc = line.split(":", 1)[1].strip()
    desc = re.sub(r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]\s*$", "", desc)
    return desc or None
