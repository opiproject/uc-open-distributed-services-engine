"""Marvell OCTEON DAO/DPDK readiness gate (read-only by default).

Does not start DAO, bind DPDK, or allocate hugepages. Tests inject
`samples=` so unit runs never SSH or execute DAO.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from ..base.commands import command_exists
from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
from . import dao_ovs, discovery, dpdk

LOG = logging.getLogger(__name__)
VENDOR = "marvell"

MARVELL_READY_FOR_DRY_RUN = "MARVELL_READY_FOR_DRY_RUN"
MARVELL_DAO_MISSING = "MARVELL_DAO_MISSING"
MARVELL_DPDK_MISSING = "MARVELL_DPDK_MISSING"
MARVELL_HUGEPAGES_MISSING = "MARVELL_HUGEPAGES_MISSING"
MARVELL_REPRESENTOR_DISCOVERY_REQUIRED = "MARVELL_REPRESENTOR_DISCOVERY_REQUIRED"
MARVELL_NOT_READY = "MARVELL_NOT_READY"

MARVELL_DAO_PROFILE = "marvell-octeon-dao-ovs-offload"


def parse_hugepages(raw: Optional[str]) -> Dict[str, Optional[int]]:
    total = free = None
    if not raw:
        return {"HugePages_Total": None, "HugePages_Free": None}
    for line in raw.splitlines():
        if line.startswith("HugePages_Total:"):
            try:
                total = int(line.split()[1])
            except (IndexError, ValueError):
                total = None
        elif line.startswith("HugePages_Free:"):
            try:
                free = int(line.split()[1])
            except (IndexError, ValueError):
                free = None
    return {"HugePages_Total": total, "HugePages_Free": free}


def _placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">")


def evaluate_marvell_readiness(
    config: Optional[Dict[str, Any]] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
    command_exists_fn=None,
) -> OperationResult:
    """Read-only DAO/DPDK gate. Missing facts stay null; no fake readiness."""
    cfg = config or {}
    samples = dict(samples or {})
    exists = command_exists_fn or command_exists
    checks: Dict[str, Any] = {}
    warnings: list = []

    lspci = samples.get("lspci")
    octeon = discovery.parse_lspci_nn(lspci) if lspci is not None else []
    if lspci is None:
        checks["octeon_device"] = None
        warnings.append("OCTEON PCI presence not probed (no lspci sample)")
    else:
        checks["octeon_device"] = bool(octeon)

    dpdk_ok = bool(samples.get("dpdk_available"))
    if samples.get("dpdk_available") is None:
        dpdk_ok = bool(exists("dpdk-devbind.py") or exists("dpdk-devbind") or exists("dpdk-testpmd"))
    checks["dpdk_available"] = dpdk_ok

    devbind = samples.get("dpdk_devbind")
    if "dpdk_devbind_tool" in samples:
        checks["dpdk_devbind_tool"] = bool(samples.get("dpdk_devbind_tool"))
    else:
        checks["dpdk_devbind_tool"] = bool(exists("dpdk-devbind.py") or exists("dpdk-devbind"))
    ports = dpdk.parse_devbind(devbind) if devbind else []
    checks["dpdk_bound_ports"] = [p for p in ports if p.get("binding") == "dpdk"]

    hugepages = parse_hugepages(samples.get("meminfo"))
    checks["hugepages"] = hugepages
    hugepages_required = bool((cfg.get("dpdk") or {}).get("hugepages_required", True))
    hugepages_ok = True
    if hugepages_required:
        total = hugepages.get("HugePages_Total")
        if samples.get("meminfo") is None:
            hugepages_ok = False
            warnings.append("hugepage availability unknown (no /proc/meminfo sample)")
        else:
            hugepages_ok = bool(total and total > 0)

    dao_cfg = cfg.get("dao") if isinstance(cfg.get("dao"), dict) else {}
    configured_app = dao_cfg.get("ovs_offload_binary") or (cfg.get("dpu") or {}).get("dao_app")
    if _placeholder(configured_app):
        configured_app = None
    dao = dao_ovs.check_dao_available(
        environ=samples.get("environ"),
        command_exists_fn=exists,
        configured_app=configured_app,
    )
    if "dao_available" in samples:
        dao_available = bool(samples.get("dao_available"))
    else:
        dao_available = bool(dao.details.get("available"))
    checks["dao"] = {"available": dao_available, "configured_app": configured_app, "scan": dao.details}

    dao_ports = samples.get("dao_port_list")
    checks["dpdk_representors_visible"] = bool(dao_ports) or bool(checks["dpdk_bound_ports"])
    checks["dao_ovs_integration_configured"] = bool(
        configured_app or dao_available or (cfg.get("marvell") or {}).get("datapath") == "dao"
    )

    host = cfg.get("host") if isinstance(cfg.get("host"), dict) else {}
    mgmt = host.get("management_interface")
    pf = (cfg.get("dpu") or {}).get("pf_name")
    mgmt_ok = bool(mgmt) and mgmt != pf and not _placeholder(mgmt)
    checks["management_interface_protected"] = mgmt_ok
    if not mgmt_ok:
        warnings.append("host.management_interface missing, placeholder, or equals dpu.pf_name")

    marvell = cfg.get("marvell") if isinstance(cfg.get("marvell"), dict) else {}
    if marvell.get("allow_host_ovs_for_forwarding") is True:
        return OperationResult(
            operation="marvell_dao_readiness",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="host OVS forwarding is not the Marvell DAO path",
            executed=True,
            details={
                "readiness_status": MARVELL_NOT_READY,
                "checks": checks,
                "warnings": warnings + ["allow_host_ovs_for_forwarding must stay false"],
            },
        )

    require_dao = marvell.get("require_dao", True)
    require_dpdk = marvell.get("require_dpdk", True)

    status = MARVELL_READY_FOR_DRY_RUN
    if checks.get("octeon_device") is False:
        status = MARVELL_NOT_READY
    elif require_dao and not dao_available:
        status = MARVELL_DAO_MISSING
    elif require_dpdk and not dpdk_ok:
        status = MARVELL_DPDK_MISSING
    elif hugepages_required and not hugepages_ok:
        status = MARVELL_HUGEPAGES_MISSING
    elif not checks["dpdk_representors_visible"]:
        status = MARVELL_REPRESENTOR_DISCOVERY_REQUIRED

    blocking = status != MARVELL_READY_FOR_DRY_RUN
    LOG.info("marvell readiness %s", status)
    return OperationResult(
        operation="marvell_dao_readiness",
        vendor=VENDOR,
        status=STATUS_OK if status == MARVELL_READY_FOR_DRY_RUN else STATUS_SKIPPED,
        message=status,
        executed=True,
        details={
            "readiness_status": status,
            "blocking": blocking,
            "checks": checks,
            "warnings": warnings,
            "profile": (cfg.get("vendor") or {}).get("profile") or MARVELL_DAO_PROFILE,
        },
    )
