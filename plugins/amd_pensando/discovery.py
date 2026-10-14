"""AMD Pensando / ionic discovery (read-only).

Collects lspci, ip -d link, ethtool -i/-S on ionic netdevs, optional
rdma link show, optional devlink, and lsmod for ionic / ionic_rdma.

Hints: Pensando, ionic, DSC. Generic AMD CPU PCI is not a DSC.
switchdev, representors, OVS offload, and vDPA stay discovery-required
unless a later probe proves them (this module does not).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..base.commands import command_exists, list_netdevs, read_file, run
from ..base.models import (
    STATUS_OK,
    STATUS_SKIPPED,
    CommandResult,
    DPUDevice,
    OperationResult,
    PhysicalFunction,
    VendorCapability,
)

LOG = logging.getLogger(__name__)
VENDOR = "amd"

_PCI = re.compile(
    r"(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_IP_HEADER = re.compile(r"^\d+:\s+([^:@]+)(?:@\S+)?:")
_DSC_WORD = re.compile(r"\bdsc\b", re.IGNORECASE)
PROBE_TIMEOUT = 15.0


def looks_like_pensando(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    if "pensando" in lower or "ionic" in lower:
        return True
    if "distributed services" in lower:
        return True
    return bool(_DSC_WORD.search(text))


def parse_lspci_nn(raw: Optional[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        line = line.strip()
        if not line or not looks_like_pensando(line):
            continue
        pci_match = _PCI.search(line)
        rows.append(
            {
                "pci_address": pci_match.group("pci").lower() if pci_match else None,
                "description": line,
                "model": _model_from_lspci(line),
            }
        )
    return rows


def parse_lsmod(raw: Optional[str]) -> Dict[str, bool]:
    """True if ionic / ionic_rdma appear as loaded modules."""
    found = {"ionic": False, "ionic_rdma": False}
    if not raw:
        return found
    for line in raw.splitlines():
        name = line.split()[0] if line.strip() else ""
        if name == "ionic":
            found["ionic"] = True
        elif name == "ionic_rdma":
            found["ionic_rdma"] = True
    return found


def parse_ip_names(raw: Optional[str]) -> List[str]:
    names: List[str] = []
    if not raw:
        return names
    for line in raw.splitlines():
        header = _IP_HEADER.match(line)
        if header and header.group(1) != "lo":
            names.append(header.group(1))
    return names


def build_inventory(
    *,
    lspci: Optional[str] = None,
    ip_link: Optional[str] = None,
    ethtool_i: Optional[Mapping[str, str]] = None,
    ethtool_s: Optional[Mapping[str, str]] = None,
    rdma_link: Optional[str] = None,
    devlink_dev: Optional[str] = None,
    lsmod: Optional[str] = None,
) -> OperationResult:
    from . import ionic as ionic_mod
    from . import rdma as rdma_mod

    rows = parse_lspci_nn(lspci)
    modules = parse_lsmod(lsmod)
    ionic_ifaces = ionic_mod.list_ionic_interfaces(ethtool_i or {}, ip_names=parse_ip_names(ip_link))
    rdma_info = rdma_mod.detect_ionic_rdma(rdma_link=rdma_link, lsmod=lsmod)

    devices: List[DPUDevice] = []
    pfs: List[PhysicalFunction] = []
    used_pci = set()
    for row in rows:
        pci = row["pci_address"]
        if pci:
            used_pci.add(pci)
        cap = _caps(notes="PCI Pensando/DSC hint; datapath unproven")
        devices.append(
            DPUDevice(
                id=("amd-%s" % pci) if pci else None,
                vendor=VENDOR,
                model=row["model"],
                serial_number=None,
                firmware_version=None,
                pci_address=pci,
                capabilities=cap,
                vendor_extensions={"lspci": row["description"]},
            )
        )

    for name in ionic_ifaces:
        info = ionic_mod.parse_ethtool_i((ethtool_i or {}).get(name))
        pci = (info.get("bus-info") or "").lower() or None
        if pci and pci in used_pci:
            for p in pfs:
                if p.pci_address == pci and not p.netdev:
                    p.netdev = name
                    break
            else:
                pfs.append(
                    PhysicalFunction(
                        id="pf-%s" % pci,
                        dpu_device_id="amd-%s" % pci,
                        pci_address=pci,
                        netdev=name,
                        eswitch_mode=None,
                    )
                )
            continue
        if pci and pci not in used_pci:
            used_pci.add(pci)
            cap = _caps(notes="ionic driver; SKU not assumed from driver alone")
            devices.append(
                DPUDevice(
                    id="amd-%s" % pci,
                    vendor=VENDOR,
                    model="ionic",
                    firmware_version=info.get("firmware-version"),
                    pci_address=pci,
                    capabilities=cap,
                    vendor_extensions={"ethtool_driver": info.get("driver")},
                )
            )
        pfs.append(
            PhysicalFunction(
                id="pf-%s" % (pci or name),
                dpu_device_id=("amd-%s" % pci) if pci else None,
                pci_address=pci,
                netdev=name,
                eswitch_mode=None,
            )
        )

    stats_ifaces = list((ethtool_s or {}).keys())
    if not devices and not ionic_ifaces and not modules.get("ionic"):
        status = STATUS_SKIPPED
        message = "no Pensando/ionic/DSC hints; not a DSC"
    else:
        status = STATUS_OK
        message = (
            "AMD Pensando discovery-first; switchdev/representors/OVS/vDPA "
            "discovery-required (not assumed)"
        )

    LOG.info("amd discovery devices=%s ionic=%s", len(devices), ionic_ifaces)
    return OperationResult(
        operation="discover",
        vendor=VENDOR,
        status=status,
        message=message,
        executed=True,
        destructive=False,
        devices=devices,
        physical_functions=pfs,
        representors=[],
        capabilities=_caps(notes="ionic Ethernet evidenced only if driver/PCI matched"),
        details={
            "ionic_interfaces": ionic_ifaces,
            "modules": modules,
            "rdma": rdma_info.details if rdma_info else {},
            "devlink_raw_bytes": len(devlink_dev or ""),
            "stats_interfaces": stats_ifaces,
            "switchdev": None,
            "representors": None,
            "ovs_hw_offload": False,
            "vdpa": None,
        },
    )


def collect_probes(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    runner=None,
    command_exists_fn=None,
) -> Dict[str, Any]:
    from . import ionic as ionic_mod

    exists = command_exists_fn or command_exists
    run_fn = runner or run
    if samples is not None:
        return {
            "lspci": samples.get("lspci"),
            "ip_link": samples.get("ip_link"),
            "ethtool_i": dict(samples.get("ethtool_i") or {}),
            "ethtool_s": dict(samples.get("ethtool_s") or {}),
            "rdma_link": samples.get("rdma_link"),
            "devlink_dev": samples.get("devlink_dev"),
            "lsmod": samples.get("lsmod"),
            "commands": [],
        }

    commands: List[CommandResult] = []

    def probe(argv: Sequence[str], need: str) -> CommandResult:
        if not exists(need):
            result = CommandResult(argv=list(argv), executed=False, reason="command not found")
            commands.append(result)
            return result
        result = run_fn(list(argv), dry_run=dry_run, timeout=PROBE_TIMEOUT)
        commands.append(result)
        return result

    lspci = probe(["lspci", "-nn"], "lspci")
    ip_link = probe(["ip", "-d", "link", "show"], "ip")
    devlink = probe(["devlink", "dev", "show"], "devlink")
    rdma = probe(["rdma", "link", "show"], "rdma")
    lsmod_cmd = probe(["lsmod"], "lsmod")
    lsmod_text = lsmod_cmd.stdout
    if not lsmod_text:
        lsmod_text = read_file("/proc/modules")

    names = parse_ip_names(ip_link.stdout) or [n for n in list_netdevs() if n != "lo"]
    ethtool_i: Dict[str, str] = {}
    ethtool_s: Dict[str, str] = {}
    if exists("ethtool") and not dry_run:
        for name in names:
            ei = run_fn(["ethtool", "-i", name], dry_run=False, timeout=PROBE_TIMEOUT)
            commands.append(ei)
            if ei.stdout and ionic_mod.is_ionic_ethtool(ei.stdout):
                ethtool_i[name] = ei.stdout
                es = run_fn(["ethtool", "-S", name], dry_run=False, timeout=PROBE_TIMEOUT)
                commands.append(es)
                if es.stdout:
                    ethtool_s[name] = es.stdout

    return {
        "lspci": lspci.stdout,
        "ip_link": ip_link.stdout,
        "ethtool_i": ethtool_i,
        "ethtool_s": ethtool_s,
        "rdma_link": rdma.stdout,
        "devlink_dev": devlink.stdout,
        "lsmod": lsmod_text,
        "commands": commands,
    }


def discover(*, samples: Optional[Mapping[str, Any]] = None, dry_run: bool = False, **kwargs: Any) -> OperationResult:
    packed = collect_probes(samples=samples, dry_run=dry_run, **kwargs)
    result = build_inventory(
        lspci=packed.get("lspci"),
        ip_link=packed.get("ip_link"),
        ethtool_i=packed.get("ethtool_i"),
        ethtool_s=packed.get("ethtool_s"),
        rdma_link=packed.get("rdma_link"),
        devlink_dev=packed.get("devlink_dev"),
        lsmod=packed.get("lsmod"),
    )
    result.commands = list(packed.get("commands") or [])
    return result


def _caps(*, notes: str) -> VendorCapability:
    return VendorCapability(
        vendor=VENDOR,
        sriov=None,
        switchdev=None,
        vdpa=None,
        ovs_hw_offload=False,
        notes=notes,
    )


def _model_from_lspci(line: str) -> Optional[str]:
    if ":" not in line:
        return None
    desc = line.split(":", 1)[1].strip()
    desc = re.sub(r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]\s*$", "", desc)
    return desc or None
