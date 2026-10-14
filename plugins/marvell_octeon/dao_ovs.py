"""DAO OVS placeholders. Real DAO CLI runs only when discovered *and* apply=True.

Default is dry-run / no apply. Do not copy NVIDIA OVS-TC or OVS-DOCA.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from ..base.models import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, OperationResult
from . import discovery, dpdk

LOG = logging.getLogger(__name__)
VENDOR = "marvell"

DAO_APP_PLACEHOLDER = "<DAO_OVS_APP>"


def check_dao_available(
    *,
    environ: Optional[Mapping[str, str]] = None,
    command_exists_fn=None,
    configured_app: Optional[str] = None,
) -> OperationResult:
    """Env + binary presence. Does not start DAO."""
    scan = discovery.scan_dao_environment(
        environ, command_exists_fn=command_exists_fn
    )
    app = configured_app or DAO_APP_PLACEHOLDER
    named = configured_app not in (None, "", DAO_APP_PLACEHOLDER)
    available = bool(scan.get("dao_env_present") or named)
    if named and command_exists_fn:
        available = bool(command_exists_fn(configured_app)) or scan.get("dao_env_present")
    LOG.info("DAO available=%s env=%s app=%s", available, scan.get("dao_env_present"), app)
    return OperationResult(
        operation="check_dao_available",
        vendor=VENDOR,
        status=STATUS_OK if available else STATUS_SKIPPED,
        message=(
            "DAO environment or configured app present"
            if available
            else "DAO not discovered; placeholders only (no CLI invented)"
        ),
        executed=True,
        destructive=False,
        details={
            "available": available,
            "configured_app": configured_app,
            "scan": scan,
        },
    )


def list_dpdk_ports(
    raw: Optional[str] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    text = raw
    if text is None and samples:
        text = samples.get("dpdk_devbind")
    result = dpdk.list_bound_devices(text)
    result.operation = "list_dpdk_ports"
    return result


def configure_representor_placeholder(
    representor: Optional[str] = None,
    *,
    apply: bool = False,
    dry_run: bool = True,
    dao_available: Optional[bool] = None,
) -> OperationResult:
    """Would attach <DPDK_REPR> in DAO OVS. No command unless apply and discovered."""
    return _gated_placeholder(
        operation="configure_representor_placeholder",
        apply=apply,
        dry_run=dry_run,
        dao_available=dao_available,
        planned=["<DAO_OVS_APP>", "add-port", representor or "<DPDK_REPR>"],
        message="DPDK representor attach placeholder; not ovs-vsctl type=system",
        extra={"representor": representor},
    )


def start_dao_ovs_offload_placeholder(
    *,
    apply: bool = False,
    dry_run: bool = True,
    dao_available: Optional[bool] = None,
    configured_app: Optional[str] = None,
) -> OperationResult:
    return _gated_placeholder(
        operation="start_dao_ovs_offload_placeholder",
        apply=apply,
        dry_run=dry_run,
        dao_available=dao_available,
        planned=[configured_app or DAO_APP_PLACEHOLDER],
        message="DAO OVS offload start placeholder; confirm <DAO_VERSION> docs",
        extra={"configured_app": configured_app},
    )


def collect_dao_telemetry_placeholder(
    *,
    dao_available: Optional[bool] = None,
) -> OperationResult:
    LOG.info("DAO telemetry placeholder; omit missing (no fake zeros)")
    return OperationResult(
        operation="collect_dao_telemetry_placeholder",
        vendor=VENDOR,
        status=STATUS_OK if dao_available else STATUS_SKIPPED,
        message="no DAO counters collected; rte_flow hits unknown",
        executed=True,
        destructive=False,
        telemetry=[],
        details={"dao_available": dao_available, "offload_state": None},
    )


def plan_configure(bridge: Optional[str] = None) -> OperationResult:
    """Backend hook: never mutates (apply=False)."""
    return OperationResult(
        operation="configure_ovs_bridge",
        vendor=VENDOR,
        status=STATUS_SKIPPED,
        message=(
            "generic OVS vs DAO not chosen; pass apply=True only after "
            "check_dao_available. bridge=%s" % (bridge or "br-int")
        ),
        destructive=True,
        executed=False,
        details={"datapath_choice": None, "apply": False, "bridge": bridge},
    )


def _gated_placeholder(
    *,
    operation: str,
    apply: bool,
    dry_run: bool,
    dao_available: Optional[bool],
    planned: Sequence[str],
    message: str,
    extra: Optional[dict] = None,
) -> OperationResult:
    details = {"apply": apply, "dry_run": dry_run, "planned": list(planned), **(extra or {})}
    if dao_available is False or dao_available is None:
        LOG.info("%s: DAO not configured/discovered; skip CLI", operation)
        return OperationResult(
            operation=operation,
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="%s — DAO not discovered/configured; no command run" % message,
            destructive=True,
            executed=False,
            details=details,
        )
    if not apply or dry_run:
        LOG.info("%s: held (apply=%s dry_run=%s)", operation, apply, dry_run)
        return OperationResult(
            operation=operation,
            vendor=VENDOR,
            status=STATUS_OK,
            message="%s — dry-run/no --apply; planned %s" % (message, list(planned)),
            destructive=True,
            executed=False,
            details=details,
        )
    LOG.warning("%s: apply requested but real DAO argv is not frozen in git", operation)
    return OperationResult(
        operation=operation,
        vendor=VENDOR,
        status=STATUS_ERROR,
        message=(
            "%s — apply=True but no discovered DAO command line; "
            "refusing to invent CLI. Set configured_app from <DAO_VERSION> docs."
            % message
        ),
        destructive=True,
        executed=False,
        details=details,
    )
