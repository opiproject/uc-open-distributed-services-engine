"""DPU-side OVS / OVS-DOCA validation for NVIDIA BlueField DPU mode.

Read-only by default. Live SSH is planned through RemoteCommandRunner;
tests inject `samples` so no hardware or SSH is required.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Sequence

from ..base.models import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, OperationResult
from ..base.remote import RemoteCommandRunner, is_dpu_mode, ssh_params
from . import ovs as nvovs

LOG = logging.getLogger(__name__)
VENDOR = "nvidia"

OVS_AVAILABLE = "OVS_AVAILABLE"
OVS_MISSING = "OVS_MISSING"
OVS_SERVICE_NOT_RUNNING = "OVS_SERVICE_NOT_RUNNING"
OVS_DB_UNREACHABLE = "OVS_DB_UNREACHABLE"
OVS_DOCA_AVAILABLE = "OVS_DOCA_AVAILABLE"
OVS_DOCA_MISSING = "OVS_DOCA_MISSING"
DPU_REMOTE_UNREACHABLE = "DPU_REMOTE_UNREACHABLE"
VALIDATION_PLANNED = "OVS_VALIDATION_PLANNED"


def validate_dpu_ovs_environment(
    config: Optional[Mapping[str, Any]],
    *,
    samples: Optional[Mapping[str, Any]] = None,
    runner: Optional[RemoteCommandRunner] = None,
    dry_run: bool = True,
    allow_doca_fallback: bool = False,
) -> OperationResult:
    """Validate OVS on the DPU. Never claims OVS-DOCA unless listed."""
    cfg = dict(config or {})
    if not is_dpu_mode(cfg):
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="not NVIDIA DPU mode; host OVS path is advanced-profile only",
            details={"dpu_ovs_status": None},
        )

    params = ssh_params(cfg)
    nvidia = cfg.get("nvidia") or {}
    want_doca = str(nvidia.get("datapath") or "").lower() in ("ovs-doca", "doca")
    details: Dict[str, Any] = {
        "want_doca": want_doca,
        "allow_doca_fallback": allow_doca_fallback,
        "dry_run": dry_run,
    }

    if samples is not None:
        return _from_samples(samples, want_doca, allow_doca_fallback, details)

    if not params.get("host") or not params.get("user"):
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="DPU_REMOTE_UNREACHABLE: dpu.management_ip/ssh_user required",
            details={**details, "dpu_ovs_status": DPU_REMOTE_UNREACHABLE},
        )

    remote = runner or RemoteCommandRunner()
    planned = []
    for argv in (
        ["command", "-v", "ovs-vsctl"],
        ["command", "-v", "ovs-appctl"],
        ["systemctl", "is-active", "openvswitch"],
        ["ovs-vsctl", "get", "Open_vSwitch", ".", "datapath_types"],
        ["ovs-vsctl", "show"],
        ["ovs-vsctl", "get", "Open_vSwitch", ".", "other_config"],
    ):
        planned.append(
            remote.run_ssh(
                params["host"],
                params["user"],
                list(argv),
                key_path=params.get("key_path"),
                port=int(params.get("port") or 22),
                dry_run=True,
                sudo=bool(params.get("sudo")),
                reason="dpu ovs validate",
            )
        )
    # Validation is read-only. Do not plan host `ovs-vsctl add-br`.
    # Bridge create belongs in prepare-ovs via RemoteCommandRunner.
    details["dpu_ovs_status"] = VALIDATION_PLANNED
    details["host_ovs_untouched"] = True
    details["bridge_plan"] = (
        "not planned during validate-config; prepare-ovs owns DPU-side add-br"
    )
    return OperationResult(
        operation="validate_dpu_ovs_environment",
        vendor=VENDOR,
        status=STATUS_OK,
        message="DPU OVS validation planned (dry-run SSH probes only; not host OVS)",
        executed=False,
        commands=planned,
        details=details,
    )


def _from_samples(
    samples: Mapping[str, Any],
    want_doca: bool,
    allow_doca_fallback: bool,
    details: Dict[str, Any],
) -> OperationResult:
    vsctl = bool(samples.get("ovs-vsctl"))
    appctl = bool(samples.get("ovs-appctl"))
    service = str(samples.get("systemctl_is_active") or "")
    show = samples.get("ovs-vsctl_show")
    show_ok = isinstance(show, str) and bool(show.strip())
    dps_raw = samples.get("datapath_types") or ""
    dps = nvovs.parse_ovs_list(str(dps_raw))
    doca = "doca" in [x.lower() for x in dps]
    details.update(
        {
            "ovs-vsctl": vsctl,
            "ovs-appctl": appctl,
            "service": service or None,
            "datapath_types": dps,
            "doca": doca,
            "show_bytes": len(show) if isinstance(show, str) else None,
        }
    )
    if samples.get("remote_unreachable"):
        details["dpu_ovs_status"] = DPU_REMOTE_UNREACHABLE
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message=DPU_REMOTE_UNREACHABLE,
            details=details,
        )
    if not vsctl:
        details["dpu_ovs_status"] = OVS_MISSING
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message=OVS_MISSING,
            details=details,
        )
    if service and service not in ("active", "unknown") and not show_ok:
        # Some BlueField/DOCA images do not manage ovs-vswitchd under a
        # systemd unit literally named "openvswitch" (or run it via a boot
        # script instead of systemd at all), so `systemctl is-active
        # openvswitch` can legitimately report "inactive" while OVS is
        # genuinely up and serving requests. A real, non-empty `ovs-vsctl
        # show` is direct evidence the OVS instance is usable and takes
        # precedence over a systemd unit-name guess; only treat the service
        # check as blocking when there is no such evidence.
        details["dpu_ovs_status"] = OVS_SERVICE_NOT_RUNNING
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message=OVS_SERVICE_NOT_RUNNING,
            details=details,
        )
    if show is None and samples.get("db_unreachable"):
        details["dpu_ovs_status"] = OVS_DB_UNREACHABLE
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message=OVS_DB_UNREACHABLE,
            details=details,
        )
    if want_doca and not doca:
        details["dpu_ovs_status"] = OVS_DOCA_MISSING
        if allow_doca_fallback:
            return OperationResult(
                operation="validate_dpu_ovs_environment",
                vendor=VENDOR,
                status=STATUS_OK,
                message="OVS_DOCA_MISSING but fallback enabled",
                details=details,
            )
        return OperationResult(
            operation="validate_dpu_ovs_environment",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="OVS_DOCA_MISSING: datapath=ovs-doca requires doca in datapath_types",
            details=details,
        )
    status_code = OVS_DOCA_AVAILABLE if (want_doca and doca) else OVS_AVAILABLE
    details["dpu_ovs_status"] = status_code
    return OperationResult(
        operation="validate_dpu_ovs_environment",
        vendor=VENDOR,
        status=STATUS_OK,
        message=status_code,
        executed=True,
        details=details,
    )


UNKNOWN_BRIDGE_REFUSED = "UNKNOWN_BRIDGE_REFUSED"


def plan_dpu_ovs_cleanup(
    config: Optional[Mapping[str, Any]],
    *,
    owned_bridge: Optional[str] = None,
    owned_ports: Optional[Sequence[str]] = None,
    owned_flows: bool = False,
    lab_owned_bridge: bool = False,
    dry_run: bool = True,
    runner: Optional[RemoteCommandRunner] = None,
) -> OperationResult:
    """Plan DPU-side OVS teardown. Never deletes an untracked bridge/port."""
    cfg = dict(config or {})
    details: Dict[str, Any] = {
        "location": "dpu",
        "lab_owned_bridge": lab_owned_bridge,
        "owned_bridge": owned_bridge,
        "owned_ports": list(owned_ports or []),
        "dry_run": dry_run,
    }
    if not is_dpu_mode(cfg):
        return OperationResult(
            operation="plan_dpu_ovs_cleanup",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="not NVIDIA DPU mode; host OVS cleanup is the advanced-profile path",
            details=details,
        )

    params = ssh_params(cfg)
    remote = runner or RemoteCommandRunner()
    commands = []
    host, user = params.get("host"), params.get("user")

    def _ssh(argv: list, reason: str):
        if not host or not user:
            from ..base.commands import run as local_run

            return local_run(argv, dry_run=True, reason=reason)
        return remote.run_ssh(
            host,
            user,
            argv,
            key_path=params.get("key_path"),
            port=int(params.get("port") or 22),
            dry_run=dry_run,
            sudo=bool(params.get("sudo")),
            reason=reason,
        )

    bridge = owned_bridge or ((cfg.get("ovs") or {}).get("bridge"))
    ports = [p for p in (owned_ports or []) if p]
    if owned_flows and bridge:
        commands.append(
            _ssh(
                ["ovs-ofctl", "del-flows", bridge],
                "dpu cleanup: remove lab OpenFlow entries on %s" % bridge,
            )
        )
    for port in ports:
        if not bridge:
            break
        commands.append(
            _ssh(
                ["ovs-vsctl", "--if-exists", "del-port", bridge, port],
                "dpu cleanup: del-port %s %s" % (bridge, port),
            )
        )

    refused = None
    if bridge and not lab_owned_bridge:
        refused = UNKNOWN_BRIDGE_REFUSED
        details["refused_del_br"] = True
        details["refuse_reason"] = (
            "will not delete DPU bridge %r: not created by this run and "
            "ovs.lab_owned is not true" % bridge
        )
    elif bridge and lab_owned_bridge:
        commands.append(
            _ssh(
                ["ovs-vsctl", "--if-exists", "del-br", bridge],
                "dpu cleanup: del-br %s (lab_owned)" % bridge,
            )
        )

    commands.append(_ssh(["ovs-vsctl", "show"], "dpu cleanup: collect final ovs-vsctl show"))
    details["dpu_ovs_status"] = refused or "CLEANUP_PLANNED"
    return OperationResult(
        operation="plan_dpu_ovs_cleanup",
        vendor=VENDOR,
        status=STATUS_ERROR if refused else STATUS_OK,
        message=refused or "DPU-side OVS cleanup planned (dry-run default; host OVS not touched)",
        executed=False,
        destructive=True,
        commands=commands,
        details=details,
    )
