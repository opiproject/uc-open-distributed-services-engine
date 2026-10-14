"""Intel IPU VendorBackend — discovery + OPI/P4Runtime/OVS control planes.

No NVIDIA-style OVS-DOCA path (that is a genuine NVIDIA/DOCA hardware
concept -- see plugins/nvidia_bluefield/ovs.py). The `ovs` control plane
here (see plugins/intel_ipu/ovs_p4.py) is an alternate P4Runtime
*client*, not a separate hardware datapath from the `p4` control plane.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from ..base import sriov as _sriov
from ..base.backend import VendorBackend
from ..base.commands import command_exists
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
from . import discovery, dpu_operator, opi, ovs_p4, p4runtime, telemetry
from .opi import OpiClient

LOG = logging.getLogger(__name__)


class IntelIpuBackend(VendorBackend):
    def __init__(self, config: Optional[dict] = None) -> None:
        self._config = dict(config) if config else None

    @property
    def backend_id(self) -> str:
        return "intel"

    def discover(self) -> OperationResult:
        return discovery.discover(config=self._config)

    def validate_prerequisites(self) -> OperationResult:
        cfg = self._config if self._config is not None else discovery.load_config()
        tools = {
            "lspci": command_exists("lspci"),
            "ip": command_exists("ip"),
            "devlink": command_exists("devlink"),
        }
        public = discovery.config_public_view(cfg)
        redfish = bool(public.get("redfish_base"))
        opi_ep = bool(public.get("opi_endpoint"))
        cli = public.get("management_cli")
        LOG.info("intel prerequisites tools=%s redfish=%s opi=%s cli=%s", tools, redfish, opi_ep, cli)
        if cli:
            message = (
                "management_cli %r is configured but not executed "
                "(no assumed IMC tool)" % cli
            )
        else:
            message = "no management CLI assumed; Redfish/OPI from config only"
        status = STATUS_OK if (tools["lspci"] or redfish or opi_ep) else STATUS_SKIPPED
        return OperationResult(
            operation="validate_prerequisites",
            vendor=self.backend_id,
            status=status,
            message=message,
            executed=True,
            destructive=False,
            capabilities=VendorCapability(vendor=self.backend_id, notes="OPI-first"),
            details={
                "tools": tools,
                "config": public,
                "redfish_configured": redfish,
                "redfish_probed": False,
                "opi_configured": opi_ep,
                "management_cli_executed": False,
            },
        )

    def configure_sriov(
        self,
        pf: Optional[str] = None,
        numvfs: Optional[int] = None,
        dry_run: bool = True,
    ) -> OperationResult:
        """Set sriov_numvfs on the IPU uplink PF netdev via sysfs.

        /sys/class/net/<pf>/device/sriov_numvfs is the standard
        upstream-kernel PCI SR-IOV control file (see
        plugins/base/sriov.py) -- every SR-IOV-capable PF driver
        implements it, including the ice/idpf driver that binds the
        IPU's host-facing PF (discovery.py's physical_functions[].netdev,
        which is what config's dpu.pf_name should be filled with). This
        is *not* the same claim as switchdev/eswitch mode, OVS-DOCA
        offload, or a proven OPI/P4Runtime forwarding control plane --
        those remain discovery-required/refused elsewhere in this
        backend (configure_switchdev, configure_ovs_bridge,
        configure_forwarding). Creating VFs on the PF is a plain host-OS
        SR-IOV operation, independent of which (if any) control plane
        will later program forwarding for those VFs.
        """
        if not pf:
            return OperationResult(
                operation="configure_sriov",
                vendor=self.backend_id,
                status=STATUS_SKIPPED,
                message=(
                    "pf (IPU uplink PF netdev, e.g. dpu.pf_name from `discover`) "
                    "is required; not guessed"
                ),
                destructive=True,
                executed=False,
            )
        result = _sriov.configure_sriov_numvfs(
            pf, numvfs, dry_run=dry_run, vendor=self.backend_id, force=False
        )
        result.operation = "configure_sriov"
        return result

    def configure_switchdev(self, pf: Optional[str] = None) -> OperationResult:
        LOG.warning("intel: switchdev not assumed")
        return OperationResult(
            operation="configure_switchdev",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="Do not copy NVIDIA eswitch. pf=%s" % pf,
            destructive=True,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id, switchdev=None),
            details={"opi": opi.plan_map().message},
        )

    def discover_representors(self, pf: Optional[str] = None) -> OperationResult:
        result = discovery.discover(config=self._config)
        result.operation = "discover_representors"
        result.representors = []
        result.message = (
            "representors unknown for Intel IPU until datapath discovery; "
            "not guessed from netdev names. pf=%s" % pf
        )
        return result

    def configure_ovs_bridge(self, bridge: Optional[str] = None) -> OperationResult:
        """Plan (never apply here) the ACC-side OVS bridge, but only when
        `intel.control_plane: ovs` is explicitly selected -- see
        plugins/intel_ipu/ovs_p4.py. This mirrors NVIDIA's
        `prepare-ovs`/`configure_ovs_bridge`, which also always plans with
        dry_run=True; real bridge/port creation happens under
        `configure-forwarding --apply` (see _submit_ovs_forwarding below).

        For opi/p4/discovery control planes this still refuses: Intel's
        OPI/DPU-Operator and raw P4Runtime (p4rt-ctl) paths do not use a
        host/ACC OVS bridge the way this ovs control plane does.
        """
        cfg = self._config if self._config is not None else discovery.load_config()
        intel_cfg = cfg.get("intel") if isinstance(cfg.get("intel"), dict) else {}
        if str(intel_cfg.get("control_plane") or "").lower() != "ovs":
            return OperationResult(
                operation="configure_ovs_bridge",
                vendor=self.backend_id,
                status=STATUS_SKIPPED,
                message=(
                    "intel.control_plane is not 'ovs' (OPI/DPU Operator or raw "
                    "P4Runtime/p4rt-ctl do not use a host/ACC OVS bridge); "
                    "set intel.control_plane: ovs to use this path instead. bridge=%s"
                    % bridge
                ),
                destructive=True,
                executed=False,
            )
        ovs_cfg = cfg.get("ovs") if isinstance(cfg.get("ovs"), dict) else {}
        result = ovs_p4.create_bridge(cfg, bridge or ovs_cfg.get("bridge") or "br-int", dry_run=True)
        result.operation = "configure_ovs_bridge"
        return result

    def configure_vdpa(self, vf: Optional[str] = None) -> OperationResult:
        return OperationResult(
            operation="configure_vdpa",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="vDPA discovery-required. vf=%s" % vf,
            destructive=True,
            executed=False,
        )

    def collect_telemetry(self, source_id: Optional[str] = None) -> OperationResult:
        client = OpiClient.from_config(self._config)
        if client.base_url or client.mock_devices:
            return client.get_telemetry(source_id or "")
        p4_client = p4runtime.P4RuntimeClient.from_config(self._config)
        if p4_client.endpoint or p4_client.has_mock_entries:
            return p4_client.read_counters(source_id or "")
        return telemetry.collect(source_id)

    def validate_offload(self) -> OperationResult:
        return OperationResult(
            operation="validate_offload",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="Offload unproven; not offloaded:yes",
            executed=False,
            flows=[],
            details={"offload_state": None},
        )

    def configure_forwarding(self, intent: ForwardingIntent, dry_run: bool = True) -> ForwardingResult:
        """Refuse OVS. Capability must be proven; otherwise discovery-required."""
        from .vm_to_vm_capability import (
            INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED,
            check_vm_to_vm_forwarding_capability,
        )

        forwarding_entity = (
            "Intel IPU packet pipeline, P4 path, or DPU-side network function "
            "(exact mechanism discovery-required)"
        )
        # Default control_entity for the not-proven / discovery-required case
        # below, where we don't yet know which control plane (if any) will
        # end up being used.
        control_entity = "labctl -> intel backend -> OPI/DPU Operator style API or P4Runtime/IPDK -> IPU pipeline or network function"
        cap = check_vm_to_vm_forwarding_capability(self._config)
        if not (cap.details or {}).get("capability_proven"):
            errors = list((cap.details or {}).get("errors") or [INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED])
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message=cap.message,
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=errors,
                offload_requested=intent.offload.requested,
                details={
                    "discovery_required": True,
                    "capability": cap.details,
                    "ovs_attempted": False,
                    "verdict": "UNSUPPORTED",
                },
            )
        cfg = self._config if self._config is not None else discovery.load_config()
        public = discovery.config_public_view(cfg)
        # opi_or_plugin_l2_api, p4_or_pipeline_path, and ovs_p4_control_plane
        # are independent -- any one is sufficient (see
        # vm_to_vm_capability.py) -- build the placeholder that matches
        # whichever one actually proved capability rather than always
        # assuming OPI. Note "ovs" here is a P4Runtime *client* choice
        # (ordinary ovs-vsctl/ovs-ofctl instead of p4rt-ctl), not a
        # separate hardware datapath the way NVIDIA's OVS-TC/OVS-DOCA
        # choice is -- see plugins/intel_ipu/ovs_p4.py.
        control_plane = (cap.details or {}).get("control_plane")
        if control_plane == "p4":
            control_entity = "labctl -> intel backend -> P4Runtime (IPDK infrap4d) -> IPU P4 pipeline"
            p4_cfg = cfg.get("p4") if isinstance(cfg.get("p4"), dict) else {}
            placeholder = {
                "p4runtime": "p4.v1/WriteRequest-placeholder",
                "table_entries": [
                    {
                        "id": "%s-%s" % (intent.name, e.name),
                        "match": {"vf_index": e.vf_index, "representor": e.representor},
                        "action": "forward",
                        "bidirectional": intent.policy.bidirectional,
                    }
                    for e in intent.endpoints
                ],
            }
            details = {
                "p4_placeholder": placeholder,
                "p4_endpoint": p4_cfg.get("endpoint"),
                "ovs_attempted": False,
            }
            if dry_run:
                message = "P4Runtime-style placeholder table entries built; not submitted (still dry-run)"
            else:
                return self._submit_p4_forwarding(intent, cfg, p4_cfg, forwarding_entity, control_entity, details)
        elif control_plane == "ovs":
            control_entity = (
                "labctl -> intel backend -> OVS (P4Runtime-backed ovs-vswitchd/ovs-p4rt "
                "client) -> IPDK infrap4d -> IPU P4 pipeline"
            )
            ovs_cfg = cfg.get("ovs") if isinstance(cfg.get("ovs"), dict) else {}
            bridge = intent.bridge or ovs_cfg.get("bridge") or "br-int"
            placeholder = {
                "bridge": bridge,
                "ports": [{"endpoint": e.name, "representor": e.representor} for e in intent.endpoints],
                "bidirectional": intent.policy.bidirectional,
                "note": (
                    "simple bidirectional L2 bridging needs no explicit flow -- "
                    "ovs-vswitchd's dynamic MAC learning programs it via "
                    "P4Runtime once representors are attached"
                ),
            }
            details = {"ovs_placeholder": placeholder, "ovs_bridge": bridge, "ovs_attempted": False}
            if dry_run:
                message = "OVS bridge/representor placeholder built; not submitted (still dry-run)"
            else:
                return self._submit_ovs_forwarding(intent, cfg, bridge, forwarding_entity, control_entity, details, dry_run)
        else:
            placeholder = {
                "apiVersion": "opi.odse/v1alpha1-placeholder",
                "kind": "NetworkAttachment",
                "metadata": {"name": intent.name},
                "spec": {
                    "endpoints": [e.name for e in intent.endpoints],
                    "bidirectional": intent.policy.bidirectional,
                },
            }
            control_entity = "labctl -> intel backend -> OPI/DPU Operator style API -> IPU pipeline or network function"
            details = {"opi_placeholder": placeholder, "opi_endpoint": public.get("opi_endpoint"), "ovs_attempted": False}
            message = "OPI-style placeholder object built; not submitted (still dry-run)"
        return ForwardingResult(
            success=False,
            vendor=self.backend_id,
            message=message,
            forwarding_entity=forwarding_entity,
            control_entity=control_entity,
            warnings=["Intel VM-to-VM proven in samples only; no OVS/P4Runtime commands issued"],
            offload_requested=intent.offload.requested,
            details=details,
        )

    def _submit_p4_forwarding(
        self,
        intent: ForwardingIntent,
        cfg: dict,
        p4_cfg: dict,
        forwarding_entity: str,
        control_entity: str,
        details: dict,
    ) -> ForwardingResult:
        """--apply path for the P4 control plane: actually call
        P4RuntimeClient.write_table_entry() (real `p4rt-ctl add-entry` over
        SSH in mode="cli"; in-memory only in mode="mock"; not_implemented in
        mode="grpc") instead of stopping at the placeholder built above.

        This deliberately never invents a P4 FLOW string or table name --
        per plugins/intel_ipu/p4runtime.py's docstring, those are
        pipeline-specific and discovery-required. Each ForwardingEndpoint's
        vf_index must have a matching entry under `p4.forwarding_entries`
        (operator-supplied real {table, flow} for their compiled P4
        program, see docs/vendor-readiness/intel-ipu-p4-ipdk.md) or nothing
        is submitted and this refuses with a clear error naming the gap,
        the same "never guess" rule p4rt-ctl del-entry follows there.
        """
        configured = {
            entry.get("vf_index"): entry
            for entry in (p4_cfg.get("forwarding_entries") or [])
            if isinstance(entry, dict) and entry.get("vf_index") is not None
        }
        missing = [ep.name for ep in intent.endpoints if ep.vf_index not in configured]
        if missing:
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message=(
                    "cannot submit: no p4.forwarding_entries table/flow configured "
                    "for endpoint(s) %s; this client never invents a P4 FLOW string "
                    "-- see docs/vendor-readiness/intel-ipu-p4-ipdk.md" % ", ".join(missing)
                ),
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=[
                    "p4.forwarding_entries missing table/flow for endpoint(s): %s"
                    % ", ".join(missing)
                ],
                offload_requested=intent.offload.requested,
                details=details,
            )
        client = p4runtime.P4RuntimeClient.from_lab_config(cfg)
        commands: List[Any] = []
        submitted: List[dict] = []
        write_errors: List[str] = []
        for ep in intent.endpoints:
            entry_cfg = configured[ep.vf_index]
            entry_id = "%s-%s" % (intent.name, ep.name)
            write_result = client.write_table_entry(
                {
                    "id": entry_id,
                    "table": entry_cfg.get("table"),
                    "flow": entry_cfg.get("flow"),
                    "switch": entry_cfg.get("switch"),
                }
            )
            commands.extend(write_result.commands)
            ok = write_result.status == STATUS_OK and write_result.executed
            submitted.append(
                {
                    "endpoint": ep.name,
                    "entry_id": entry_id,
                    "status": write_result.status,
                    "executed": write_result.executed,
                    "message": write_result.message,
                }
            )
            if not ok:
                write_errors.append("%s: %s" % (entry_id, write_result.message))
        details["p4_mode"] = client.mode
        details["p4_submitted_entries"] = submitted
        success = bool(submitted) and not write_errors
        return ForwardingResult(
            success=success,
            vendor=self.backend_id,
            message=(
                "submitted %d P4Runtime table entr%s via p4rt-ctl (mode=%s)"
                % (len(submitted), "y" if len(submitted) == 1 else "ies", client.mode)
                if success
                else "P4Runtime submission failed for: %s" % "; ".join(write_errors)
            ),
            forwarding_entity=forwarding_entity,
            control_entity=control_entity,
            errors=write_errors,
            offload_requested=intent.offload.requested,
            commands=commands,
            details=details,
        )

    def _submit_ovs_forwarding(
        self,
        intent: ForwardingIntent,
        cfg: dict,
        bridge: str,
        forwarding_entity: str,
        control_entity: str,
        details: dict,
        dry_run: bool,
    ) -> ForwardingResult:
        """--apply path for the ovs control plane: actually create the
        bridge and attach each endpoint's representor over ACC SSH via
        plugins/intel_ipu/ovs_p4.py (ordinary ovs-vsctl, not p4rt-ctl).

        Every endpoint needs a real representor (from
        forwarding.endpoints[].representor in the lab config, or a proven
        `discover-representors` result) -- this never invents a
        representor name, same "never guess" rule _submit_p4_forwarding
        follows for FLOW strings. Unlike the P4 path, no per-flow
        table/action names are needed for simple bidirectional bridging:
        ovs-vswitchd's own dynamic MAC learning programs L2 forwarding via
        P4Runtime once representors are attached to the bridge (see
        module docstring in plugins/intel_ipu/ovs_p4.py); explicit_flows
        is only used when the caller opts into that instead.
        """
        missing = [ep.name for ep in intent.endpoints if not ep.representor]
        if missing:
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message=(
                    "cannot submit: no representor known for endpoint(s) %s; run "
                    "labctl vm2vm discover-representors or set "
                    "forwarding.endpoints[].representor -- this client never "
                    "invents a representor netdev name" % ", ".join(missing)
                ),
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=["representor missing for endpoint(s): %s" % ", ".join(missing)],
                offload_requested=intent.offload.requested,
                details=details,
            )
        commands: List[Any] = []
        errors: List[str] = []
        configured_ports: List[str] = []

        br_result = ovs_p4.create_bridge(cfg, bridge, dry_run=dry_run)
        commands.extend(br_result.commands)
        if br_result.status == STATUS_ERROR:
            errors.append(br_result.message)

        for ep in intent.endpoints:
            port_result = ovs_p4.add_port(cfg, bridge, ep.representor, dry_run=dry_run)
            commands.extend(port_result.commands)
            if port_result.status == STATUS_ERROR:
                errors.append(port_result.message)
            else:
                configured_ports.append(ep.representor)

        configured_flows: List[str] = []
        if intent.explicit_flows.get("enabled"):
            if len(intent.endpoints) >= 2 and intent.policy.bidirectional:
                a, b = intent.endpoints[0], intent.endpoints[1]
                for src, dst in ((a, b), (b, a)):
                    flow = "in_port=%s,actions=output:%s" % (src.representor, dst.representor)
                    flow_result = ovs_p4.add_flow(cfg, bridge, flow, dry_run=dry_run)
                    commands.extend(flow_result.commands)
                    if flow_result.status == STATUS_ERROR:
                        errors.append(flow_result.message)
                    else:
                        configured_flows.append(flow)
            else:
                errors.append("explicit_flows.enabled requires >=2 endpoints and policy.bidirectional=true")

        details["ovs_bridge"] = bridge
        details["ovs_configured_ports"] = configured_ports
        details["ovs_configured_flows"] = configured_flows
        details["ovs_attempted"] = True
        executed_all = bool(commands) and all(c.executed for c in commands)
        success = not errors and bool(configured_ports) and (dry_run or executed_all)
        if dry_run:
            message = "planned OVS bridge/representor forwarding over ACC SSH (dry-run; no command executed)"
        elif success:
            message = "configured OVS bridge/representor forwarding over ACC SSH (bridge=%s)" % bridge
        elif not executed_all:
            message = "OVS commands were planned but not executed (ACC SSH not configured or refused)"
        else:
            message = "OVS forwarding configuration failed: %s" % "; ".join(errors)
        return ForwardingResult(
            success=success,
            vendor=self.backend_id,
            message=message,
            forwarding_entity=forwarding_entity,
            control_entity=control_entity,
            errors=errors,
            offload_requested=intent.offload.requested,
            commands=commands,
            details=details,
        )

    def cleanup(self) -> OperationResult:
        dpu_operator.plan_reconcile()
        return OperationResult(
            operation="cleanup",
            vendor=self.backend_id,
            status=STATUS_NOT_IMPLEMENTED,
            message="TODO: no CR/Redfish delete in skeleton",
            destructive=True,
            executed=False,
        )
