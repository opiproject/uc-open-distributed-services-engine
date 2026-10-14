"""Vendor-neutral plugin base (models, VendorBackend, errors, commands)."""

from .backend import VendorBackend
from .commands import (
    command_exists,
    list_netdevs,
    list_pci_devices,
    planned_command,
    read_file,
    run,
    write_file,
)
from .errors import (
    DiscoveryError,
    PluginError,
    PrerequisiteError,
    UnknownBackend,
    UnsupportedOperation,
)
from .models import (
    CommandResult,
    DPUDevice,
    OffloadFlow,
    OperationResult,
    PhysicalFunction,
    RepresentorPort,
    TelemetrySample,
    VendorCapability,
    VirtualFunction,
)

__all__ = [
    "CommandResult",
    "DPUDevice",
    "DiscoveryError",
    "OffloadFlow",
    "OperationResult",
    "PhysicalFunction",
    "PluginError",
    "PrerequisiteError",
    "RepresentorPort",
    "TelemetrySample",
    "UnknownBackend",
    "UnsupportedOperation",
    "VendorBackend",
    "VendorCapability",
    "VirtualFunction",
    "command_exists",
    "list_netdevs",
    "list_pci_devices",
    "planned_command",
    "read_file",
    "run",
    "write_file",
]
