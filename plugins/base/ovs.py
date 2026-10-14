"""Generic OVS control-surface helpers (ovs-vsctl/ovs-ofctl), vendor-parameterized.

`ovs-vsctl`/`ovs-ofctl` are standard upstream OVS tools -- the bridge/
port/flow primitives here (add-br, add-port, add-flow, capability probe)
are not NVIDIA-specific. This module holds that vendor-neutral subset
once so every vendor backend that drives real OVS (NVIDIA OVS-TC/OVS-DOCA
via plugins/nvidia_bluefield/ovs.py, Intel's P4Runtime-backed OVS via
plugins/intel_ipu/ovs_p4.py, ...) can call the same well-tested code path
instead of re-implementing it per vendor.

What stays vendor-specific and is deliberately NOT here: NVIDIA's
OVS-DOCA gating (`datapath_type=doca` validation, `doca-init`,
`hw-offload`) -- those are genuine NVIDIA/DOCA hardware concepts with no
Intel equivalent, and still live in plugins/nvidia_bluefield/ovs.py.

Default dry_run=True everywhere. Every mutation goes through the
injectable `runner`/`command_exists_fn` callables (or
plugins.base.commands.run/command_exists locally), so dry_run never
executes a real command unless a caller explicitly passes dry_run=False.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from .commands import command_exists, run
from .models import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, CommandResult, OperationResult

LOG = logging.getLogger(__name__)

RunFn = Callable[..., CommandResult]
ExistsFn = Callable[[str], bool]


def parse_ovs_list(raw: Optional[str]) -> List[str]:
    """Parse ovs-vsctl get/list output: [system, netdev] or system, netdev."""
    if not raw:
        return []
    text = raw.strip().strip('"')
    text = text.replace("[", " ").replace("]", " ").replace(",", " ")
    return [tok for tok in text.split() if tok]


def parse_other_config(raw: Optional[str]) -> Dict[str, str]:
    """Parse `{hw-offload="true", ...}` or key=value lines."""
    out: Dict[str, str] = {}
    if not raw:
        return out
    for match in re.finditer(r"([\w-]+)\s*=\s*\"?([^,\"}\s]+)\"?", raw):
        out[match.group(1)] = match.group(2)
    return out


def probe_ovs_capabilities(
    *,
    vendor: str = "generic",
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    datapath_types: Optional[Sequence[str]] = None,
    iface_types: Optional[Sequence[str]] = None,
    dry_run: bool = True,
) -> OperationResult:
    """Read datapath_types / iface_types / other_config. Does not mutate.

    Default dry_run=True: plan the get commands. Live SSH/local ovs-vsctl
    only when the caller explicitly sets dry_run=False (apply path).
    """
    exists = command_exists_fn or command_exists
    run_fn = runner or run
    commands: List[CommandResult] = []

    if datapath_types is not None:
        dps = list(datapath_types)
        ifaces = list(iface_types or [])
        return OperationResult(
            operation="probe_ovs_capabilities",
            vendor=vendor,
            status=STATUS_OK,
            message="OVS datapath_types=%s (not assumed)" % dps,
            executed=True,
            destructive=False,
            commands=commands,
            details={
                "datapath_types": dps,
                "iface_types": ifaces,
                "hw_offload": None,
                "other_config": {},
            },
        )

    if not exists("ovs-vsctl"):
        return OperationResult(
            operation="probe_ovs_capabilities",
            vendor=vendor,
            status=STATUS_SKIPPED,
            message="ovs-vsctl not found; cannot validate OVS",
            executed=False,
            details={
                "datapath_types": list(datapath_types or []),
                "iface_types": list(iface_types or []),
                "hw_offload": None,
            },
        )

    def _get(column: str) -> Optional[str]:
        argv = ["ovs-vsctl", "get", "Open_vSwitch", ".", column]
        cmd = run_fn(argv, dry_run=dry_run, timeout=15.0, reason="ovs probe %s" % column)
        commands.append(cmd)
        return cmd.stdout

    dps = parse_ovs_list(_get("datapath_types"))
    ifaces = parse_ovs_list(_get("iface_types"))
    other_raw = _get("other_config")
    other = parse_other_config(other_raw)
    hw = other.get("hw-offload")
    hw_bool: Optional[bool]
    if hw is None:
        hw_bool = None
    else:
        hw_bool = hw.lower() in ("true", "1", "yes")
    all_executed = bool(commands) and all(c.executed for c in commands)
    LOG.info("OVS capabilities vendor=%s datapath_types=%s hw-offload=%s executed=%s", vendor, dps, hw_bool, all_executed)
    return OperationResult(
        operation="probe_ovs_capabilities",
        vendor=vendor,
        status=STATUS_OK,
        message="OVS datapath_types=%s (not assumed)" % dps,
        executed=all_executed,
        destructive=False,
        commands=commands,
        details={
            "datapath_types": dps,
            "iface_types": ifaces,
            "hw_offload": hw_bool,
            "other_config": other,
        },
    )


def create_ovs_bridge(
    bridge: str,
    datapath_type: str = "system",
    dry_run: bool = True,
    *,
    vendor: str = "generic",
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
) -> OperationResult:
    if not bridge or not str(bridge).strip():
        return OperationResult(
            operation="create_ovs_bridge",
            vendor=vendor,
            status=STATUS_ERROR,
            message="bridge name required",
            destructive=True,
            executed=False,
        )
    br = bridge.strip()
    dp = (datapath_type or "system").strip()
    commands: List[CommandResult] = []
    add = _ovs_set(
        ["ovs-vsctl", "--may-exist", "add-br", br],
        operation="create_ovs_bridge",
        vendor=vendor,
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
        message="add-br %s" % br,
    )
    if add.status == STATUS_SKIPPED and not add.commands:
        add.details["bridge"] = br
        add.details["datapath_type"] = dp
        return add
    commands.extend(add.commands)
    if add.status == STATUS_ERROR:
        # A real (non-dry-run) add-br actually failed on the far end --
        # do not fall through to the hardcoded STATUS_OK below, which
        # would silently report success for a command that never ran
        # successfully.
        add.details["bridge"] = br
        add.details["datapath_type"] = dp
        add.commands = commands
        return add
    if dp and dp.lower() != "system":
        typed = _ovs_set(
            ["ovs-vsctl", "set", "bridge", br, "datapath_type=%s" % dp],
            operation="create_ovs_bridge",
            vendor=vendor,
            dry_run=dry_run,
            runner=runner,
            command_exists_fn=command_exists_fn,
            message="set datapath_type=%s" % dp,
        )
        commands.extend(typed.commands)
        if typed.status == STATUS_ERROR:
            typed.commands = commands
            return typed
    LOG.info("create_ovs_bridge vendor=%s %s datapath_type=%s dry_run=%s", vendor, br, dp, dry_run)
    return OperationResult(
        operation="create_ovs_bridge",
        vendor=vendor,
        status=STATUS_OK,
        message="bridge %s datapath_type=%s dry_run=%s" % (br, dp, dry_run),
        destructive=True,
        executed=bool(not dry_run and add.executed),
        commands=commands,
        details={"bridge": br, "datapath_type": dp, "dry_run": dry_run},
    )


def add_representor_port(
    bridge: str,
    representor: str,
    interface_type: str = "system",
    dry_run: bool = True,
    *,
    vendor: str = "generic",
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
) -> OperationResult:
    """Attach an existing representor/port netdev. Name is not invented."""
    if not bridge or not representor:
        return OperationResult(
            operation="add_representor_port",
            vendor=vendor,
            status=STATUS_ERROR,
            message="bridge and representor netdev are required (not guessed)",
            destructive=True,
            executed=False,
        )
    br = bridge.strip()
    port = representor.strip()
    add = _ovs_set(
        ["ovs-vsctl", "--may-exist", "add-port", br, port],
        operation="add_representor_port",
        vendor=vendor,
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
        message="add-port %s %s" % (br, port),
    )
    if add.status == STATUS_SKIPPED and not add.commands:
        return add
    commands = list(add.commands)
    if add.status == STATUS_ERROR:
        # Real add-port failed on the far end; do not mask it with the
        # hardcoded STATUS_OK below.
        add.commands = commands
        return add
    itype = (interface_type or "system").strip()
    if itype.lower() != "system":
        typed = _ovs_set(
            ["ovs-vsctl", "set", "Interface", port, "type=%s" % itype],
            operation="add_representor_port",
            vendor=vendor,
            dry_run=dry_run,
            runner=runner,
            command_exists_fn=command_exists_fn,
            message="set Interface %s type=%s" % (port, itype),
        )
        commands.extend(typed.commands)
        if typed.status == STATUS_ERROR:
            typed.commands = commands
            return typed
    LOG.info("add_representor_port vendor=%s %s on %s type=%s dry_run=%s", vendor, port, br, itype, dry_run)
    return OperationResult(
        operation="add_representor_port",
        vendor=vendor,
        status=STATUS_OK,
        message="port %s on %s type=%s dry_run=%s" % (port, br, itype, dry_run),
        destructive=True,
        executed=bool(not dry_run and add.executed),
        commands=commands,
        details={
            "bridge": br,
            "representor": port,
            "interface_type": itype,
            "dry_run": dry_run,
        },
    )


def add_flow(
    bridge: str,
    flow: str,
    dry_run: bool = True,
    *,
    vendor: str = "generic",
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
) -> OperationResult:
    """ovs-ofctl add-flow. Flow string is caller-provided, not synthesized here.

    Symbolic (non-numeric) in_port/output port names require an OVS/OpenFlow
    build that supports them; confirm against your ovs-ofctl version before
    relying on this outside dry-run.
    """
    if not bridge or not flow:
        return OperationResult(
            operation="add_flow",
            vendor=vendor,
            status=STATUS_ERROR,
            message="bridge and flow are required",
            destructive=True,
            executed=False,
        )
    return _ovs_set(
        ["ovs-ofctl", "add-flow", bridge.strip(), flow.strip()],
        operation="add_flow",
        vendor=vendor,
        dry_run=dry_run,
        runner=runner,
        command_exists_fn=command_exists_fn,
        message="add-flow %s %s" % (bridge, flow),
        extra_details={"flow": flow},
    )


def dump_ovs_state(
    *,
    vendor: str = "generic",
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[ExistsFn] = None,
    dry_run: bool = True,
) -> OperationResult:
    """Read-only ovs-vsctl show + dpctl/show + capability probe."""
    exists = command_exists_fn or command_exists
    run_fn = runner or run
    commands: List[CommandResult] = []
    if not exists("ovs-vsctl"):
        return OperationResult(
            operation="dump_ovs_state",
            vendor=vendor,
            status=STATUS_SKIPPED,
            message="ovs-vsctl not found",
            executed=False,
            details={"show": None, "dpctl": None},
        )
    show = run_fn(["ovs-vsctl", "show"], dry_run=dry_run, timeout=15.0, reason="ovs dump")
    commands.append(show)
    dpctl = None
    if exists("ovs-appctl"):
        dpctl = run_fn(
            ["ovs-appctl", "dpctl/show"],
            dry_run=dry_run,
            timeout=15.0,
            reason="ovs dpctl",
        )
        commands.append(dpctl)
    cap = probe_ovs_capabilities(vendor=vendor, runner=runner, command_exists_fn=command_exists_fn, dry_run=dry_run)
    commands.extend(cap.commands)
    return OperationResult(
        operation="dump_ovs_state",
        vendor=vendor,
        status=STATUS_OK,
        message="captured ovs-vsctl show (offload not inferred)",
        executed=not dry_run and bool(show.executed),
        destructive=False,
        commands=commands,
        details={
            "show": show.stdout,
            "dpctl": None if dpctl is None else dpctl.stdout,
            "capabilities": cap.details,
        },
    )


def _ovs_set(
    argv: Sequence[str],
    *,
    operation: str,
    vendor: str,
    dry_run: bool,
    runner: Optional[RunFn],
    command_exists_fn: Optional[ExistsFn],
    message: str,
    extra_details: Optional[Dict[str, Any]] = None,
) -> OperationResult:
    exists = command_exists_fn or command_exists
    tool = argv[0] if argv else "ovs-vsctl"
    if not exists(tool):
        return OperationResult(
            operation=operation,
            vendor=vendor,
            status=STATUS_SKIPPED,
            message="%s not found; %s" % (tool, message),
            destructive=True,
            executed=False,
            details={"dry_run": dry_run, **(extra_details or {})},
        )
    run_fn = runner or run
    cmd = run_fn(list(argv), dry_run=dry_run, timeout=15.0, reason=operation)
    status = STATUS_OK
    if not dry_run and (cmd.timed_out or not cmd.executed or cmd.returncode not in (0, None)):
        status = STATUS_ERROR
    msg = "%s dry_run=%s" % (message, dry_run)
    if not dry_run and cmd.timed_out:
        msg = "%s timed out" % message
    elif not dry_run and not cmd.executed:
        msg = "%s not executed: %s" % (message, cmd.reason or "command runner refused")
    return OperationResult(
        operation=operation,
        vendor=vendor,
        status=status,
        message=msg,
        destructive=True,
        executed=bool(cmd.executed),
        commands=[cmd],
        details={"dry_run": dry_run, "argv": list(argv), **(extra_details or {})},
    )
