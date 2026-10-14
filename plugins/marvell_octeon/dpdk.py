"""DPDK device bind status for Marvell OCTEON. No EAL / rte_flow insert."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "marvell"

_PCI = re.compile(
    r"(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_DRV = re.compile(r"drv=(\S+)")
_IFACE = re.compile(r"if=(\S+)")


def parse_devbind(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse `dpdk-devbind.py -s` (or similar) into PCI rows.

    Unknown fields stay None. Does not infer representor netdevs.
    """
    ports: List[Dict[str, Any]] = []
    if not raw:
        return ports
    section = None
    for line in raw.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if "dpdk-compatible driver" in lower or "using dpdk" in lower:
            section = "dpdk"
            continue
        if "kernel driver" in lower:
            section = "kernel"
            continue
        if "other" in lower and "driver" in lower:
            section = "other"
            continue
        pci_match = _PCI.search(stripped)
        if not pci_match:
            continue
        drv_m = _DRV.search(stripped)
        if_m = _IFACE.search(stripped)
        ports.append(
            {
                "pci_address": pci_match.group("pci").lower(),
                "driver": drv_m.group(1) if drv_m else None,
                "iface": None if not if_m or if_m.group(1) == "*" else if_m.group(1),
                "binding": section,
                "raw": stripped,
            }
        )
    return ports


def list_bound_devices(raw: Optional[str] = None) -> OperationResult:
    ports = parse_devbind(raw)
    dpdk_bound = [p for p in ports if p.get("binding") == "dpdk"]
    LOG.info("dpdk parse ports=%s dpdk_bound=%s", len(ports), len(dpdk_bound))
    status = STATUS_OK if ports else STATUS_SKIPPED
    return OperationResult(
        operation="list_dpdk_ports",
        vendor=VENDOR,
        status=status,
        message="parsed dpdk-devbind status; no bind/unbind performed",
        executed=True,
        destructive=False,
        details={"ports": ports, "dpdk_bound": dpdk_bound},
    )


def plan_datapath() -> OperationResult:
    LOG.warning("marvell_octeon.dpdk: will not start DPDK or insert rte_flow")
    return OperationResult(
        operation="dpdk",
        vendor=VENDOR,
        status=STATUS_SKIPPED,
        message="No EAL/rte_flow; representors may be DPDK ports not netdevs",
        destructive=True,
        executed=False,
        flows=[],
        details={"offload_state": None},
    )
