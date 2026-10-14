"""OPI lifecycle.v1 and inventory.v1 for the VM-to-VM lab.

The messages are the pinned protos in kubernetes/opi/pinned/. This
module does not add fields to those messages. JSON uses proto3 JSON
names (camelCase), the same names a gRPC-gateway would emit.

SetNumVfsRequest has no device id. Callers pass the PF via lab config
dpu.pf_name. That fact is returned next to the proto body, not inside
it. Representor names are not an OPI field and are never filled here.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from .models import STATUS_OK, STATUS_SKIPPED, OperationResult, PhysicalFunction
from .sriov import configure_sriov_numvfs

SETNUMVFS_LIMITATION = (
    "opi_api.lifecycle.v1.SetNumVfsRequest has no device id; "
    "the PF is lab config dpu.pf_name"
)
INIT_GAP = (
    "LifeCycleService.Init cannot provision an xPU. "
    "InitRequest has dpu_mode and dpu_identifier only: no image and no procedure. "
    "dpu_mode is not applied and is not switchdev."
)
PING_REACHED = "healthy means SSH to dpu.management_ip accepted a command"
PING_UNCONFIGURED = (
    "dpu.management_ip and dpu.ssh_user are required to check the xPU"
)
NOT_OPI = ("representor", "offload")

_VENDORS_WITHOUT_SRIOV = ("amd-pensando", "marvell-octeon")

_PCI_LINE = re.compile(
    r"^(?P<addr>(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])\s+"
    r"(?P<class>[^:]+):\s+(?P<rest>.*)$"
)
_VENDOR_DEVICE = re.compile(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]")
_REV = re.compile(r"\(rev\s+([0-9a-fA-F]+)\)")


@dataclass
class OpiCall:
    """One OPI operation. `body` is the proto JSON a gateway would return."""

    service: str
    http_status: int
    body: Dict[str, Any]
    limitation: Optional[str] = None
    dry_run: bool = True
    executed: bool = False
    not_opi: Sequence[str] = field(default_factory=lambda: list(NOT_OPI))


def _placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">")


def _configured(value: Any) -> bool:
    return bool(value) and not _placeholder(value)


def _vendor_type(config: Optional[Mapping[str, Any]]) -> str:
    vendor = (config or {}).get("vendor")
    if isinstance(vendor, dict):
        return str(vendor.get("type") or "")
    return ""


def _dpu(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    dpu = (config or {}).get("dpu")
    return dict(dpu) if isinstance(dpu, dict) else {}


def _host_name(config: Optional[Mapping[str, Any]]) -> Optional[str]:
    host = (config or {}).get("host")
    if not isinstance(host, dict):
        return None
    name = host.get("name")
    return str(name) if _configured(name) else None


def _normalize_pci(addr: str) -> str:
    text = addr.strip().lower()
    if ":" in text and text.count(":") == 1:
        return "0000:" + text
    return text


def parse_lspci_pci(raw: Optional[str]) -> list:
    """PCIeDeviceInfo-shaped dicts from `lspci -nn` text. Unparsed lines are skipped."""
    devices = []
    if not raw:
        return devices
    for line in raw.splitlines():
        match = _PCI_LINE.match(line.strip())
        if not match:
            continue
        entry: Dict[str, Any] = {"address": _normalize_pci(match.group("addr"))}
        class_name = match.group("class").strip()
        if class_name:
            entry["class"] = class_name
        rest = match.group("rest")
        ids = _VENDOR_DEVICE.search(rest)
        if ids:
            entry["vendor"] = ids.group(1).lower()
            entry["product"] = ids.group(2).lower()
        rev = _REV.search(rest)
        if rev:
            entry["revision"] = rev.group(1).lower()
        devices.append(entry)
    return devices


def _device_entry(device_id: str, node: Optional[str]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"id": device_id}
    if node:
        entry["topology"] = {"node": node}
    return entry


def get_devices(
    config: Optional[Mapping[str, Any]] = None,
    *,
    physical_functions: Optional[Sequence[Any]] = None,
    include_configured: bool = True,
) -> OpiCall:
    """DeviceService.GetDevices. Health is omitted when it is not known."""
    node = _host_name(config)
    devices: Dict[str, Any] = {}
    for pf in physical_functions or []:
        device_id = None
        if isinstance(pf, PhysicalFunction):
            device_id = pf.netdev or pf.pci_address or pf.id
        elif isinstance(pf, Mapping):
            device_id = pf.get("netdev") or pf.get("pci_address") or pf.get("id")
        if _configured(device_id):
            devices[str(device_id)] = _device_entry(str(device_id), node)
    if include_configured:
        dpu = _dpu(config)
        for key in ("pf_name", "pf_pci"):
            device_id = dpu.get(key)
            if _configured(device_id) and str(device_id) not in devices:
                devices[str(device_id)] = _device_entry(str(device_id), node)
    return OpiCall(
        service="opi_api.lifecycle.v1.DeviceService.GetDevices",
        http_status=200,
        body={"devices": devices},
        executed=True,
        dry_run=True,
    )


def get_inventory(
    *,
    lspci_text: Optional[str] = None,
    name: str = "inventory/local/inventory/pci",
) -> OpiCall:
    """InventoryService.GetInventory. Only the PCI slice is filled."""
    return OpiCall(
        service="opi_api.inventory.v1.InventoryService.GetInventory",
        http_status=200,
        body={"pci": parse_lspci_pci(lspci_text)},
        executed=lspci_text is not None,
        dry_run=True,
        limitation="BIOS, chassis, CPU, and memory are not filled; name=%s" % name,
    )


def probe_management_ssh(config: Optional[Mapping[str, Any]]) -> tuple:
    """True when SSH to the configured xPU management address accepts a command."""
    from .remote import RemoteCommandRunner, ssh_params

    params = ssh_params(config if isinstance(config, dict) else None)
    host = params.get("host")
    user = params.get("user")
    if not _configured(host) or not _configured(user):
        return False, PING_UNCONFIGURED
    result = RemoteCommandRunner().run_ssh(
        str(host),
        str(user),
        ["true"],
        key_path=params.get("key_path"),
        port=int(params.get("port") or 22),
        dry_run=False,
        timeout=params.get("command_timeout_seconds") or 15,
        reason="opi Ping",
        sudo=False,
    )
    if result.executed and result.returncode == 0:
        return True, PING_REACHED
    detail = (result.stderr or result.reason or "ssh failed").strip()
    return False, "SSH to %s failed: %s" % (host, detail)


def ping(
    payload: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
    *,
    probe: Optional[Callable[..., Any]] = None,
) -> OpiCall:
    """HeartbeatService.Ping. healthy means the xPU management SSH answered."""
    checker = probe or probe_management_ssh
    healthy, limitation = checker(config)
    return OpiCall(
        service="opi_api.lifecycle.v1.HeartbeatService.Ping",
        http_status=200,
        body={
            "timestamp": time.time_ns(),
            "responderId": "odse-opi",
            "healthy": bool(healthy),
        },
        executed=True,
        dry_run=False,
        limitation=str(limitation) if limitation else None,
    )


def init_xpu(
    config: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> OpiCall:
    """LifeCycleService.Init. The published request cannot provision a card."""
    return OpiCall(
        service="opi_api.lifecycle.v1.LifeCycleService.Init",
        http_status=501,
        body={"code": "UNIMPLEMENTED", "message": INIT_GAP},
        limitation=INIT_GAP,
        executed=False,
        dry_run=True,
    )


def set_num_vfs(
    config: Optional[Mapping[str, Any]],
    vf_cnt: Any,
    dry_run: bool = True,
    *,
    read_file_fn: Optional[Callable[[str], Optional[str]]] = None,
    write_file_fn: Optional[Callable[..., Any]] = None,
) -> OpiCall:
    """DeviceService.SetNumVfs. AMD and Marvell stay unimplemented."""
    service = "opi_api.lifecycle.v1.DeviceService.SetNumVfs"
    vendor = _vendor_type(config)
    if vendor in _VENDORS_WITHOUT_SRIOV:
        return OpiCall(
            service=service,
            http_status=501,
            body={
                "code": "UNIMPLEMENTED",
                "message": "SR-IOV is not implemented for vendor %s" % vendor,
            },
            limitation=SETNUMVFS_LIMITATION,
            dry_run=dry_run,
            executed=False,
        )
    if isinstance(vf_cnt, bool) or not isinstance(vf_cnt, int) or vf_cnt < 0:
        return OpiCall(
            service=service,
            http_status=400,
            body={
                "code": "INVALID_ARGUMENT",
                "message": "vfCnt must be a non-negative integer",
            },
            limitation=SETNUMVFS_LIMITATION,
            dry_run=dry_run,
            executed=False,
        )
    pf = _dpu(config).get("pf_name")
    if not _configured(pf):
        return OpiCall(
            service=service,
            http_status=400,
            body={
                "code": "INVALID_ARGUMENT",
                "message": "dpu.pf_name is required because SetNumVfsRequest has no device id",
            },
            limitation=SETNUMVFS_LIMITATION,
            dry_run=dry_run,
            executed=False,
        )
    result = configure_sriov_numvfs(
        str(pf),
        vf_cnt,
        dry_run=dry_run,
        vendor="opi",
        read_file_fn=read_file_fn,
        write_file_fn=write_file_fn,
    )
    return set_num_vfs_from_result(result, vf_cnt=vf_cnt, dry_run=dry_run)


def set_num_vfs_from_result(
    result: OperationResult,
    *,
    vf_cnt: Any,
    dry_run: bool,
) -> OpiCall:
    """Shape a configure_sriov OperationResult as SetNumVfs. Does not write again."""
    service = "opi_api.lifecycle.v1.DeviceService.SetNumVfs"
    if result.status == STATUS_SKIPPED:
        return OpiCall(
            service=service,
            http_status=501,
            body={"code": "UNIMPLEMENTED", "message": result.message},
            limitation=SETNUMVFS_LIMITATION,
            dry_run=dry_run,
            executed=False,
        )
    if result.status != STATUS_OK:
        return OpiCall(
            service=service,
            http_status=400,
            body={"code": "FAILED_PRECONDITION", "message": result.message},
            limitation=SETNUMVFS_LIMITATION,
            dry_run=dry_run,
            executed=False,
        )
    count = vf_cnt if isinstance(vf_cnt, int) and not isinstance(vf_cnt, bool) else None
    body: Dict[str, Any] = {}
    if count is not None:
        body["vfCnt"] = count
    return OpiCall(
        service=service,
        http_status=200,
        body=body,
        limitation=SETNUMVFS_LIMITATION,
        dry_run=dry_run,
        executed=bool(result.executed),
    )
