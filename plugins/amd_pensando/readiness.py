"""AMD Pensando ionic / ionic_rdma / DSC readiness (discovery-first)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
from . import discovery, ionic, rdma

LOG = logging.getLogger(__name__)
VENDOR = "amd"

AMD_PENSANDO_DISCOVERY_READY = "AMD_PENSANDO_DISCOVERY_READY"
AMD_IONIC_MISSING = "AMD_IONIC_MISSING"
AMD_SRIOV_NOT_AVAILABLE = "AMD_SRIOV_NOT_AVAILABLE"
AMD_REPRESENTORS_NOT_DISCOVERED = "AMD_REPRESENTORS_NOT_DISCOVERED"
AMD_SWITCHDEV_NOT_DISCOVERED = "AMD_SWITCHDEV_NOT_DISCOVERED"
AMD_OVS_PATH_NOT_DISCOVERED = "AMD_OVS_PATH_NOT_DISCOVERED"
AMD_RDMA_AVAILABLE = "AMD_RDMA_AVAILABLE"
AMD_NOT_READY = "AMD_NOT_READY"

AMD_DISCOVERY_PROFILE = "amd-pensando-discovery-first"


def evaluate_amd_readiness(
    config: Optional[Dict[str, Any]] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    cfg = config or {}
    samples = dict(samples or {})
    amd = cfg.get("amd_pensando") if isinstance(cfg.get("amd_pensando"), dict) else {}
    if amd.get("allow_assumed_forwarding"):
        return OperationResult(
            operation="amd_pensando_readiness",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="AMD must not assume a forwarding control path",
            executed=True,
            details={"readiness_status": AMD_NOT_READY},
        )

    lspci = samples.get("lspci")
    devices = discovery.parse_lspci_nn(lspci) if lspci is not None else None
    ethtool_i = samples.get("ethtool_i") or {}
    ionic_ifaces = ionic.list_ionic_interfaces(ethtool_i) if ethtool_i else []
    lsmod = samples.get("lsmod")
    modules = rdma.parse_lsmod(lsmod) if lsmod is not None else {}
    rdma_det = rdma.detect_ionic_rdma(
        rdma_link=samples.get("rdma_link"),
        lsmod=lsmod,
    )
    sriov_total = samples.get("sriov_totalvfs")
    representors = samples.get("representors") or []
    switchdev = samples.get("switchdev")
    ovs_compat = samples.get("ovs_compatible")
    dsc_mgmt = samples.get("dsc_management")
    if dsc_mgmt is None:
        dsc_mgmt = bool((cfg.get("dpu") or {}).get("management_ip"))

    checks = {
        "device_detected": None if devices is None else bool(devices),
        "ionic_module": modules.get("ionic"),
        "ionic_interfaces": ionic_ifaces,
        "ionic_ethtool": {name: ionic.parse_ethtool_i(raw) for name, raw in ethtool_i.items()},
        "ionic_stats_names": sorted(
            {
                k
                for raw in (samples.get("ethtool_s") or {}).values()
                for k in ionic.parse_ethtool_s(raw)
            }
        ),
        "ionic_rdma": rdma_det.details,
        "sriov_totalvfs": sriov_total,
        "vf_creation_tested": False,
        "representors": representors,
        "switchdev": switchdev,
        "ovs_compatible": ovs_compat,
        "dsc_management_configured": dsc_mgmt,
    }

    status = AMD_PENSANDO_DISCOVERY_READY
    if devices is not None and not devices:
        status = AMD_NOT_READY
    elif amd.get("require_ionic", True) and not (ionic_ifaces or modules.get("ionic")):
        if ethtool_i or lsmod is not None:
            status = AMD_IONIC_MISSING
        else:
            status = AMD_PENSANDO_DISCOVERY_READY
    elif amd.get("require_sriov") and not sriov_total:
        status = AMD_SRIOV_NOT_AVAILABLE
    elif amd.get("require_representors") and not representors:
        status = AMD_REPRESENTORS_NOT_DISCOVERED
    elif amd.get("require_switchdev") and not switchdev:
        status = AMD_SWITCHDEV_NOT_DISCOVERED
    elif amd.get("require_ovs") and not ovs_compat:
        status = AMD_OVS_PATH_NOT_DISCOVERED
    elif rdma_det.details.get("detected") and amd.get("require_rdma"):
        status = AMD_RDMA_AVAILABLE

    extra_status = []
    if rdma_det.details.get("detected") and status == AMD_PENSANDO_DISCOVERY_READY:
        extra_status.append(AMD_RDMA_AVAILABLE)
    if not representors:
        extra_status.append(AMD_REPRESENTORS_NOT_DISCOVERED)
    if switchdev is not True:
        extra_status.append(AMD_SWITCHDEV_NOT_DISCOVERED)
    if ovs_compat is not True:
        extra_status.append(AMD_OVS_PATH_NOT_DISCOVERED)

    LOG.info("amd readiness %s", status)
    return OperationResult(
        operation="amd_pensando_readiness",
        vendor=VENDOR,
        status=STATUS_OK if status in (AMD_PENSANDO_DISCOVERY_READY, AMD_RDMA_AVAILABLE) else STATUS_SKIPPED,
        message=status,
        executed=True,
        details={
            "readiness_status": status,
            "related_statuses": extra_status,
            "checks": checks,
            "profile": (cfg.get("vendor") or {}).get("profile") or AMD_DISCOVERY_PROFILE,
            "warnings": [
                "Do not assume representors, switchdev, or OVS forwarding",
                "ionic statistics are not hardware-offload proof",
            ],
        },
    )
