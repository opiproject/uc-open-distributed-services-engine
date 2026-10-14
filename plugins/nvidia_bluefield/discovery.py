"""NVIDIA / Mellanox / mlx5 discovery (read-only).

Collects lspci, devlink, ip -d link, ethtool -i (mlx5 only), and optional
ovs-vsctl show. Detects likely NVIDIA devices only when output contains
Mellanox, NVIDIA, mlx5, BlueField, or ConnectX.

Unknown fields stay None. Netdev names like eth0v1 are not representors
unless portname/phys_port_name matches pf<N>vf<M> / pf<N>sf<M>.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..base.commands import command_exists, list_netdevs, run
from ..base.models import (
    STATUS_OK,
    STATUS_SKIPPED,
    CommandResult,
    DPUDevice,
    OperationResult,
    PhysicalFunction,
    RepresentorPort,
    VendorCapability,
    VirtualFunction,
)

LOG = logging.getLogger(__name__)
VENDOR = "nvidia"

NVIDIA_HINTS = ("mellanox", "nvidia", "mlx5", "bluefield", "connectx")

# Domain-prefixed (0000:0d:00.0) or host-short lspci form (0d:00.0).
_PCI = re.compile(
    r"(?P<pci>(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_IP_HEADER = re.compile(r"^\d+:\s+([^:@]+)(?:@\S+)?:")
# Real `ip -d link show` packs attribute/value pairs onto the same long
# line as `link/ether ...` (confirmed on real E2100 host-4 hardware --
# see example-output/host-4/Step1.txt and
# plugins/intel_ipu/discovery.py), not one attribute per line. Search
# each interface's whole block for these tokens instead of anchoring on
# line-start; this still matches captures that do put one attribute per
# line. Some iproute2 versions print `switchid`, others
# `phys_switch_id`; both are accepted as the same attribute.
_IP_ATTR = re.compile(
    r"\b(?P<key>parentdev|portname|phys_port_name|switchid|phys_switch_id)"
    r"\s+(?P<value>\S+)"
)
_PORTNAME_VF = re.compile(
    r"^(?:c(?P<controller>\d+))?(?:pf|p)(?P<pf>\d+)vf(?P<vf>\d+)$",
    re.IGNORECASE,
)
_PORTNAME_SF = re.compile(
    r"^(?:c(?P<controller>\d+))?pf(?P<pf>\d+)sf(?P<sf>\d+)$",
    re.IGNORECASE,
)
_DEVLINK_PCI = re.compile(
    r"pci/(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_DEVLINK_KV = re.compile(r"(\w+)\s+(\S+)")

PROBE_TIMEOUT = 15.0


def looks_like_nvidia(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(hint in lower for hint in NVIDIA_HINTS)


def normalize_pci_address(addr: Optional[str]) -> Optional[str]:
    """Normalize `0d:00.0` from `lspci -nn` to `0000:0d:00.0`."""
    if not addr:
        return None
    pci = addr.strip().lower()
    if pci.count(":") == 1:
        return "0000:%s" % pci
    return pci


def parse_portname(name: Optional[str]) -> Dict[str, Any]:
    """Parse pf0vf0 / p0vf1 / pf0sf0. Other strings yield kind=None."""
    out: Dict[str, Any] = {
        "raw": name,
        "kind": None,
        "pf_index": None,
        "vf_index": None,
        "sf_index": None,
        "controller": None,
    }
    if not name:
        return out
    token = name.strip()
    vf_match = _PORTNAME_VF.match(token)
    if vf_match:
        out["kind"] = "vf"
        out["pf_index"] = int(vf_match.group("pf"))
        out["vf_index"] = int(vf_match.group("vf"))
        if vf_match.group("controller") is not None:
            out["controller"] = int(vf_match.group("controller"))
        return out
    sf_match = _PORTNAME_SF.match(token)
    if sf_match:
        out["kind"] = "sf"
        out["pf_index"] = int(sf_match.group("pf"))
        out["sf_index"] = int(sf_match.group("sf"))
        if sf_match.group("controller") is not None:
            out["controller"] = int(sf_match.group("controller"))
    return out


def parse_lspci_nn(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse `lspci -nn` lines; keep only NVIDIA-hint matches."""
    rows: List[Dict[str, Any]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        line = line.strip()
        if not line or not looks_like_nvidia(line):
            continue
        pci_match = _PCI.search(line)
        pci = normalize_pci_address(pci_match.group("pci") if pci_match else None)
        is_vf = "virtual function" in line.lower()
        rows.append(
            {
                "pci_address": pci,
                "description": line,
                "is_virtual_function": is_vf,
                "model": _model_from_lspci(line),
            }
        )
    return rows


def parse_ethtool_i(raw: Optional[str]) -> Dict[str, Optional[str]]:
    info: Dict[str, Optional[str]] = {}
    if not raw:
        return info
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip().lower()] = value.strip() or None
    return info


