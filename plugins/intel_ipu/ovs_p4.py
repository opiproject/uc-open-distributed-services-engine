"""Intel IPU OVS control plane -- an alternate P4Runtime *client*, not a
separate hardware mechanism from plugins/intel_ipu/p4runtime.py.

Per IPDK's own docs (https://ipdk.io/p4cp-userguide/apps/lnw/es2k/
es2k-linux-networking.html) and https://github.com/ipdk-io/ipu-playbook's
`ovs_offload_lnw.py`:

    `infrap4d`: includes a P4Runtime server... programs IPU E2100 via TDI.
    `ovs-vswitchd`: integrated with p4runtime intelligence and acts as a
    gRPC client. Programs IPU E2100 ... by communicating with the gRPC
    server.

In other words, the patched/"enhanced" `ovs-vswitchd` IPDK ships is a
P4Runtime CLIENT of the same `infrap4d` gRPC server that
plugins/intel_ipu/p4runtime.py's mode="cli" drives directly via
`p4rt-ctl`. Choosing this module over p4runtime.py is a choice of
*front-end* (ordinary `ovs-vsctl`/`ovs-ofctl` bridge/port semantics,
plus dynamic L2 MAC learning so simple bridging needs no FLOW string at
all) onto the *same* backend -- not a fallback of it, and not a
separate/independent hardware datapath the way NVIDIA's OVS-TC/OVS-DOCA
choice is (see docs/vendor-readiness/intel-ipu-ovs-p4.md).

This deliberately reuses plugins/base/ovs.py's generic add-br/add-port/
add-flow/capability-probe mechanics (vendor="intel") instead of
reimplementing them -- the same ovs-vsctl/ovs-ofctl primitives NVIDIA's
plugins/nvidia_bluefield/ovs.py already exercises. What's Intel-specific
here is only the transport: every command runs over SSH to the ACC (Arm
Compute Complex) using the same `dpu:` SSH block p4runtime.py's mode="cli"
and NVIDIA's DPU-mode SSH already use (plugins.base.remote.ssh_params) --
there is no "host OVS" fallback for Intel the way NVIDIA has one.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from ..base import ovs as base_ovs
from ..base.models import CommandResult, OperationResult
from ..base.remote import RemoteCommandRunner, ssh_params

LOG = logging.getLogger(__name__)
VENDOR = "intel"


def _acc_runner(cfg: Mapping[str, Any], dry_run: bool):
    """Build a runner callable that SSHes to the ACC, or plans a clear
    'not executed' CommandResult when the ACC SSH target isn't configured
    -- mirrors plugins/intel_ipu/p4runtime.py's P4RuntimeClient._run_cli.
    """
    params = ssh_params(dict(cfg))
    host = params.get("host")
    user = params.get("user")
    if not host or not user:

        def _missing(argv, dry_run=True, timeout=None, reason=None, **_kwargs):
            return CommandResult(
                argv=list(argv),
                executed=False,
                dry_run=True,
                reason=(
                    "ovs control plane has no ACC SSH target configured "
                    "(dpu.management_ip/dpu.ssh_user); see "
                    "config/lab-intel-vm2vm.example.yaml"
                )
                + (" (%s)" % reason if reason else ""),
            )

        return _missing

    remote = RemoteCommandRunner()

    def _run(argv, dry_run=True, timeout=None, reason=None, **_kwargs):
        effective_timeout = params["command_timeout_seconds"]
        if timeout is not None and timeout > effective_timeout:
            effective_timeout = timeout
        return remote.run_ssh(
            host,
            user,
            list(argv),
            key_path=params.get("key_path"),
            port=int(params.get("port") or 22),
            dry_run=dry_run,
            timeout=effective_timeout,
            reason=reason,
            sudo=bool(params.get("sudo")),
        )

    return _run


def _always_present(_name: str) -> bool:
    # Same convention as plugins.nvidia_bluefield.backend's DPU-mode
    # `exists`: the tool lives on the remote ACC, not on this host, so a
    # local command_exists() check would always (wrongly) say "missing".
    # Let the SSH command itself surface a real "not found" if it isn't.
    return True


def probe_capabilities(cfg: Mapping[str, Any], dry_run: bool = True) -> OperationResult:
    """ovs-vsctl get Open_vSwitch . datapath_types/iface_types/other_config
    over ACC SSH. Read-only; does not mutate."""
    return base_ovs.probe_ovs_capabilities(
        vendor=VENDOR,
        runner=_acc_runner(cfg, dry_run),
        command_exists_fn=_always_present,
        dry_run=dry_run,
    )


def create_bridge(cfg: Mapping[str, Any], bridge: str, dry_run: bool = True) -> OperationResult:
    """ovs-vsctl --may-exist add-br <bridge> over ACC SSH."""
    return base_ovs.create_ovs_bridge(
        bridge,
        datapath_type="system",
        dry_run=dry_run,
        vendor=VENDOR,
        runner=_acc_runner(cfg, dry_run),
        command_exists_fn=_always_present,
    )


def add_port(cfg: Mapping[str, Any], bridge: str, representor: str, dry_run: bool = True) -> OperationResult:
    """ovs-vsctl --may-exist add-port <bridge> <representor> over ACC SSH.

    representor must already be known (from forwarding.endpoints[].representor
    in the lab config, or a proven discover-representors result) -- this
    never invents a port representor name.
    """
    return base_ovs.add_representor_port(
        bridge,
        representor,
        dry_run=dry_run,
        vendor=VENDOR,
        runner=_acc_runner(cfg, dry_run),
        command_exists_fn=_always_present,
    )


def add_flow(cfg: Mapping[str, Any], bridge: str, flow: str, dry_run: bool = True) -> OperationResult:
    """ovs-ofctl add-flow <bridge> <flow> over ACC SSH. flow is caller-provided.

    Only needed for explicit_flows; simple bidirectional L2 bridging does
    not require this -- ovs-vswitchd's dynamic MAC learning (first packet
    to each representor) programs it via P4Runtime automatically per the
    IPDK Linux Networking recipe.
    """
    return base_ovs.add_flow(
        bridge,
        flow,
        dry_run=dry_run,
        vendor=VENDOR,
        runner=_acc_runner(cfg, dry_run),
        command_exists_fn=_always_present,
    )


def dump_state(cfg: Mapping[str, Any], dry_run: bool = True) -> OperationResult:
    """Read-only ovs-vsctl show + dpctl/show + capability probe over ACC SSH."""
    return base_ovs.dump_ovs_state(
        vendor=VENDOR,
        runner=_acc_runner(cfg, dry_run),
        command_exists_fn=_always_present,
        dry_run=dry_run,
    )


def probe_control_plane_live(cfg: Mapping[str, Any]) -> Optional[bool]:
    """Best-effort, honest live proof for `ovs_p4_control_plane` (see
    vm_to_vm_capability.py). Only ever called when the dual gate there
    (intel.control_plane == "ovs" and remote_execution.dry_run_default ==
    false) already passed.

    Returns True only when every underlying ovs-vsctl SSH call actually
    executed and returned 0 (OVS reachable and functioning on the ACC).
    Returns None (not proven, not disproven) on anything short of that --
    an unreachable ACC or missing ovs-vsctl means "discovery-required",
    not "confirmed absent".
    """
    result = probe_capabilities(cfg, dry_run=False)
    cmds = list(result.commands or [])
    if not cmds or not all(c.executed and c.returncode == 0 for c in cmds):
        return None
    return True
