"""AMD Pensando discovery workflow stages (read-only except VF create + --apply)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, OperationResult
from . import discovery, ionic, rdma
from .capability_report import AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH, build_gap_report
from .readiness import evaluate_amd_readiness

LOG = logging.getLogger(__name__)
VENDOR = "amd"


def _samples(samples: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return dict(samples or {})


def discover_device(config=None, *, samples=None) -> OperationResult:
    return discovery.discover(samples=_samples(samples))


def discover_ionic(config=None, *, samples=None) -> OperationResult:
    s = _samples(samples)
    ethtool_i = s.get("ethtool_i") or {}
    return ionic.list_ionic_as_result(ethtool_i)


def discover_rdma(config=None, *, samples=None) -> OperationResult:
    s = _samples(samples)
    return rdma.detect_ionic_rdma(rdma_link=s.get("rdma_link"), lsmod=s.get("lsmod"))


def discover_sriov(config=None, *, samples=None) -> OperationResult:
    s = _samples(samples)
    total = s.get("sriov_totalvfs")
    return OperationResult(
        operation="discover_sriov",
        vendor=VENDOR,
        status=STATUS_OK if total else STATUS_SKIPPED,
        message="sriov_totalvfs=%s" % total,
        executed=True,
        details={"sriov_totalvfs": total, "vf_creation_tested": False},
    )


def dry_run_create_vfs(config=None, *, apply: bool = False, samples=None) -> OperationResult:
    if apply:
        LOG.warning("amd VF create apply requested; refusing without proven PF identity")
        return OperationResult(
            operation="dry_run_create_vfs",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="VF create blocked: PF identity/sriov_totalvfs not proven for apply",
            destructive=True,
            executed=False,
            details={"apply": True, "created": False},
        )
    return OperationResult(
        operation="dry_run_create_vfs",
        vendor=VENDOR,
        status=STATUS_OK,
        message="VF create planned only (dry-run); no sriov_numvfs write",
        destructive=True,
        executed=False,
        details={"apply": False, "planned": ["would write sriov_numvfs after PF proof"]},
    )


def discover_representors(config=None, *, samples=None) -> OperationResult:
    s = _samples(samples)
    reps = list(s.get("representors") or [])
    return OperationResult(
        operation="discover_representors",
        vendor=VENDOR,
        status=STATUS_OK if reps else STATUS_SKIPPED,
        message="representors discovered" if reps else "representors not discovered",
        executed=True,
        representors=[],
        details={"representors": reps, "assumed": False},
    )


def discover_switchdev(config=None, *, samples=None) -> OperationResult:
    s = _samples(samples)
    mode = s.get("switchdev")
    return OperationResult(
        operation="discover_switchdev",
        vendor=VENDOR,
        status=STATUS_OK if mode else STATUS_SKIPPED,
        message="switchdev=%s" % mode,
        executed=True,
        details={"switchdev": mode, "assumed": False},
    )


def discover_ovs_feasibility(config=None, *, samples=None) -> OperationResult:
    s = _samples(samples)
    ok = s.get("ovs_compatible")
    return OperationResult(
        operation="discover_ovs_feasibility",
        vendor=VENDOR,
        status=STATUS_OK if ok else STATUS_SKIPPED,
        message="OVS path proven" if ok else "OVS forwarding not discovered",
        executed=True,
        details={"ovs_compatible": ok, "assumed": False},
    )


def collect_evidence(config=None, *, samples=None) -> OperationResult:
    ready = evaluate_amd_readiness(config, samples=samples)
    return OperationResult(
        operation="collect_evidence",
        vendor=VENDOR,
        status=STATUS_OK,
        message="AMD discovery evidence snapshot",
        executed=True,
        details={"readiness": ready.details},
    )


def generate_gap_report(config=None, *, samples=None, prior: Optional[Dict[str, Any]] = None) -> OperationResult:
    report = build_gap_report(config or {}, samples=_samples(samples), prior=prior)
    return OperationResult(
        operation="generate_gap_report",
        vendor=VENDOR,
        status=STATUS_OK,
        message=report.get("final_status"),
        executed=True,
        details=report,
    )


def forwarding_allowed(config=None, *, samples=None) -> bool:
    report = build_gap_report(config or {}, samples=_samples(samples))
    return report.get("final_status") not in (
        AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH,
        "AMD_NOT_READY",
        "AMD_DISCOVERY_ONLY",
    )


STAGE_FUNCS = {
    "discover-device": discover_device,
    "discover-ionic": discover_ionic,
    "discover-rdma": discover_rdma,
    "discover-sriov": discover_sriov,
    "dry-run-create-vfs": dry_run_create_vfs,
    "discover-representors": discover_representors,
    "discover-switchdev": discover_switchdev,
    "discover-ovs-feasibility": discover_ovs_feasibility,
    "collect-evidence": collect_evidence,
    "generate-gap-report": generate_gap_report,
}