def parse_ip_d_link(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Per-interface attrs from `ip -d link show`.

    See the `_IP_ATTR` module comment: real output puts parentdev/
    portname/switchid on the same physical line as `link/ether ...`, so
    each line in an interface's block is scanned for known tokens
    rather than requiring them at the start of their own line.
    """
    ifaces: List[Dict[str, Any]] = []
    if not raw:
        return ifaces
    current: Optional[Dict[str, Any]] = None
    for line in raw.splitlines():
        header = _IP_HEADER.match(line)
        if header:
            current = {
                "netdev": header.group(1),
                "phys_port_name": None,
                "portname": None,
                "parentdev": None,
                "phys_switch_id": None,
            }
            ifaces.append(current)
            continue
        if current is None:
            continue
        for match in _IP_ATTR.finditer(line):
            key = match.group("key")
            value = match.group("value").strip() or None
            if key == "parentdev" and not current["parentdev"]:
                current["parentdev"] = value
            elif key == "phys_port_name" and not current["phys_port_name"]:
                current["phys_port_name"] = value
            elif key == "portname" and not current["portname"]:
                current["portname"] = value
            elif key in ("switchid", "phys_switch_id") and not current["phys_switch_id"]:
                current["phys_switch_id"] = value
    return ifaces


def parse_devlink_dev_show(raw: Optional[str]) -> List[str]:
    """PCI handles from `devlink dev show`. No eswitch mode claimed."""
    handles: List[str] = []
    if not raw:
        return handles
    for line in raw.splitlines():
        match = _DEVLINK_PCI.search(line.strip())
        if match:
            pci = match.group("pci").rstrip(":").lower()
            if pci not in handles:
                handles.append(pci)
    return handles


def parse_devlink_port_show(raw: Optional[str]) -> List[Dict[str, Any]]:
    ports: List[Dict[str, Any]] = []
    if not raw:
        return ports
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if ": " not in line:
            continue
        handle, rest = line.split(": ", 1)
        handle = handle.strip()
        attrs = dict(_DEVLINK_KV.findall(rest))
        pci = None
        pci_match = _DEVLINK_PCI.search(handle)
        if pci_match:
            pci = pci_match.group("pci").lower()
        flavour = attrs.get("flavour")
        netdev = attrs.get("netdev")
        ports.append(
            {
                "handle": handle,
                "pci_address": pci,
                "netdev": netdev,
                "flavour": flavour,
                "pfnum": _as_int(attrs.get("pfnum")),
                "vfnum": _as_int(attrs.get("vfnum")),
                "sfnum": _as_int(attrs.get("sfnum")),
            }
        )
    return ports


def _iface_portname(iface: Mapping[str, Any]) -> Optional[str]:
    return iface.get("portname") or iface.get("phys_port_name")


def map_dpu_vf_representors(
    *,
    ip_link: Optional[str] = None,
    devlink_port: Optional[str] = None,
    vf_indexes: Optional[Sequence[int]] = None,
    pf_pci: Optional[str] = None,
    host_vf_pci: Optional[Mapping[int, Optional[str]]] = None,
    ovs_show: Optional[str] = None,
    strategy: str = "nvidia-portname",
) -> Dict[str, Any]:
    """Map host VF indexes to DPU-side representors. Never guesses names.

    `eth0v1` / `enp4s0f0v0` are not representors. NVIDIA portnames such as
    pf0vf0 / pf0vf1 / pf1vf0 / pf0sf0 are parsed; SFs are not VF mappings.
    Uncertain matches stay confidence=low with representor_netdev null.
    """
    indexes = list(vf_indexes if vf_indexes is not None else (0, 1))
    pci_by_index = dict(host_vf_pci or {})
    ifaces = parse_ip_d_link(ip_link)
    ports = parse_devlink_port_show(devlink_port)
    by_netdev = {i.get("netdev"): i for i in ifaces if i.get("netdev")}
    want_pci = (pf_pci or "").strip().lower() or None
    ovs_blob = (ovs_show or "").lower()

    mappings: List[Dict[str, Any]] = []
    for vf_index in indexes:
        row: Dict[str, Any] = {
            "vf_index": vf_index,
            "vf_pci": pci_by_index.get(vf_index),
            "vf_pci_address": pci_by_index.get(vf_index),
            "dpu_representor_name": None,
            "dpu_representor_portname": None,
            "representor_netdev": None,
            "switchid": None,
            "phys_switch_id": None,
            "confidence": None,
            "mapping_confidence": None,
            "mapping_source": None,
            "mapping_method": strategy,
            "devlink_handle": None,
            "no_representor_reason": None,
            "troubleshooting_hints": [],
        }

        if not ip_link and not devlink_port:
            row["no_representor_reason"] = (
                "DPU representor probes planned but not executed (dry-run); "
                "no ip -d link / devlink port samples"
            )
            row["troubleshooting_hints"] = [
                "run mapping with fixture samples or execute DPU probes "
                "(ip -d link show, devlink port show) on the BlueField Arm OS",
            ]
            mappings.append(row)
            continue

        portname_hits: List[tuple] = []
        for iface in ifaces:
            token = _iface_portname(iface)
            parsed = parse_portname(token)
            if parsed.get("kind") != "vf" or parsed.get("vf_index") != vf_index:
                continue
            portname_hits.append((iface, parsed, token))
        pf0_pn = [h for h in portname_hits if h[1].get("pf_index") == 0]
        if pf0_pn:
            portname_hits = pf0_pn

        devlink_hits = [
            p
            for p in ports
            if p.get("flavour") == "pcivf" and p.get("vfnum") == vf_index
        ]
        if want_pci:
            pci_hits = [p for p in devlink_hits if (p.get("pci_address") or "") == want_pci]
            if pci_hits:
                devlink_hits = pci_hits
        pf0_dl = [p for p in devlink_hits if p.get("pfnum") == 0]
        if pf0_dl:
            devlink_hits = pf0_dl

        if len(devlink_hits) > 1 or len(portname_hits) > 1:
            row["confidence"] = "low"
            row["mapping_confidence"] = "low"
            row["mapping_source"] = "ambiguous"
            row["no_representor_reason"] = (
                "multiple DPU representor candidates for vf_index=%d; refusing to guess"
                % vf_index
            )
            mappings.append(row)
            continue

        dl = devlink_hits[0] if len(devlink_hits) == 1 else None
        pn = portname_hits[0] if len(portname_hits) == 1 else None

        if dl and pn:
            dl_name = dl.get("netdev")
            pn_name = pn[0].get("netdev")
            if dl_name and pn_name and dl_name != pn_name:
                row["confidence"] = "low"
                row["mapping_confidence"] = "low"
                row["mapping_source"] = "conflict"
                row["no_representor_reason"] = (
                    "devlink netdev %r disagrees with portname netdev %r"
                    % (dl_name, pn_name)
                )
                mappings.append(row)
                continue
            name = dl_name or pn_name
            token = pn[2]
            switchid = pn[0].get("phys_switch_id")
            source = "devlink_pcivf+nvidia-portname"
            if name and name.lower() in ovs_blob:
                source = source + "+ovs"
            row.update(
                {
                    "dpu_representor_name": name,
                    "representor_netdev": name,
                    "dpu_representor_portname": token,
                    "switchid": switchid,
                    "phys_switch_id": switchid,
                    "confidence": "high",
                    "mapping_confidence": "high",
                    "mapping_source": source,
                    "devlink_handle": dl.get("handle"),
                }
            )
            mappings.append(row)
            continue

        if dl and dl.get("netdev"):
            name = dl.get("netdev")
            iface = by_netdev.get(name) or {}
            token = _iface_portname(iface)
            parsed_token = parse_portname(token)
            parsed_name = parse_portname(name)
            # flavour=pcivf + vfnum is structured; still refuse netdev-only guesses
            if parsed_token.get("kind") != "vf" and parsed_name.get("kind") != "vf":
                row["confidence"] = "low"
                row["mapping_confidence"] = "low"
                row["mapping_source"] = "devlink_pcivf"
                row["dpu_representor_name"] = name
                row["no_representor_reason"] = (
                    "devlink flavour=pcivf vfnum=%d netdev %r has no NVIDIA "
                    "portname (pf<N>vf<M>); not treated as high confidence"
                    % (vf_index, name)
                )
                mappings.append(row)
                continue
            source = "devlink_pcivf"
            if parsed_token.get("kind") == "vf":
                source = "devlink_pcivf+nvidia-portname"
            switchid = iface.get("phys_switch_id")
            row.update(
                {
                    "dpu_representor_name": name,
                    "representor_netdev": name,
                    "dpu_representor_portname": token or (
                        name if parsed_name.get("kind") == "vf" else None
                    ),
                    "switchid": switchid,
                    "phys_switch_id": switchid,
                    "confidence": "high",
                    "mapping_confidence": "high",
                    "mapping_source": source,
                    "devlink_handle": dl.get("handle"),
                }
            )
            mappings.append(row)
            continue

        if pn:
            name = pn[0].get("netdev")
            token = pn[2]
            switchid = pn[0].get("phys_switch_id")
            # Structured portname is not a netdev-name guess, but without
            # devlink corroboration it is not high confidence.
            conf = "medium" if switchid else "low"
            row.update(
                {
                    "dpu_representor_name": name,
                    "representor_netdev": name if conf != "low" else None,
                    "dpu_representor_portname": token,
                    "switchid": switchid,
                    "phys_switch_id": switchid,
                    "confidence": conf,
                    "mapping_confidence": conf,
                    "mapping_source": "nvidia-portname",
                }
            )
            if conf == "low":
                row["no_representor_reason"] = (
                    "portname %r matched vf_index=%d without switchid or "
                    "devlink flavour=pcivf; not high confidence"
                    % (token, vf_index)
                )
            mappings.append(row)
            continue

        row["no_representor_reason"] = (
            "no DPU representor with portname pf<N>vf%d or devlink "
            "flavour=pcivf vfnum=%d (eth0v1 is not a representor)"
            % (vf_index, vf_index)
        )
        row["troubleshooting_hints"] = [
            "run `ip -d link show` and `devlink port show` on the DPU OS, not the hypervisor",
            "confirm representors are named/labelled pf<N>vf<M> (not eth0v1)",
        ]
        mappings.append(row)

    return {
        "schema_version": "odse-vf-representor-map/v1alpha1",
        "location": "dpu",
        "strategy": strategy,
        "mappings": mappings,
        "note": (
            "DPU-side NVIDIA mapping. eth0v1 is not a representor. "
            "Host sysfs virtfn mapping is not used in DPU mode."
        ),
    }


def build_inventory(
    *,
    lspci: Optional[str] = None,
    devlink_dev: Optional[str] = None,
    devlink_port: Optional[str] = None,
    ip_link: Optional[str] = None,
    ethtool_by_iface: Optional[Mapping[str, str]] = None,
    ovs: Optional[str] = None,
) -> OperationResult:
    """Pure parse of collected outputs into models."""
    lspci_rows = parse_lspci_nn(lspci)
    ip_ifaces = parse_ip_d_link(ip_link)
    dl_ports = parse_devlink_port_show(devlink_port)
    dl_devs = parse_devlink_dev_show(devlink_dev)
    ethtool_by_iface = dict(ethtool_by_iface or {})

    mlx5_ethtool: Dict[str, Dict[str, Optional[str]]] = {}
    for iface, raw in ethtool_by_iface.items():
        parsed = parse_ethtool_i(raw)
        blob = " ".join(str(v) for v in parsed.values() if v)
        if looks_like_nvidia(blob) or looks_like_nvidia(parsed.get("driver")):
            mlx5_ethtool[iface] = parsed

    devices: List[DPUDevice] = []
    pfs: List[PhysicalFunction] = []
    vfs: List[VirtualFunction] = []

    for row in lspci_rows:
        pci = row["pci_address"]
        if not pci:
            continue
        if row["is_virtual_function"]:
            vfs.append(
                VirtualFunction(
                    id="vf-%s" % pci,
                    pf_id=None,
                    vf_index=None,
                    pci_address=pci,
                    netdev=None,
                    bound_driver=None,
                )
            )
            continue
        is_ethernet_pf = "ethernet controller" in (row["description"] or "").lower()
        fw = None
        netdev = None
        for iface, info in mlx5_ethtool.items():
            bus = (info.get("bus-info") or "").lower()
            if bus == pci:
                netdev = iface
                fw = info.get("firmware-version")
                break
        if netdev is None:
            for iface in ip_ifaces:
                parent = (iface.get("parentdev") or "").lower()
                parsed_name = parse_portname(
                    iface.get("portname") or iface.get("phys_port_name")
                )
                if parent == pci and parsed_name["kind"] is None:
                    # parentdev on a PF uplink (p0 / eth0) — only if not a vf/sf token
                    token = iface.get("portname") or iface.get("phys_port_name")
                    if token and parse_portname(token)["kind"] in ("vf", "sf"):
                        continue
                    netdev = iface.get("netdev")
                    break
        cap = VendorCapability(
            vendor=VENDOR,
            sriov=None,
            switchdev=None,
            vdpa=None,
            ovs_hw_offload=None,
            notes="hints from lspci/ethtool only; capabilities not assumed",
        )
        device = DPUDevice(
            id="nvidia-%s" % pci,
            vendor=VENDOR,
            model=row["model"],
            serial_number=None,
            firmware_version=fw,
            pci_address=pci,
            management_address=None,
            capabilities=cap,
            vendor_extensions={"lspci": row["description"]},
        )
        devices.append(device)
        if is_ethernet_pf:
            pfs.append(
                PhysicalFunction(
                    id="pf-%s" % pci,
                    dpu_device_id=device.id,
                    pci_address=pci,
                    netdev=netdev,
                    eswitch_mode=None,
                    max_vfs=None,
                    current_vfs=None,
                )
            )

    representors: List[RepresentorPort] = []
    seen_rep: set = set()
    for port in dl_ports:
        flavour = (port.get("flavour") or "").lower()
        netdev = port.get("netdev")
        if flavour not in ("pcivf", "pcisf") or not netdev:
            continue
        if netdev in seen_rep:
            continue
        seen_rep.add(netdev)
        representors.append(
            RepresentorPort(
                id="rep-%s" % netdev,
                dpu_device_id=_device_id_for_pci(devices, port.get("pci_address")),
                virtual_function_id=None,
                netdev=netdev,
                vf_index=port.get("vfnum") if flavour == "pcivf" else None,
                mapping_method="devlink_%s" % flavour,
                confidence="high",
            )
        )

    for iface in ip_ifaces:
        netdev = iface.get("netdev")
        token = iface.get("portname") or iface.get("phys_port_name")
        parsed = parse_portname(token)
        if parsed["kind"] not in ("vf", "sf") or not netdev:
            continue
        if netdev in seen_rep:
            continue
        seen_rep.add(netdev)
        parent = (iface.get("parentdev") or "").lower() or None
        representors.append(
            RepresentorPort(
                id="rep-%s" % netdev,
                dpu_device_id=_device_id_for_pci(devices, parent),
                virtual_function_id=None,
                netdev=netdev,
                vf_index=parsed["vf_index"],
                mapping_method="portname" if iface.get("portname") else "phys_port_name",
                confidence="high",
            )
        )

    # sriov: proven if VF lspci rows or pcivf representors exist
    sriov_proven = bool(vfs) or any(
        (r.mapping_method or "").startswith("devlink_pcivf") or r.vf_index is not None
        for r in representors
    )
    for dev in devices:
        if dev.capabilities:
            dev.capabilities.sriov = True if sriov_proven else None

    pf_by_pci = {p.pci_address: p for p in pfs if p.pci_address}
    vf_count: Dict[str, int] = {}
    for r in representors:
        pci = None
        for d in devices:
            if d.id == r.dpu_device_id and d.pci_address:
                pci = d.pci_address
                break
        if pci:
            vf_count[pci] = vf_count.get(pci, 0) + (1 if r.vf_index is not None else 0)
    for pci, n in vf_count.items():
        pf = pf_by_pci.get(pci)
        if pf is not None:
            pf.current_vfs = n if n else None

    notes = []
    if dl_devs:
        notes.append("devlink_dev=%s" % ",".join(dl_devs))
    if ovs:
        notes.append("ovs-vsctl show captured (%s bytes)" % len(ovs))
    else:
        notes.append("ovs-vsctl absent or empty")

    status = STATUS_OK
    message = "parsed NVIDIA-hinted inventory"
    if not lspci_rows and not representors and not mlx5_ethtool:
        status = STATUS_SKIPPED
        message = "no NVIDIA/Mellanox/mlx5/BlueField/ConnectX hints in collected output"
    elif not pfs and not representors:
        message = (
            "NVIDIA hints present but no Ethernet PFs or representors parsed; "
            "empty lists mean nothing matched"
        )

    cap = VendorCapability(
        vendor=VENDOR,
        sriov=True if sriov_proven else None,
        switchdev=None,
        vdpa=None,
        ovs_hw_offload=None,
        notes="; ".join(notes),
    )
    return OperationResult(
        operation="discover",
        vendor=VENDOR,
        status=status,
        message=message,
        destructive=False,
        executed=True,
        devices=devices,
        physical_functions=pfs,
        virtual_functions=vfs,
        representors=representors,
        capabilities=cap,
        details={
            "ovs_present": bool(ovs),
            "devlink_devices": dl_devs,
            "mlx5_interfaces": sorted(mlx5_ethtool.keys()),
        },
    )


def collect_probes(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run read-only probes or return fixture `samples`.

    samples keys: lspci, devlink_dev, devlink_port, ip_link, ovs,
    ethtool (dict netdev -> text).
    """
    commands: List[CommandResult] = []
    if samples is not None:
        ethtool = samples.get("ethtool") or {}
        return {
            "lspci": samples.get("lspci"),
            "devlink_dev": samples.get("devlink_dev"),
            "devlink_port": samples.get("devlink_port"),
            "ip_link": samples.get("ip_link"),
            "ovs": samples.get("ovs"),
            "ethtool": ethtool,
            "commands": commands,
        }

    def probe(argv: Sequence[str]) -> CommandResult:
        if not command_exists(argv[0]):
            LOG.info("skip missing command: %s", argv[0])
            result = CommandResult(
                argv=list(argv),
                executed=False,
                dry_run=False,
                reason="command not found",
            )
            commands.append(result)
            return result
        result = run(list(argv), dry_run=dry_run, timeout=PROBE_TIMEOUT, shell=False)
        commands.append(result)
        return result

    lspci = probe(["lspci", "-nn"])
    devlink_dev = probe(["devlink", "dev", "show"])
    devlink_port = probe(["devlink", "port", "show"])
    ip_link = probe(["ip", "-d", "link", "show"])
    ovs = probe(["ovs-vsctl", "show"]) if command_exists("ovs-vsctl") else CommandResult(
        argv=["ovs-vsctl", "show"],
        executed=False,
        reason="command not found",
    )
    if not command_exists("ovs-vsctl"):
        commands.append(ovs)

    ethtool_map: Dict[str, str] = {}
    ifaces = _candidate_ifaces(_stdout(ip_link))
    if not ifaces:
        ifaces = [n for n in list_netdevs() if n != "lo"]
    for name in ifaces:
        if dry_run:
            break
        if not command_exists("ethtool"):
            break
        et = run(["ethtool", "-i", name], dry_run=False, timeout=PROBE_TIMEOUT)
        commands.append(et)
        text = _stdout(et)
        if not text:
            continue
        parsed = parse_ethtool_i(text)
        blob = " ".join(filter(None, [parsed.get("driver"), parsed.get("bus-info"), text]))
        if looks_like_nvidia(blob):
            ethtool_map[name] = text

    return {
        "lspci": _stdout(lspci),
        "devlink_dev": _stdout(devlink_dev),
        "devlink_port": _stdout(devlink_port),
        "ip_link": _stdout(ip_link),
        "ovs": _stdout(ovs),
        "ethtool": ethtool_map,
        "commands": commands,
    }


def discover(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    pf: Optional[str] = None,
) -> OperationResult:
    packed = collect_probes(samples=samples, dry_run=dry_run)
    result = build_inventory(
        lspci=packed.get("lspci"),
        devlink_dev=packed.get("devlink_dev"),
        devlink_port=packed.get("devlink_port"),
        ip_link=packed.get("ip_link"),
        ethtool_by_iface=packed.get("ethtool") or {},
        ovs=packed.get("ovs"),
    )
    result.commands = list(packed.get("commands") or [])
    if dry_run:
        result.executed = False
        result.message = "dry-run: probes not executed; " + result.message
    if pf:
        result = _filter_pf(result, pf)
    return result


def discover_representors(
    pf: Optional[str] = None,
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
) -> OperationResult:
    result = discover(samples=samples, dry_run=dry_run, pf=pf)
    result.operation = "discover_representors"
    return result


def _filter_pf(result: OperationResult, pf: str) -> OperationResult:
    """Keep PFs/representors only when pf matches pci or netdev exactly."""
    needle = pf.strip().lower()
    keep_ids = set()
    keep_pci = set()
    for p in result.physical_functions:
        if (p.pci_address or "").lower() == needle or (p.netdev or "").lower() == needle:
            if p.dpu_device_id:
                keep_ids.add(p.dpu_device_id)
            if p.pci_address:
                keep_pci.add(p.pci_address.lower())
    if not keep_ids:
        result.representors = []
        result.physical_functions = []
        result.devices = []
        result.message = "pf %r did not match a discovered PF pci/netdev; not guessed" % pf
        result.details["pf_filter"] = pf
        return result
    result.devices = [d for d in result.devices if d.id in keep_ids]
    result.physical_functions = [
        p for p in result.physical_functions if p.dpu_device_id in keep_ids
    ]
    result.representors = [
        r for r in result.representors if r.dpu_device_id in keep_ids
    ]
    result.details["pf_filter"] = pf
    return result


def _device_id_for_pci(devices: Sequence[DPUDevice], pci: Optional[str]) -> Optional[str]:
    if not pci:
        return None
    want = pci.lower()
    for d in devices:
        if (d.pci_address or "").lower() == want:
            return d.id
    return None


def _model_from_lspci(line: str) -> Optional[str]:
    """Vendor+device description from an `lspci -nn` line.

    Must not split on the first bare ":" -- the PCI address itself
    contains one (`0d:00.0`, or `0000:0d:00.0` with a domain), so a
    naive `line.split(":", 1)` grabs a fragment of the address/class
    instead of the description. The device-class code is always closed
    with "]: " right before the vendor+device description, so split
    there instead.
    """
    if "]: " not in line:
        return None
    try:
        desc = line.split("]: ", 1)[1].strip()
        desc = re.sub(
            r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]\s*(?:\(rev\s+[0-9a-fA-F]+\))?\s*$",
            "",
            desc,
        )
        return desc or None
    except IndexError:
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stdout(result: Optional[CommandResult]) -> Optional[str]:
    if result is None or not result.stdout:
        return None
    return result.stdout


def _candidate_ifaces(ip_raw: Optional[str]) -> List[str]:
    names = []
    for iface in parse_ip_d_link(ip_raw):
        name = iface.get("netdev")
        if name and name != "lo":
            names.append(name)
    return names
