"""AMD Pensando VendorBackend — discovery-first; no assumed OVS offload."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.backend import VendorBackend
from ..base.commands import command_exists
from ..base.models import (
    FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED,
    STATUS_NOT_IMPLEMENTED,
    STATUS_OK,
    STATUS_SKIPPED,
    ForwardingIntent,
    ForwardingResult,
    OperationResult,
    VendorCapability,
)
from . import discovery, ionic, rdma, telemetry

LOG = logging.getLogger(__name__)


class AmdPensandoBackend(VendorBackend):
    def __init__(self, lab_config: Optional[dict] = None) -> None:
        self._lab = dict(lab_config) if lab_config else {}

    @property
    def backend_id(self) -> str:
        return "amd"

    def discover(self) -> OperationResult:
        return discovery.discover()

    def validate_prerequisites(self) -> OperationResult:
        tools = {
            "lspci": command_exists("lspci"),
            "ip": command_exists("ip"),
            "ethtool": command_exists("ethtool"),
            "rdma": command_exists("rdma"),
            "devlink": command_exists("devlink"),
        }
        missing_ionic = "skip CI if no ionic module; not a fail"
        LOG.info("amd prerequisites %s", tools)
        status = STATUS_OK if tools["lspci"] or tools["ethtool"] else STATUS_SKIPPED
        return OperationResult(
            operation="validate_prerequisites",
            vendor=self.backend_id,
            status=status,
            message="ionic optional (%s)" % missing_ionic,
            executed=True,
            capabilities=VendorCapability(
                vendor=self.backend_id,
                switchdev=None,
                vdpa=None,
                ovs_hw_offload=False,
                sriov=None,
            ),
            details={"tools": tools},
        )

    def configure_sriov(
        self,
        pf: Optional[str] = None,
        numvfs: Optional[int] = None,
        dry_run: bool = True,
    ) -> OperationResult:
        return OperationResult(
            operation="configure_sriov",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="SR-IOV discovery-required; amd_pensando_allow_sriov default false. pf=%s" % pf,
            destructive=True,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id, sriov=None),
        )

    def configure_switchdev(self, pf: Optional[str] = None) -> OperationResult:
        return OperationResult(
            operation="configure_switchdev",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="switchdev discovery-required; no NVIDIA eswitch. pf=%s" % pf,
            destructive=True,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id, switchdev=None),
        )

    def discover_representors(self, pf: Optional[str] = None) -> OperationResult:
        inv = discovery.discover()
        inv.operation = "discover_representors"
        inv.representors = []
        inv.message = (
            "representors discovery-required; not guessed. OPI Network is AMD-7 "
            "contingency if none. pf=%s" % pf
        )
        return inv

    def configure_ovs_bridge(self, bridge: Optional[str] = None) -> OperationResult:
        return OperationResult(
            operation="configure_ovs_bridge",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="OVS hw-offload not assumed (try_ovs_offload false). bridge=%s" % bridge,
            destructive=True,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id, ovs_hw_offload=False),
        )

    def configure_vdpa(self, vf: Optional[str] = None) -> OperationResult:
        return OperationResult(
            operation="configure_vdpa",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="vDPA discovery-required. vf=%s" % vf,
            destructive=True,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id, vdpa=None),
        )

    def collect_telemetry(self, source_id: Optional[str] = None) -> OperationResult:
        if source_id:
            return ionic.get_ionic_stats(source_id, raw=None)
        return telemetry.collect(source_id)

    def validate_offload(self) -> OperationResult:
        return OperationResult(
            operation="validate_offload",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="ionic stats ≠ offload; FN-05/17 required before any claim",
            executed=False,
            flows=[],
            details={"offload_state": None},
            capabilities=VendorCapability(vendor=self.backend_id, ovs_hw_offload=False),
        )

    def configure_forwarding(self, intent: ForwardingIntent, dry_run: bool = True) -> ForwardingResult:
        """Blocked until discovery proves a control path. No assumed OVS."""
        from .capability_report import (
            AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH,
            VM_TO_VM_NOT_PROVEN,
            build_gap_report,
        )

        forwarding_entity = "AMD Pensando DSC datapath (only if supported by discovered capabilities)"
        control_entity = "labctl -> amd backend -> discovered Pensando control path"
        report = build_gap_report(self._lab)
        if report.get("final_status") != "AMD_READY_FOR_VM_TO_VM_DRY_RUN":
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message=VM_TO_VM_NOT_PROVEN,
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=[
                    FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED,
                    AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH,
                ],
                offload_requested=intent.offload.requested,
                details={
                    "gap_report": report,
                    "verdict": "UNSUPPORTED",
                    "ovs_attempted": False,
                },
            )
        missing = [e.name for e in intent.endpoints if not e.representor]
        if missing:
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message=VM_TO_VM_NOT_PROVEN,
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=[FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED],
                offload_requested=intent.offload.requested,
                details={"gap_report": report},
            )
        bridge = intent.bridge or "br-int"
        return ForwardingResult(
            success=False,
            vendor=self.backend_id,
            message="control path listed in gap report but OVS is still not assumed for AMD",
            forwarding_entity=forwarding_entity,
            control_entity=control_entity,
            configured_endpoints=[e.name for e in intent.endpoints],
            offload_requested=intent.offload.requested,
            details={"bridge": bridge, "gap_report": report, "ovs_attempted": False},
        )

    def cleanup(self) -> OperationResult:
        return OperationResult(
            operation="cleanup",
            vendor=self.backend_id,
            status=STATUS_NOT_IMPLEMENTED,
            message="no ionic/OVS teardown",
            destructive=True,
            executed=False,
        )
