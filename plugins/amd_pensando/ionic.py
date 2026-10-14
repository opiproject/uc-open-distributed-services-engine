"""ionic Ethernet driver facts. Stats are not OVS/hardware-offload proof."""

from __future__ import annotations

import logging
from typing import Dict, List, Mapping, Optional, Sequence

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult, PhysicalFunction, TelemetrySample

LOG = logging.getLogger(__name__)
VENDOR = "amd"


def parse_ethtool_i(raw: Optional[str]) -> Dict[str, Optional[str]]:
    info: Dict[str, Optional[str]] = {}
    if not raw:
        return info
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip().lower()] = value.strip() or None
    return info


def is_ionic_ethtool(raw: Optional[str]) -> bool:
    info = parse_ethtool_i(raw)
    driver = (info.get("driver") or "").lower()
    return driver == "ionic" or driver.startswith("ionic")


def parse_ethtool_s(raw: Optional[str]) -> Dict[str, float]:
    """Parse ethtool -S. Only keys with numeric values; no invented zeros."""
    stats: Dict[str, float] = {}
    if not raw:
        return stats
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("nic statistics"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip().rstrip(",")
        try:
            stats[key.strip()] = float(value)
        except ValueError:
            continue
    return stats


def list_ionic_interfaces(
    ethtool_i: Optional[Mapping[str, str]] = None,
    *,
    ip_names: Optional[Sequence[str]] = None,
) -> List[str]:
    """Interfaces whose ethtool -i driver is ionic. Names are not guessed."""
    names: List[str] = []
    for iface, raw in (ethtool_i or {}).items():
        if is_ionic_ethtool(raw):
            names.append(iface)
    if ethtool_i:
        return sorted(names)
    return sorted(n for n in (ip_names or []) if n)  # without ethtool, do not claim ionic


def get_ionic_driver_info(interface: str, raw: Optional[str] = None) -> OperationResult:
    info = parse_ethtool_i(raw)
    ionic = is_ionic_ethtool(raw)
    LOG.info("ionic driver info %s ionic=%s", interface, ionic)
    return OperationResult(
        operation="get_ionic_driver_info",
        vendor=VENDOR,
        status=STATUS_OK if ionic else STATUS_SKIPPED,
        message=("driver=ionic" if ionic else "not ionic; generic NIC is not a DSC"),
        executed=True,
        physical_functions=[
            PhysicalFunction(
                netdev=interface,
                pci_address=(info.get("bus-info") or None),
                eswitch_mode=None,
            )
        ],
        details={"interface": interface, "ethtool_i": info, "ionic": ionic},
    )


def get_ionic_stats(interface: str, raw: Optional[str] = None) -> OperationResult:
    stats = parse_ethtool_s(raw)
    samples = [
        TelemetrySample(
            source_object_id=interface,
            source_kind="netdev",
            metric_name=key,
            value=val,
            unit="count",
        )
        for key, val in stats.items()
    ]
    LOG.info("ionic stats %s keys=%s (not offload proof)", interface, list(stats)[:8])
    return OperationResult(
        operation="get_ionic_stats",
        vendor=VENDOR,
        status=STATUS_OK if stats else STATUS_SKIPPED,
        message="ethtool -S parsed; not OVS/hw-offload evidence",
        executed=True,
        telemetry=samples,
        details={"interface": interface, "stat_names": sorted(stats.keys())},
    )


def plan_discover_ionic() -> OperationResult:
    return list_ionic_as_result({})


def list_ionic_as_result(ethtool_i: Mapping[str, str]) -> OperationResult:
    names = list_ionic_interfaces(ethtool_i)
    return OperationResult(
        operation="list_ionic_interfaces",
        vendor=VENDOR,
        status=STATUS_OK if names else STATUS_SKIPPED,
        message="ionic netdevs=%s" % names,
        executed=True,
        details={"interfaces": names, "driver": "ionic"},
    )


def pf_placeholder() -> PhysicalFunction:
    return PhysicalFunction(netdev=None, pci_address=None, eswitch_mode=None)
