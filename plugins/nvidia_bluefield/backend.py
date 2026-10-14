"""NVIDIA BlueField VendorBackend — discovery implemented; configure still stubs."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

from ..base.backend import VendorBackend
from ..base.commands import command_exists, planned_command, run
from ..base.models import (
    STATUS_ERROR,
    STATUS_NOT_IMPLEMENTED,
    STATUS_OK,
    STATUS_SKIPPED,
    ForwardingIntent,
    ForwardingResult,
    OperationResult,
    VendorCapability,
)
from ..base.remote import RemoteCommandRunner, ssh_params
from . import discovery, ovs, switchdev, telemetry, vdpa

LOG = logging.getLogger(__name__)


class NvidiaBlueFieldBackend(VendorBackend):
    def __init__(self, lab_config: Optional[dict] = None) -> None:
        self._lab = dict(lab_config) if lab_config else {}
        self._remote = RemoteCommandRunner()

    @property
    def backend_id(self) -> str:
        return "nvidia"

    def _dpu_forwarding(self) -> bool:
        """Host OVS is opt-in (nvidia-connectx-host-switchdev). Default is DPU mode."""
        nvidia = self._lab.get("nvidia") if isinstance(self._lab.get("nvidia"), dict) else {}
        if nvidia.get("mode") == "host-switchdev":
            return False
        if nvidia.get("ovs_location") == "host":
            return False
        if nvidia.get("allow_host_ovs_for_forwarding") is True:
            return False
        return True

    def _ovs_runner(self, dry_run: bool = True):
        if not self._dpu_forwarding():
            return run
        params = ssh_params(self._lab)
        if not params.get("host") or not params.get("user"):
            LOG.warning(
                "nvidia DPU mode: missing dpu.management_ip/ssh_user; "
                "planning SSH placeholders, not host ovs-vsctl"
            )

            def _placeholder(argv, dry_run=True, timeout=None, reason=None, **_kwargs):
                return planned_command(
                    ["ssh", "-o", "BatchMode=yes", "<DPU_SSH>", "--"] + list(argv),
                    (reason or "dpu ovs") + " (DPU SSH incomplete; not host OVS)",
                )

            return _placeholder
        remote = self._remote

        def _run(argv, dry_run=True, timeout=None, reason=None, **_kwargs):
            # Honor the caller's dry_run. Unit tests and default labctl
            # paths pass dry_run=True; live SSH only happens when an
            # apply path explicitly opts in.
            #
            # plugins.nvidia_bluefield.ovs hardcodes a fixed timeout=15.0
            # guess for every ovs-vsctl/ovs-ofctl call (it has no notion
            # of DPU config). That is too short for links where a single
            # SSH round trip stalls on the DPU's own `sudo` hostname
            # lookup (see docs/operations/dpu-remote-execution.md) --
            # the same failure mode already fixed for
            # discover-representors. Use dpu.command_timeout_seconds as
            # a floor so slow-link DPUs get real headroom here too,
            # while still respecting a caller who explicitly asks for
            # more than that.
            effective_timeout = params["command_timeout_seconds"]
            if timeout is not None and timeout > effective_timeout:
                effective_timeout = timeout
            return remote.run_ssh(
                params["host"],
                params["user"],
                list(argv),
                key_path=params.get("key_path"),
                port=int(params.get("port") or 22),
                dry_run=dry_run,
                timeout=effective_timeout,
                reason=reason,
                sudo=bool(params.get("sudo")),
            )

        return _run

    def discover(self) -> OperationResult:
        return discovery.discover(dry_run=False)

    def validate_prerequisites(self) -> OperationResult:
        tools = {
            "lspci": command_exists("lspci"),
            "devlink": command_exists("devlink"),
            "ip": command_exists("ip"),
            "ethtool": command_exists("ethtool"),
            "ovs-vsctl": command_exists("ovs-vsctl"),
        }
        required = ("lspci", "ip")
        missing_required = [name for name in required if not tools[name]]
        if missing_required:
            status = STATUS_SKIPPED
            message = "missing %s; NVIDIA discovery will skip those probes" % (
                ",".join(missing_required),
            )
        else:
            status = STATUS_OK
            message = "read-only probes available; ovs-vsctl optional=%s" % tools["ovs-vsctl"]
        LOG.info("nvidia validate_prerequisites: %s", tools)
        return OperationResult(
            operation="validate_prerequisites",
            vendor=self.backend_id,
            status=status,
            message=message,
            executed=True,
            destructive=False,
            capabilities=VendorCapability(vendor=self.backend_id),
            details={"tools": tools},
        )

    def configure_sriov(
        self,
        pf: Optional[str] = None,
        numvfs: Optional[int] = None,
        dry_run: bool = True,
    ) -> OperationResult:
        """Set sriov_numvfs on the host PF netdev via sysfs.

        pf is the PF *netdev name* (e.g. "ens1f0np0"), not a PCI address
        -- this matches tools/labctl.py's stage_prepare_vfs, which passes
        dpu.pf_name, and the host-side sriov_numvfs sysfs file, which is
        keyed by netdev under /sys/class/net/<pf>/device/. This always
        runs locally (never over the DPU management SSH channel): VF
        creation is a host-OS operation even on a BlueField DPU-mode
        profile, distinct from the DPU Arm-side OVS/representor config
        that self._ovs_runner() targets.
        """
        result = switchdev.configure_sriov_numvfs(pf, numvfs, dry_run=dry_run, force=False)
        result.operation = "configure_sriov"
        return result

    def configure_switchdev(self, pf: Optional[str] = None) -> OperationResult:
        return switchdev.set_eswitch_mode_switchdev(pf or "", dry_run=True, force=False)

    def discover_representors(self, pf: Optional[str] = None) -> OperationResult:
        if self._dpu_forwarding():
            runner = self._ovs_runner(dry_run=True)
            planned = []
            for argv in (
                ["ip", "-d", "link", "show"],
                ["devlink", "port", "show"],
                ["ovs-vsctl", "show"],
            ):
                planned.append(runner(argv, dry_run=True, reason="dpu representor discovery"))
            return OperationResult(
                operation="discover_representors",
                vendor=self.backend_id,
                status=STATUS_OK,
                message="DPU-side representor probes planned via RemoteCommandRunner (not host OVS)",
                executed=False,
                destructive=False,
                commands=planned,
                details={
                    "location": "dpu",
                    "pf": pf,
                    "host_mapping": False,
                    "strategy": "nvidia-portname",
                },
            )
        return discovery.discover_representors(pf=pf, dry_run=False)

    def configure_ovs_bridge(self, bridge: Optional[str] = None) -> OperationResult:
        runner = self._ovs_runner(dry_run=True)
        exists = (lambda _n: True) if self._dpu_forwarding() else command_exists
        datapath = ((self._lab.get("nvidia") or {}).get("datapath") or "system")
        dp = "doca" if str(datapath).lower() in ("ovs-doca", "doca") else "system"
        return ovs.create_ovs_bridge(
            bridge or "br-int",
            datapath_type=dp,
            dry_run=True,
            runner=runner,
            command_exists_fn=exists,
        )

    def configure_vdpa(self, vf: Optional[str] = None) -> OperationResult:
        return vdpa.plan_configure(vf)

    def collect_telemetry(self, source_id: Optional[str] = None) -> OperationResult:
        if self._dpu_forwarding():
            runner = self._ovs_runner(dry_run=True)
            show = runner(["ovs-vsctl", "show"], dry_run=True, reason="dpu ovs evidence")
            return OperationResult(
                operation="collect_telemetry",
                vendor=self.backend_id,
                status=STATUS_OK,
                message="DPU-side ovs-vsctl show planned; no fake counters",
                executed=False,
                telemetry=[],
                commands=[show],
                details={"location": "dpu", "source_id": source_id},
            )
        return telemetry.collect(source_id)

    def validate_offload(self) -> OperationResult:
        if self._dpu_forwarding():
            runner = self._ovs_runner(dry_run=True)
            bridge = ((self._lab.get("ovs") or {}).get("bridge") or "br-int")
            commands = [
                runner(["ovs-vsctl", "show"], dry_run=True, reason="dpu offload: ovs-vsctl show"),
                runner(
                    ["ovs-ofctl", "dump-flows", bridge],
                    dry_run=True,
                    reason="dpu offload: ovs-ofctl dump-flows",
                ),
                runner(["ovs-appctl", "dpctl/show"], dry_run=True, reason="dpu offload: dpctl/show"),
                runner(
                    ["ovs-appctl", "dpctl/dump-flows"],
                    dry_run=True,
                    reason="dpu offload: dpctl/dump-flows",
                ),
            ]
            return OperationResult(
                operation="validate_offload",
                vendor=self.backend_id,
                status=STATUS_OK,
                message=(
                    "DPU-side OVS/OVS-DOCA probes planned; offload_state is unknown. "
                    "No fake offloaded:yes."
                ),
                executed=False,
                flows=[],
                commands=commands,
                details={"offload_state": None, "location": "dpu", "offloaded": None},
            )
        LOG.info("nvidia: validate_offload host path; will not claim offloaded:yes")
        return OperationResult(
            operation="validate_offload",
            vendor=self.backend_id,
            status=STATUS_NOT_IMPLEMENTED,
            message="TODO: tools/offload-checker; unknown is valid. No fake in_hw.",
            executed=False,
            flows=[],
            details={"offload_state": None, "location": "host"},
        )

    def configure_forwarding(self, intent: ForwardingIntent, dry_run: bool = True) -> ForwardingResult:
        """Plan or execute an OVS bridge + representor L2 forwarding path.

        This is the most complete configure_forwarding() of the four
        vendors today because plugins.nvidia_bluefield.ovs already has
        real OVS planning helpers. dry_run mirrors tools/labctl.py's
        StageContext.dry_run (True unless the caller passed --apply and
        the apply-readiness gate passed): every ovs-vsctl/ovs-ofctl call
        below is only actually sent over SSH to the DPU when dry_run is
        False, same as configure_sriov above.
        """
        forwarding_entity = "NVIDIA BlueField/ConnectX E-Switch"
        control_entity = (
            "labctl -> nvidia backend -> DPU-side OVS/OVS-DOCA -> BlueField E-Switch"
        )
        if not self._dpu_forwarding():
            forwarding_entity = "NVIDIA BlueField/ConnectX E-Switch (host path)"
            control_entity = (
                "labctl -> nvidia backend -> host OVS (advanced profile nvidia-connectx-host-switchdev)"
            )
        runner = self._ovs_runner(dry_run=dry_run)
        exists = (lambda _n: True) if self._dpu_forwarding() else command_exists
        warnings: list = []
        errors: list = []
        commands: list = []
        configured_ports: list = []
        configured_flows: list = []

        missing = [e.name for e in intent.endpoints if not e.representor]
        if missing:
            errors.append(
                "representor missing for endpoint(s) %s; run "
                "labctl vm2vm discover-representors before configure-forwarding"
                % ", ".join(missing)
            )
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message="cannot plan forwarding without representors",
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=errors,
                offload_requested=intent.offload.requested,
                details={"intent": intent.name},
            )

        bridge = intent.bridge or "br-int"
        datapath_type = str(intent.details.get("datapath_type") or "system")
        if datapath_type.lower() == "doca":
            br_result = ovs.create_ovs_doca_bridge(
                bridge, dry_run=dry_run, runner=runner, command_exists_fn=exists
            )
        else:
            br_result = ovs.create_ovs_bridge(
                bridge,
                datapath_type=datapath_type,
                dry_run=dry_run,
                runner=runner,
                command_exists_fn=exists,
            )
        commands.extend(br_result.commands)
        if br_result.status == STATUS_ERROR:
            errors.append(br_result.message)

        for endpoint in intent.endpoints:
            port_result = ovs.add_representor_port(
                bridge,
                endpoint.representor,
                dry_run=dry_run,
                runner=runner,
                command_exists_fn=exists,
            )
            commands.extend(port_result.commands)
            if port_result.status == STATUS_ERROR:
                errors.append(port_result.message)
            else:
                configured_ports.append(endpoint.representor)

        if intent.mode != "bridge":
            warnings.append("mode=%r not implemented; only 'bridge' is supported today" % intent.mode)

        if intent.explicit_flows.get("enabled"):
            if len(intent.endpoints) >= 2 and intent.policy.bidirectional:
                a, b = intent.endpoints[0], intent.endpoints[1]
                for src, dst in ((a, b), (b, a)):
                    flow = "in_port=%s,actions=output:%s" % (src.representor, dst.representor)
                    flow_result = ovs.add_flow(
                        bridge, flow, dry_run=dry_run, runner=runner, command_exists_fn=exists
                    )
                    commands.extend(flow_result.commands)
                    configured_flows.append(flow)
                warnings.append(
                    "explicit_flows uses symbolic port names in the planned "
                    "flow string; confirm exact ovs-ofctl/OpenFlow syntax for "
                    "your OVS build before relying on this under --apply"
                )
            else:
                warnings.append(
                    "explicit_flows.enabled requires >=2 endpoints and policy.bidirectional=true"
                )

        offload_configured: Optional[bool] = None
        if intent.offload.requested:
            hw_result = ovs.enable_ovs_hw_offload(
                dry_run=dry_run, runner=runner, command_exists_fn=exists
            )
            commands.extend(hw_result.commands)
            offload_configured = hw_result.status == STATUS_OK

        success = not errors
        if dry_run:
            message = "planned OVS bridge/representor forwarding (dry-run; no CLI executed)"
            offload_confirmed = False
        elif success:
            message = "configured OVS bridge/representor forwarding on the DPU"
            # "Confirmed" only when every planned command actually executed
            # and returned 0 -- not merely that the plan had no errors.
            offload_confirmed = bool(
                intent.offload.requested
                and offload_configured
                and all(
                    getattr(c, "executed", False) and getattr(c, "returncode", None) == 0
                    for c in commands
                )
            )
        else:
            message = "attempted to configure OVS bridge/representor forwarding on the DPU; see errors"
            offload_confirmed = False
        return ForwardingResult(
            success=success,
            vendor=self.backend_id,
            message=message,
            forwarding_entity=forwarding_entity,
            control_entity=control_entity,
            configured_endpoints=[e.name for e in intent.endpoints],
            configured_ports=configured_ports,
            configured_flows=configured_flows,
            offload_requested=intent.offload.requested,
            offload_configured=offload_configured,
            offload_confirmed=offload_confirmed,
            warnings=warnings,
            errors=errors,
            commands=commands,
            details={"bridge": bridge, "datapath_type": datapath_type},
        )

    def cleanup(self) -> OperationResult:
        if self._dpu_forwarding():
            from .dpu_ovs import plan_dpu_ovs_cleanup

            ovs_cfg = self._lab.get("ovs") or {}
            return plan_dpu_ovs_cleanup(
                self._lab,
                owned_bridge=ovs_cfg.get("bridge"),
                owned_ports=list(ovs_cfg.get("ports") or []),
                lab_owned_bridge=bool(ovs_cfg.get("lab_owned")),
                dry_run=True,
                runner=self._remote,
            )
        LOG.warning("nvidia: host-path cleanup stub — will not delete VFs/bridges")
        return OperationResult(
            operation="cleanup",
            vendor=self.backend_id,
            status=STATUS_NOT_IMPLEMENTED,
            message="TODO: documented rollback only; host-switchdev cleanup is advanced-profile",
            destructive=True,
            executed=False,
        )
