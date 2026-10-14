"""Shared dataclasses for vendor plugins.

Unknown facts are None (or omitted lists), never invented devices or
fake offload/telemetry zeros. Aligns with docs/architecture/api-model.md
but is the *plugin* envelope (OperationResult), not the OPI CRD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# OperationResult.status
STATUS_NOT_IMPLEMENTED = "not_implemented"
STATUS_SKIPPED = "skipped"
STATUS_OK = "ok"
STATUS_ERROR = "error"

# ForwardingResult.errors entries. Backends append one of these codes
# (optionally with more detail) rather than an ad-hoc string, so
# tools/labctl.py and tools/report_generator.py can branch on them.
FORWARDING_ERROR_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
FORWARDING_ERROR_REPRESENTOR_REQUIRED = "REPRESENTOR_REQUIRED"
FORWARDING_ERROR_DAO_NOT_AVAILABLE = "DAO_NOT_AVAILABLE"
FORWARDING_ERROR_REPRESENTOR_MAPPING_REQUIRED = "REPRESENTOR_MAPPING_REQUIRED"
FORWARDING_ERROR_OPI_ENDPOINT_REQUIRED = "OPI_ENDPOINT_REQUIRED"
FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED = "REPRESENTOR_OR_CONTROL_PATH_REQUIRED"
FORWARDING_ERROR_P4_ENDPOINT_REQUIRED = "P4_ENDPOINT_REQUIRED"


@dataclass
class CommandResult:
    """A command that was planned or executed via plugins.base.commands.run."""

    argv: List[str]
    executed: bool = False
    dry_run: bool = True
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    reason: Optional[str] = None
    timed_out: bool = False
    shell: bool = False


@dataclass
class VendorCapability:
    """Honest capability flags. True only when proven; stubs use None."""

    vendor: str
    sriov: Optional[bool] = None
    switchdev: Optional[bool] = None
    vdpa: Optional[bool] = None
    ovs_hw_offload: Optional[bool] = None
    opi_native: Optional[bool] = None
    # P4Runtime/IPDK-style pipeline control (e.g. infrap4d on Intel IPU).
    # Independent of opi_native/switchdev -- some hardware exposes P4Runtime
    # without an OPI daemon or kernel switchdev, and predates either.
    p4_native: Optional[bool] = None
    notes: str = ""


@dataclass
class DPUDevice:
    id: Optional[str] = None
    vendor: str = ""
    model: Optional[str] = None
    serial_number: Optional[str] = None
    firmware_version: Optional[str] = None
    pci_address: Optional[str] = None
    management_address: Optional[str] = None
    capabilities: Optional[VendorCapability] = None
    vendor_extensions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PhysicalFunction:
    id: Optional[str] = None
    dpu_device_id: Optional[str] = None
    pci_address: Optional[str] = None
    netdev: Optional[str] = None
    eswitch_mode: Optional[str] = None
    max_vfs: Optional[int] = None
    current_vfs: Optional[int] = None


@dataclass
class VirtualFunction:
    id: Optional[str] = None
    pf_id: Optional[str] = None
    vf_index: Optional[int] = None
    pci_address: Optional[str] = None
    netdev: Optional[str] = None
    bound_driver: Optional[str] = None


@dataclass
class RepresentorPort:
    id: Optional[str] = None
    dpu_device_id: Optional[str] = None
    virtual_function_id: Optional[str] = None
    netdev: Optional[str] = None
    vf_index: Optional[int] = None
    mapping_method: Optional[str] = None
    confidence: Optional[str] = None


@dataclass
class OffloadFlow:
    id: Optional[str] = None
    representor_port_id: Optional[str] = None
    match_fields: Dict[str, Any] = field(default_factory=dict)
    actions: List[str] = field(default_factory=list)
    offload_state: Optional[str] = None  # hardware | software | unknown | None
    packet_count: Optional[int] = None
    byte_count: Optional[int] = None


@dataclass
class TelemetrySample:
    source_object_id: Optional[str] = None
    source_kind: Optional[str] = None
    metric_name: str = ""
    value: Optional[float] = None
    unit: str = ""
    timestamp: Optional[str] = None
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class ForwardingEndpoint:
    """One VM/VF side of a forwarding intent (see ForwardingIntent)."""

    name: str = ""
    vm: Optional[str] = None
    vf_index: Optional[int] = None
    vf_pci: Optional[str] = None
    representor: Optional[str] = None
    mac: Optional[str] = None
    ip_cidr: Optional[str] = None
    role: Optional[str] = None


@dataclass
class ForwardingPolicy:
    action: str = "allow"
    bidirectional: bool = True
    isolation_group: Optional[str] = None
    allowed_ethertypes: List[str] = field(default_factory=list)
    allowed_protocols: List[str] = field(default_factory=list)


@dataclass
class ForwardingOffloadRequest:
    requested: bool = False
    fallback_to_software: bool = True
    require_confirmed_offload: bool = False


@dataclass
class ForwardingIntent:
    """The one user-facing forwarding abstraction (see docs/architecture/
    forwarding-intent-model.md). Users express *what* should be connected;
    VendorBackend.configure_forwarding() decides *how*.
    """

    name: str = ""
    type: str = "l2-vm-to-vm"  # l2-vm-to-vm | l2-segment
    mode: str = "bridge"
    endpoints: List[ForwardingEndpoint] = field(default_factory=list)
    policy: ForwardingPolicy = field(default_factory=ForwardingPolicy)
    offload: ForwardingOffloadRequest = field(default_factory=ForwardingOffloadRequest)
    explicit_flows: Dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    # Not part of the YAML forwarding: block a user writes; populated by
    # tools/labctl.py from ovs.bridge / dpu.preferred_datapath so backends
    # do not have to reach back into the whole lab config.
    bridge: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ForwardingOperation:
    """One concrete step taken (or planned) while satisfying a ForwardingIntent.

    A richer, evidence-oriented sibling of CommandResult; a backend may
    record a list of these under ForwardingResult.details['operations'].
    """

    kind: str = ""  # e.g. add_port, add_flow, enable_hw_offload, dao_placeholder
    target: Optional[str] = None
    description: str = ""
    destructive: bool = True
    executed: bool = False
    command: Optional[CommandResult] = None


@dataclass
class ForwardingResult:
    """Structured return of VendorBackend.configure_forwarding()."""

    success: bool = False
    vendor: str = ""
    operation: str = "configure_forwarding"
    message: str = ""
    forwarding_entity: Optional[str] = None
    control_entity: Optional[str] = None
    configured_endpoints: List[str] = field(default_factory=list)
    configured_ports: List[str] = field(default_factory=list)
    configured_flows: List[str] = field(default_factory=list)
    offload_requested: Optional[bool] = None
    offload_configured: Optional[bool] = None
    offload_confirmed: Optional[bool] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    evidence_files: List[str] = field(default_factory=list)
    commands: List[CommandResult] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationResult:
    """Structured return for every VendorBackend method."""

    operation: str
    vendor: str
    status: str = STATUS_NOT_IMPLEMENTED
    message: str = ""
    destructive: bool = False
    executed: bool = False
    devices: List[DPUDevice] = field(default_factory=list)
    physical_functions: List[PhysicalFunction] = field(default_factory=list)
    virtual_functions: List[VirtualFunction] = field(default_factory=list)
    representors: List[RepresentorPort] = field(default_factory=list)
    flows: List[OffloadFlow] = field(default_factory=list)
    telemetry: List[TelemetrySample] = field(default_factory=list)
    capabilities: Optional[VendorCapability] = None
    commands: List[CommandResult] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
