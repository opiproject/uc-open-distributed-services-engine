"""Marvell DAO telemetry. No invented counters; unknown stays null."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
from . import dao_ovs
from .readiness import evaluate_marvell_readiness

LOG = logging.getLogger(__name__)
VENDOR = "marvell"


def collect_dao_evidence(
    config: Optional[Dict[str, Any]] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    samples = dict(samples or {})
    cfg = config or {}
    ready = evaluate_marvell_readiness(cfg, samples=samples)
    dao_available = bool((ready.details.get("checks") or {}).get("dao", {}).get("available"))
    dao_running = samples.get("dao_running")
    if dao_running is None:
        dao_running = None
    ports = samples.get("dpdk_port_counters")
    representors = samples.get("representor_counters")
    indicators = samples.get("flow_offload_indicators")
    logs = samples.get("dao_logs")
    ovs_flows = samples.get("ovs_flows")
    vm_counters = samples.get("vm_counters")
    host_counters = samples.get("host_pf_vf_counters")

    confidence = "unknown"
    warnings = list((ready.details or {}).get("warnings") or [])
    if dao_available and indicators:
        confidence = "low"
        warnings.append("DAO/DPDK indicators present but not treated as offloaded:yes")
    elif not dao_available:
        warnings.append("DAO not available; marvell_dao_evidence stays empty")

    evidence = {
        "dao_available": dao_available,
        "dao_running": dao_running,
        "dpdk_ports": ports if ports is not None else [],
        "representors": representors if representors is not None else [],
        "flow_offload_indicators": indicators,
        "dao_logs": logs,
        "ovs_flows": ovs_flows,
        "vm_counters": vm_counters,
        "host_pf_vf_counters": host_counters,
        "confidence": confidence,
        "warnings": warnings,
    }
    placeholder = dao_ovs.collect_dao_telemetry_placeholder(dao_available=dao_available)
    LOG.info("marvell dao evidence confidence=%s", confidence)
    return OperationResult(
        operation="collect_dao_evidence",
        vendor=VENDOR,
        status=STATUS_OK if dao_available else STATUS_SKIPPED,
        message="DAO evidence collected without synthesizing counters",
        executed=True,
        telemetry=placeholder.telemetry,
        details={"marvell_dao_evidence": evidence, "offload_state": None},
    )
