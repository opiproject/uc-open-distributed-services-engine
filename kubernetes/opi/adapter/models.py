"""ODSE OPI-aligned object models (v1alpha1).

Skeletons for serialization and VendorBackend signatures. Not a
protobuf/CRD generator. Unknown/optional fields use None.
See kubernetes/opi/README.md and docs/architecture/api-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

API_VERSION = "odse.opi/v1alpha1"


@dataclass
class ObjectMeta:
    id: str
    name: Optional[str] = None
    vendor: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class DPUCapabilities:
    sriov_max_vfs: int = 0
    switchdev_supported: bool = False
    vdpa_supported: bool = False
    opi_native: Optional[bool] = None


@dataclass
class DPUDevice:
    metadata: ObjectMeta
    model: Optional[str] = None
    serial_number: Optional[str] = None
    firmware_version: Optional[str] = None
    pci_address: Optional[str] = None
    management_address: Optional[str] = None
    numa_node: Optional[int] = None
    driver_version: Optional[str] = None
    capabilities: DPUCapabilities = field(default_factory=DPUCapabilities)
    status: Dict[str, Any] = field(default_factory=dict)
    vendor_extensions: Dict[str, Any] = field(default_factory=dict)
    kind: str = "DPUDevice"
    api_version: str = API_VERSION


@dataclass
class NetworkFunction:
    metadata: ObjectMeta
    dpu_device_id: str = ""
    function_type: str = ""
    image: str = ""
    attach_point: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, Any]] = None
    status: Dict[str, Any] = field(default_factory=dict)
    vendor_extensions: Dict[str, Any] = field(default_factory=dict)
    kind: str = "NetworkFunction"
    api_version: str = API_VERSION


@dataclass
class NetworkAttachment:
    metadata: ObjectMeta
    dpu_device_id: str = ""
    virtual_function_id: Optional[str] = None
    representor_port_id: Optional[str] = None
    bridge_name: str = ""
    admin_state: str = "up"
    mtu_bytes: Optional[int] = None
    vlan_id: Optional[int] = None
    ovn_logical_port: Optional[str] = None
    status: Dict[str, Any] = field(default_factory=dict)
    vendor_extensions: Dict[str, Any] = field(default_factory=dict)
    kind: str = "NetworkAttachment"
    api_version: str = API_VERSION


@dataclass
class OffloadPolicy:
    metadata: ObjectMeta
    target_id: str = ""
    hw_offload: bool = False
    datapath_type: Optional[str] = None
    require_in_hw_markers: bool = False
    status: Dict[str, Any] = field(default_factory=dict)
    vendor_extensions: Dict[str, Any] = field(default_factory=dict)
    kind: str = "OffloadPolicy"
    api_version: str = API_VERSION


@dataclass
class TelemetrySample:
    metadata: ObjectMeta
    source_object_id: str = ""
    source_kind: str = ""
    metric_name: str = ""
    value: Optional[float] = None
    unit: str = ""
    timestamp: Optional[str] = None
    labels: Dict[str, str] = field(default_factory=dict)
    kind: str = "TelemetrySample"
    api_version: str = API_VERSION


@dataclass
class LifecycleOperation:
    metadata: ObjectMeta
    target_device_id: str = ""
    operation_type: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: Dict[str, Any] = field(default_factory=dict)
    vendor_extensions: Dict[str, Any] = field(default_factory=dict)
    kind: str = "LifecycleOperation"
    api_version: str = API_VERSION
