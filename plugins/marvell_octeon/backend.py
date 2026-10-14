"""Marvell OCTEON VendorBackend — DAO/DPDK first; kernel switchdev not assumed."""

from __future__ import annotations

import logging
from typing import Optional

from ..base.backend import VendorBackend
from ..base.commands import command_exists
from ..base.models import (
    FORWARDING_ERROR_DAO_NOT_AVAILABLE,
    FORWARDING_ERROR_REPRESENTOR_MAPPING_REQUIRED,
    STATUS_OK,
    STATUS_SKIPPED,
    ForwardingIntent,
    ForwardingResult,
    OperationResult,
    VendorCapability,
)
from . import dao_ovs, discovery, dpdk, telemetry

LOG = logging.getLogger(__name__)


class MarvellOcteonBackend(VendorBackend):
    def __init__(self, configured_dao_app: Optional[str] = None, lab_config: Optional[dict] = None) -> None:
        self.configured_dao_app = configured_dao_app
        self._lab = dict(lab_config) if lab_config else {}

    @property
    def backend_id(self) -> str:
        return "marvell"

    def discover(self) -> OperationResult:
        return discovery.discover()

    def validate_prerequisites(self) -> OperationResult:
        dao = dao_ovs.check_dao_available(configured_app=self.configured_dao_app)
        tools = {
            "lspci": command_exists("lspci"),
            "ip": command_exists("ip"),
            "ovs-vsctl": command_exists("ovs-vsctl"),
            "dpdk-devbind.py": command_exists("dpdk-devbind.py"),
        }
        return OperationResult(
            operation="validate_prerequisites",
            vendor=self.backend_id,
            status=STATUS_OK if (tools["lspci"] or dao.details.get("available")) else STATUS_SKIPPED,
            message="OCTEON tools optional; DAO not required for PCI discovery",
            executed=True,
            capabilities=VendorCapability(vendor=self.backend_id, switchdev=None),
            details={"tools": tools, "dao": dao.details},
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
            message="SR-IOV after PF identity; apply not implied. pf=%s" % pf,
            destructive=True,
            executed=False,
        )

    def configure_switchdev(self, pf: Optional[str] = None) -> OperationResult:
        return OperationResult(
            operation="configure_switchdev",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="Kernel eswitch not assumed; use DPDK representors/DAO. pf=%s" % pf,
            destructive=True,
            executed=False,
            capabilities=VendorCapability(vendor=self.backend_id, switchdev=None),
        )

    def discover_representors(self, pf: Optional[str] = None) -> OperationResult:
        from . import dpdk_representor_map

        inv = discovery.discover()
        mapped = dpdk_representor_map.map_dpdk_representors(config=self._lab)
        inv.operation = "discover_representors"
        inv.representors = []
        inv.message = (
            "kernel representors not guessed; DPDK map in details. pf=%s" % pf
        )
        inv.details = dict(inv.details)
        inv.details["dpdk_list"] = dao_ovs.list_dpdk_ports().details
        inv.details["dpdk_note"] = dpdk.plan_datapath().message
        inv.details["dpdk_representor_map"] = mapped.details
        return inv

    def configure_ovs_bridge(self, bridge: Optional[str] = None) -> OperationResult:
        return dao_ovs.plan_configure(bridge)

    def configure_vdpa(self, vf: Optional[str] = None) -> OperationResult:
        return OperationResult(
            operation="configure_vdpa",
            vendor=self.backend_id,
            status=STATUS_SKIPPED,
            message="vDPA discovery-required; may be vhost-user. vf=%s" % vf,
            destructive=True,
            executed=False,
        )

    def collect_telemetry(self, source_id: Optional[str] = None) -> OperationResult:
        dao = dao_ovs.check_dao_available(configured_app=self.configured_dao_app)
        placeholder = dao_ovs.collect_dao_telemetry_placeholder(
            dao_available=bool(dao.details.get("available"))
        )
        if placeholder.telemetry:
            return placeholder
        return telemetry.collect(source_id)

    def validate_offload(self) -> OperationResult:
        from . import dao_telemetry

        return dao_telemetry.collect_dao_evidence(self._lab)

    def cleanup(self) -> OperationResult:
        from . import cleanup as dao_cleanup

        return dao_cleanup.plan_dao_cleanup(self._lab, dry_run=True, apply=False)

    def configure_forwarding(self, intent: ForwardingIntent, dry_run: bool = True) -> ForwardingResult:
        """Staged skeleton: DAO OVS-offload / DPDK representor path.

        Returns success=False with a specific error code (rather than a
        fake success) whenever DAO or the representor mapping has not
        actually been discovered/configured.
        """
        forwarding_entity = "Marvell OCTEON hardware path"
        control_entity = (
            "labctl -> marvell backend -> DAO OVS-offload / DPDK representors -> OCTEON hardware"
        )
        dao = dao_ovs.check_dao_available(configured_app=self.configured_dao_app)
        dao_available = bool(dao.details.get("available"))
        if not dao_available:
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message=dao.message,
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=[FORWARDING_ERROR_DAO_NOT_AVAILABLE],
                offload_requested=intent.offload.requested,
                details={"dao": dao.details},
            )
        missing = [e.name for e in intent.endpoints if not e.representor]
        if missing:
            return ForwardingResult(
                success=False,
                vendor=self.backend_id,
                message="DPDK representor mapping missing for %s" % ", ".join(missing),
                forwarding_entity=forwarding_entity,
                control_entity=control_entity,
                errors=[FORWARDING_ERROR_REPRESENTOR_MAPPING_REQUIRED],
                offload_requested=intent.offload.requested,
                details={"dao": dao.details},
            )
        placeholders = [
            dao_ovs.configure_representor_placeholder(
                endpoint.representor, apply=False, dry_run=True, dao_available=True
            ).message
            for endpoint in intent.endpoints
        ]
        start = dao_ovs.start_dao_ovs_offload_placeholder(
            apply=False, dry_run=True, dao_available=True, configured_app=self.configured_dao_app
        )
        return ForwardingResult(
            success=True,
            vendor=self.backend_id,
            message="DAO OVS-offload forwarding staged as placeholders (no CLI executed)",
            forwarding_entity=forwarding_entity,
            control_entity=control_entity,
            configured_endpoints=[e.name for e in intent.endpoints],
            configured_ports=[e.representor for e in intent.endpoints],
            offload_requested=intent.offload.requested,
            offload_configured=False,
            offload_confirmed=False,
            warnings=[
                "DAO placeholders only; real DAO OVS-offload CLI not confirmed/executed on hardware"
            ],
            details={"placeholders": placeholders, "dao_start": start.message},
        )
