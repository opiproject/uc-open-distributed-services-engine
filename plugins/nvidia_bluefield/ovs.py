"""NVIDIA OVS helpers: kernel OVS-TC first; OVS-DOCA optional and unassumed.

OVS-DOCA is NVIDIA-specific and needs a DOCA-enabled OVS build plus DOCA
packages (`<DOCA_VERSION>`). `datapath_type=doca` is accepted only when
`ovs-vsctl` reports it in datapath_types (or a test injects that list).
Default dry_run=True — no ovs-vsctl mutations unless dry_run=False.

The generic add-br/add-port/add-flow/capability-probe mechanics (not
NVIDIA-specific) now live in plugins/base/ovs.py and are called here with
vendor="nvidia" -- see that module's docstring for why (Intel's
P4Runtime-backed OVS path in plugins/intel_ipu/ovs_p4.py reuses the same
code instead of re-implementing it). Only genuinely NVIDIA/DOCA-specific
concepts (datapath_type=doca gating, doca-init, hw-offload) stay here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..base import ovs as base_ovs
from ..base.commands import command_exists, run
from ..base.models import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    CommandResult,
    OperationResult,
)

LOG = logging.getLogger(__name__)
VENDOR = "nvidia"

# Kernel / upstream OVS. "doca" is NVIDIA OVS-DOCA only.
DATAPATH_SYSTEM = "system"
DATAPATH_DOCA = "doca"

RunFn = Callable[..., CommandResult]
ExistsFn = Callable[[str], bool]

# Re-exported for existing callers/tests that import these from here.
parse_ovs_list = base_ovs.parse_ovs_list
parse_other_config = base_ovs.parse_other_config


def probe_ovs_capabilities(
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    datapath_types: Optional[Sequence[str]] = None,
    iface_types: Optional[Sequence[str]] = None,
    dry_run: bool = True,
) -> OperationResult:
    """Read datapath_types / iface_types / other_config. Does not mutate.

    Default dry_run=True: plan the get commands. Live SSH/ovs-vsctl only
    when the caller explicitly sets dry_run=False (apply path).

    Delegates the generic get/parse mechanics to plugins.base.ovs and adds
    the NVIDIA-specific doca_datapath/doca_note fields on top.
    """
    base = base_ovs.probe_ovs_capabilities(
        vendor=VENDOR,
        runner=runner,
        command_exists_fn=command_exists_fn,
        datapath_types=datapath_types,
        iface_types=iface_types,
        dry_run=dry_run,
    )
    dps = base.details.get("datapath_types") or []
    doca = DATAPATH_DOCA in [x.lower() for x in dps]
    base.details["doca_datapath"] = doca
    base.details["doca_note"] = (
        "OVS-DOCA is NVIDIA-specific and requires DOCA packages "
        "and a DOCA-enabled OVS; datapath_type=doca is invalid until listed"
    )
    base.message = "OVS datapath_types=%s doca_available=%s (not assumed)" % (dps, doca)
    if base.status == STATUS_SKIPPED:
        base.message = "ovs-vsctl not found; cannot validate OVS or OVS-DOCA"
    LOG.info("OVS capabilities datapath_types=%s doca=%s hw-offload=%s", dps, doca, base.details.get("hw_offload"))
    return base


def enable_ovs_hw_offload(
    dry_run: bool = True,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
) -> OperationResult:
    """Set Open_vSwitch other_config:hw-offload=true (kernel OVS-TC path)."""
    return _ovs_set(
        ["ovs-vsctl", "set", "Open_vSwitch", ".", "other_config:hw-offload=true"],
        operation="enable_ovs_hw_offload",
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
        message="enable other_config:hw-offload=true (not proof of in_hw)",
    )


def enable_doca_init(
    dry_run: bool = True,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    datapath_types: Optional[Sequence[str]] = None,
) -> OperationResult:
    """NVIDIA OVS-DOCA init. Skipped unless doca datapath is listed.

    Placeholder other_config:doca-init=true — confirm against <DOCA_VERSION>.
    """
    cap = probe_ovs_capabilities(
        runner=runner,
        command_exists_fn=command_exists_fn,
        datapath_types=datapath_types,
        dry_run=dry_run,
    )
    if not cap.details.get("doca_datapath"):
        LOG.warning("enable_doca_init: OVS-DOCA datapath not listed; refusing")
        return OperationResult(
            operation="enable_doca_init",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message=(
                "OVS-DOCA not available (datapath_types=%s). "
                "Install DOCA-enabled OVS/<DOCA_VERSION> before enabling. "
                "Kernel hw-offload is the default path."
                % cap.details.get("datapath_types")
            ),
            destructive=True,
            executed=False,
            commands=list(cap.commands),
            details={**cap.details, "dry_run": dry_run},
        )
    return _ovs_set(
        ["ovs-vsctl", "set", "Open_vSwitch", ".", "other_config:doca-init=true"],
        operation="enable_doca_init",
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
        message="OVS-DOCA doca-init placeholder; verify <DOCA_VERSION> docs",
        extra_details={"doca_datapath": True, "nvidia_specific": True},
    )


def create_ovs_bridge(
    bridge: str,
    datapath_type: str = DATAPATH_SYSTEM,
    dry_run: bool = True,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    datapath_types: Optional[Sequence[str]] = None,
) -> OperationResult:
    """NVIDIA-specific datapath_type=doca gate, then delegate add-br/set
    datapath_type mechanics to plugins.base.ovs.create_ovs_bridge."""
    if not bridge or not str(bridge).strip():
        return OperationResult(
            operation="create_ovs_bridge",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="bridge name required",
            destructive=True,
            executed=False,
        )
    br = bridge.strip()
    dp = (datapath_type or DATAPATH_SYSTEM).strip()
    pre_commands: List[CommandResult] = []
    if dp.lower() == DATAPATH_DOCA:
        cap = probe_ovs_capabilities(
            runner=runner,
            command_exists_fn=command_exists_fn,
            datapath_types=datapath_types,
            dry_run=dry_run,
        )
        pre_commands.extend(cap.commands)
        proven = bool(cap.details.get("doca_datapath"))
        # Live/injected negative evidence: refuse. Dry-run with an empty
        # probe is unknown, not "doca missing" — plan add-br via the
        # runner (SSH on DPU) and do not invent that DOCA is listed.
        if not proven and (datapath_types is not None or not dry_run):
            return OperationResult(
                operation="create_ovs_bridge",
                vendor=VENDOR,
                status=STATUS_SKIPPED,
                message="datapath_type=doca requested but not in OVS datapath_types=%s"
                % cap.details.get("datapath_types"),
                destructive=True,
                executed=False,
                commands=pre_commands,
                details={**cap.details, "bridge": br, "datapath_type": dp},
            )
    result = base_ovs.create_ovs_bridge(
        br,
        datapath_type=dp,
        dry_run=dry_run,
        vendor=VENDOR,
        runner=runner,
        command_exists_fn=command_exists_fn,
    )
    result.commands = pre_commands + list(result.commands)
    if result.status == STATUS_OK:
        result.message = "bridge %s datapath_type=%s dry_run=%s (system=kernel OVS-TC default)" % (
            br,
            dp,
            dry_run,
        )
    LOG.info("create_ovs_bridge %s datapath_type=%s dry_run=%s", br, dp, dry_run)
    return result


def create_ovs_doca_bridge(
    bridge: str,
    dry_run: bool = True,
    **kwargs: Any,
) -> OperationResult:
    """Create a bridge with datapath_type=doca only if OVS lists that type."""
    result = create_ovs_bridge(
        bridge, datapath_type=DATAPATH_DOCA, dry_run=dry_run, **kwargs
    )
    result.operation = "create_ovs_doca_bridge"
    result.details["nvidia_specific"] = True
    result.details["requires_doca_packages"] = True
    return result


def add_doca_port(
    bridge: str,
    port: str,
    dry_run: bool = True,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    datapath_types: Optional[Sequence[str]] = None,
    iface_types: Optional[Sequence[str]] = None,
) -> OperationResult:
    """Add a port on an OVS-DOCA bridge. Interface type is not guessed.

    If iface_types includes 'doca', use type=doca; otherwise skip rather
    than assume dpdk/system.
    """
    cap = probe_ovs_capabilities(
        runner=runner,
        command_exists_fn=command_exists_fn,
        datapath_types=datapath_types,
        iface_types=iface_types,
        dry_run=dry_run,
    )
    if not cap.details.get("doca_datapath"):
        return OperationResult(
            operation="add_doca_port",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="OVS-DOCA datapath not available; not adding port %r" % port,
            destructive=True,
            executed=False,
            details={**cap.details, "bridge": bridge, "port": port},
        )
    types = [t.lower() for t in (cap.details.get("iface_types") or [])]
    iface_type = "doca" if "doca" in types else None
    if iface_type is None:
        return OperationResult(
            operation="add_doca_port",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message=(
                "OVS-DOCA datapath listed but iface type 'doca' not in %s; "
                "not guessing dpdk/system for port %s"
                % (cap.details.get("iface_types"), port)
            ),
            destructive=True,
            executed=False,
            details={**cap.details, "bridge": bridge, "port": port, "iface_type": None},
        )
    return add_representor_port(
        bridge,
        port,
        interface_type=iface_type,
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
    )


def add_representor_port(
    bridge: str,
    representor: str,
    interface_type: str = DATAPATH_SYSTEM,
    dry_run: bool = True,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
) -> OperationResult:
    """Attach an existing representor netdev. Name is not invented.

    Delegates to plugins.base.ovs.add_representor_port (generic mechanic).
    """
    return base_ovs.add_representor_port(
        bridge,
        representor,
        interface_type=interface_type,
        dry_run=dry_run,
        vendor=VENDOR,
        runner=runner,
        command_exists_fn=command_exists_fn,
    )


def add_flow(
    bridge: str,
    flow: str,
    dry_run: bool = True,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
) -> OperationResult:
    """ovs-ofctl add-flow. Flow string is caller-provided, not synthesized here.

    Symbolic (non-numeric) in_port/output port names require an OVS/OpenFlow
    build that supports them; confirm against your ovs-ofctl version before
    relying on this outside dry-run.
    """
    return base_ovs.add_flow(
        bridge, flow, dry_run=dry_run, vendor=VENDOR, runner=runner, command_exists_fn=command_exists_fn
    )


def dump_ovs_state(
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    dry_run: bool = True,
) -> OperationResult:
    """Read-only ovs-vsctl show + dpctl/show + capability probe."""
    result = base_ovs.dump_ovs_state(
        vendor=VENDOR, runner=runner, command_exists_fn=command_exists_fn, dry_run=dry_run
    )
    if result.details.get("capabilities") is not None:
        caps = dict(result.details["capabilities"])
        dps = caps.get("datapath_types") or []
        caps["doca_datapath"] = DATAPATH_DOCA in [x.lower() for x in dps]
        caps["doca_note"] = (
            "OVS-DOCA is NVIDIA-specific and requires DOCA packages "
            "and a DOCA-enabled OVS; datapath_type=doca is invalid until listed"
        )
        result.details["capabilities"] = caps
    return result


def plan_configure_bridge(bridge: Optional[str] = None) -> OperationResult:
    return create_ovs_bridge(bridge or "br-int", datapath_type=DATAPATH_SYSTEM, dry_run=True)


# Re-exported for enable_ovs_hw_offload/enable_doca_init above and any
# existing callers/tests that import the low-level helper from here.
def _ovs_set(
    argv: Sequence[str],
    *,
    operation: str,
    dry_run: bool,
    runner: Optional[RunFn],
    command_exists_fn: Optional[ExistsFn],
    message: str,
    extra_details: Optional[Dict[str, Any]] = None,
) -> OperationResult:
    return base_ovs._ovs_set(
        argv,
        operation=operation,
        vendor=VENDOR,
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
        message=message,
        extra_details=extra_details,
    )
