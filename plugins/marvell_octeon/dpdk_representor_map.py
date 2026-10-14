"""Marvell DPDK/DAO representor mapping. Discovery only; no DPDK rebind.

Linux netdev representors are not assumed. Mapping is high-confidence
only when a DAO/DPDK source explicitly names vf_index.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
from . import discovery, dpdk

LOG = logging.getLogger(__name__)
VENDOR = "marvell"

_VF = re.compile(r"\bvf(?:_index)?[=:]?\s*(?P<vf>\d+)\b", re.IGNORECASE)
_PORT = re.compile(r"\b(?:port(?:_id)?|dpdk_port)[=:]?\s*(?P<port>\S+)", re.IGNORECASE)
_NAME = re.compile(r"\b(?:name|representor)[=:]?\s*(?P<name>\S+)", re.IGNORECASE)
_PCI = re.compile(
    r"(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)


def parse_dao_port_list(raw: Optional[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        vf_m = _VF.search(stripped)
        port_m = _PORT.search(stripped)
        name_m = _NAME.search(stripped)
        pci_m = _PCI.search(stripped)
        if not (vf_m or port_m or name_m or pci_m):
            continue
        rows.append(
            {
                "vf_index": int(vf_m.group("vf")) if vf_m else None,
                "dpdk_port_id": port_m.group("port") if port_m else None,
                "representor_name": name_m.group("name") if name_m else None,
                "vf_pci": pci_m.group("pci").lower() if pci_m else None,
                "raw": stripped,
            }
        )
    return rows


def map_dpdk_representors(
    *,
    config: Optional[Dict[str, Any]] = None,
    samples: Optional[Mapping[str, Any]] = None,
    vf_indexes: Optional[List[int]] = None,
) -> OperationResult:
    """Map VF index → DPDK representor only when the source is explicit."""
    cfg = config or {}
    samples = dict(samples or {})
    wanted = list(vf_indexes) if vf_indexes is not None else [0, 1]
    pci_rows = discovery.parse_lspci_nn(samples.get("lspci"))
    bind_rows = dpdk.parse_devbind(samples.get("dpdk_devbind"))
    dao_rows = parse_dao_port_list(samples.get("dao_port_list"))
    explicit = {
        int(item["vf_index"]): item
        for item in dao_rows
        if item.get("vf_index") is not None
    }

    mappings: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for vf_index in wanted:
        row = explicit.get(vf_index)
        if row and (row.get("representor_name") or row.get("dpdk_port_id")):
            mappings.append(
                {
                    "vf_index": vf_index,
                    "vf_pci": row.get("vf_pci"),
                    "dpdk_port_id": row.get("dpdk_port_id"),
                    "representor_name": row.get("representor_name"),
                    "representor_netdev": row.get("representor_name"),
                    "representor_type": "dpdk",
                    "backing_device": row.get("vf_pci"),
                    "mapping_source": "dao-port-list",
                    "confidence": "high",
                    "warnings": [],
                }
            )
            continue
        warnings.append(
            "vf_index=%s has no explicit DAO/DPDK representor; confidence=low"
            % vf_index
        )
        mappings.append(
            {
                "vf_index": vf_index,
                "vf_pci": None,
                "dpdk_port_id": None,
                "representor_name": None,
                "representor_netdev": None,
                "representor_type": "dpdk",
                "backing_device": None,
                "mapping_source": "unmapped",
                "confidence": "low",
                "warnings": [
                    "DPDK representor mapping requires an explicit vf_index in the DAO port list"
                ],
            }
        )

    high = all(m.get("confidence") == "high" for m in mappings) if mappings else False
    LOG.info("marvell dpdk representor map high=%s rows=%s", high, len(mappings))
    return OperationResult(
        operation="map_dpdk_representors",
        vendor=VENDOR,
        status=STATUS_OK if high else STATUS_SKIPPED,
        message="explicit DPDK representor map" if high else "DPDK representor discovery-required",
        executed=True,
        details={
            "location": "dpu",
            "model": "dpdk",
            "strategy": "marvell-dpdk-representor",
            "mappings": mappings,
            "pci_devices": pci_rows,
            "dpdk_bindings": bind_rows,
            "dao_ports": dao_rows,
            "warnings": warnings,
            "require_high_confidence": bool(
                ((cfg.get("representor_mapping") or {}).get("require_high_confidence", True))
            ),
        },
    )


def mapping_document(result: OperationResult) -> Dict[str, Any]:
    details = result.details or {}
    return {
        "schema_version": "odse-vf-representor-map/v1alpha1",
        "location": "dpu",
        "model": "dpdk",
        "strategy": "marvell-dpdk-representor",
        "vendor": "marvell-octeon",
        "mappings": details.get("mappings") or [],
        "warnings": details.get("warnings") or [],
        "note": "DPDK representors are not Linux netdevs. Uncertain maps stay confidence=low.",
    }
