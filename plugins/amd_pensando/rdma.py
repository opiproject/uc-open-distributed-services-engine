"""RDMA facts for ionic_rdma. Optional; missing RDMA is not an Ethernet failure."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
LOG = logging.getLogger(__name__)
VENDOR = "amd"


def parse_lsmod(raw: Optional[str]) -> Dict[str, bool]:
    found = {"ionic": False, "ionic_rdma": False}
    if not raw:
        return found
    for line in raw.splitlines():
        name = line.split()[0] if line.strip() else ""
        if name == "ionic":
            found["ionic"] = True
        elif name == "ionic_rdma":
            found["ionic_rdma"] = True
    return found


def parse_rdma_link(raw: Optional[str]) -> List[Dict[str, Optional[str]]]:
    rows: List[Dict[str, Optional[str]]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("link "):
            continue
        parts = stripped.split()
        dev = parts[1] if len(parts) > 1 else None
        netdev = None
        if "netdev" in parts:
            idx = parts.index("netdev")
            if idx + 1 < len(parts):
                netdev = parts[idx + 1]
        rows.append({"rdma_device": dev, "netdev": netdev, "raw": stripped})
    return rows


def list_rdma_devices(raw: Optional[str] = None) -> OperationResult:
    rows = parse_rdma_link(raw)
    LOG.info("rdma link devices=%s", len(rows))
    return OperationResult(
        operation="list_rdma_devices",
        vendor=VENDOR,
        status=STATUS_OK if rows else STATUS_SKIPPED,
        message="rdma link show parsed" if rows else "no rdma link output",
        executed=True,
        destructive=False,
        details={"devices": rows, "rdma_required_for_ethernet": False},
    )


def detect_ionic_rdma(
    *,
    rdma_link: Optional[str] = None,
    lsmod: Optional[str] = None,
) -> OperationResult:
    modules = parse_lsmod(lsmod)
    rows = parse_rdma_link(rdma_link)
    ionic_links = [
        r
        for r in rows
        if r.get("rdma_device") and "ionic" in (r.get("rdma_device") or "").lower()
    ]
    detected = bool(modules.get("ionic_rdma") or ionic_links)
    LOG.info("ionic_rdma detected=%s module=%s links=%s", detected, modules.get("ionic_rdma"), len(ionic_links))
    return OperationResult(
        operation="detect_ionic_rdma",
        vendor=VENDOR,
        status=STATUS_OK if detected else STATUS_SKIPPED,
        message=(
            "ionic_rdma present"
            if detected
            else "ionic_rdma not found (OK for Ethernet-only discovery)"
        ),
        executed=True,
        details={
            "detected": detected,
            "ionic_rdma_module": modules.get("ionic_rdma"),
            "ionic_rdma_links": ionic_links,
            "rdma_required_for_ovs": False,
        },
    )


def plan_rdma() -> OperationResult:
    return detect_ionic_rdma()
