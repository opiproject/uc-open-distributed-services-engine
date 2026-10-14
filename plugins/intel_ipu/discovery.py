"""Intel IPU discovery (read-only).

Collects lspci -nn, ip -d link show, devlink dev show. Redfish/IMC/OPI
endpoints come from a config file if provided — no management CLI is
run unless that file names one, and even then this module does not
execute it.

ice / E810 host NICs are not IPUs. A line must contain IPU, E2100,
"Infrastructure Processing Unit"/"Infrastructure Data Path Function",
"idpf", or a known IPU PCI device ID (Intel alone is not enough).

Matching on free text alone is not reliable: the same silicon's PCI
device ID can render as different human-readable strings depending on
the local `pci.ids` database version. Seen in the wild for PCI device
ID 1452 (vendor 8086): "Infrastructure Processing Unit E2100" on one
host's `lspci -nn` and "Infrastructure Data Path Function" on another,
real E2100 host (see docs/vendor-readiness/intel-ipu-e2100.md and the
`host-4` discovery failure this fixed). `IPU_PCI_DEVICE_IDS` matches on
the `[vvvv:dddd]` suffix `lspci -nn` always prints, independent of
whatever text a given `pci.ids` snapshot has for it. Do not add an ID
there without a real `lspci -nn` line (fixture or actual host)
confirming it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ..base.commands import command_exists, run
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
VENDOR = "intel"
CONFIG_ENV = "ODSE_INTEL_IPU_CONFIG"

# Strong IPU hints only. "Intel" on an ice NIC is not sufficient.
IPU_HINTS = (
    "ipu",
    "e2100",
    "infrastructure processing unit",
    "infrastructure data path function",
    "idpf",
)

# Known Intel IPU PCI device IDs ("vendor:device", lowercase), matched
# against the `[vvvv:dddd]` suffix `lspci -nn` prints regardless of the
# local pci.ids text -- see module docstring. 8086:1452 is confirmed by
# both tests/fixtures/intel_ipu/lspci.txt ("Infrastructure Processing
# Unit E2100") and a real host-4 IPU E2100 ("Infrastructure Data Path
# Function").
IPU_PCI_DEVICE_IDS = ("8086:1452",)

# Domain-prefixed (0000:0a:00.0) or host-short lspci form (0a:00.0) --
# real `lspci -nn` on host-4 (a real E2100 host) prints the short form.
# See normalize_pci_address().
_PCI = re.compile(
    r"(?P<pci>(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_DEVLINK_PCI = re.compile(
    r"pci/(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])"
)
_IP_HEADER = re.compile(r"^\d+:\s+([^:@]+)(?:@\S+)?:")
# lspci -nn always ends a device line with "[vendor:device]" and
# optionally "(rev xx)" after it, e.g. "... [8086:1452] (rev 20)".
_LSPCI_VENDOR_DEVICE_ID = re.compile(
    r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]\s*(?:\(rev\s+[0-9a-fA-F]+\))?\s*$"
)
# Real `ip -d link show` packs attribute/value pairs onto the same long
# line as `link/ether ...` (confirmed on real host-4 E2100 hardware --
# see example-output/host-4/Step1.txt), not one attribute per line as an
# earlier version of this parser (and its NVIDIA/tooling counterparts)
# assumed. Search each interface's whole block for these tokens instead
# of anchoring on line-start so this works regardless of iproute2
# version/line-wrapping. Some iproute2 versions print `switchid`, others
# `phys_switch_id`; both are accepted.
_IP_ATTR = re.compile(
    r"\b(?P<key>parentdev|portname|phys_port_name|switchid|phys_switch_id)"
    r"\s+(?P<value>\S+)"
)
# IPU sub-function/representor-style netdevs are named <base>d<N>, e.g.
# enp10s0f0d1, enp10s0f0d2 alongside the PF uplink enp10s0f0 (see
# docs/operations/first-run-intel-vm2vm.md step 1). Not asserted as
# representors -- only reported alongside the PF for visibility.
_IPU_SUBFUNCTION_SUFFIX = re.compile(r"d\d+$")
PROBE_TIMEOUT = 15.0


def looks_like_intel_ipu(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    if any(hint in lower for hint in IPU_HINTS):
        return True
    match = _LSPCI_VENDOR_DEVICE_ID.search(text)
    if match:
        vendor_device = "%s:%s" % (match.group(1).lower(), match.group(2).lower())
        if vendor_device in IPU_PCI_DEVICE_IDS:
            return True
    return False


def normalize_pci_address(addr: Optional[str]) -> Optional[str]:
    """Normalize `0a:00.0` from `lspci -nn` to `0000:0a:00.0`."""
    if not addr:
        return None
    pci = addr.strip().lower()
    if pci.count(":") == 1:
        return "0000:%s" % pci
    return pci


def load_config(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Load JSON config. Missing file → {} (not an error)."""
    candidate = path or os.environ.get(CONFIG_ENV)
    if not candidate:
        return {}
    target = Path(candidate)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        LOG.info("intel config %s unreadable: %s", target, exc)
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        LOG.warning("intel config %s invalid JSON: %s", target, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def config_public_view(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Config for OperationResult.details — secrets redacted."""
    out: Dict[str, Any] = {}
    for key in (
        "redfish_base",
        "imc_address",
        "opi_endpoint",
        "opi_mode",
        "management_cli",
        "management_tool",
    ):
        if key in cfg and cfg[key] not in (None, ""):
            out[key] = cfg[key]
    if cfg.get("username"):
        out["username"] = cfg.get("username")
    if cfg.get("password") or cfg.get("token"):
        out["secret"] = "***REDACTED***"
    return out


def parse_lspci_nn(raw: Optional[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        line = line.strip()
        if not line or not looks_like_intel_ipu(line):
            continue
        pci_match = _PCI.search(line)
        pci = normalize_pci_address(pci_match.group("pci") if pci_match else None)
        rows.append(
            {
                "pci_address": pci,
                "description": line,
                "model": _model_from_lspci(line),
            }
        )
    return rows


def parse_devlink_dev_show(raw: Optional[str]) -> List[str]:
    handles: List[str] = []
    if not raw:
        return handles
    for line in raw.splitlines():
        match = _DEVLINK_PCI.search(line.strip())
        if match:
            pci = match.group("pci").lower()
            if pci not in handles:
                handles.append(pci)
    return handles


def parse_ip_d_link(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Per-interface attrs from `ip -d link show` (netdev/parentdev/etc).

    See the `_IP_ATTR` module comment: real output puts these tokens on
    the same physical line as `link/ether ...`, so we scan each
    interface's whole block for known tokens rather than requiring them
    at the start of their own line.
    """
    ifaces: List[Dict[str, Any]] = []
    if not raw:
        return ifaces
    current: Optional[Dict[str, Any]] = None
    for line in raw.splitlines():
        header = _IP_HEADER.match(line)
        if header:
            name = header.group(1)
            current = {"netdev": name, "parentdev": None, "portname": None, "switchid": None}
            if name and name != "lo":
                ifaces.append(current)
            continue
        if current is None:
            continue
        for match in _IP_ATTR.finditer(line):
            key = match.group("key")
            value = match.group("value").strip() or None
            if key == "parentdev":
                current["parentdev"] = value
            elif key in ("portname", "phys_port_name") and not current["portname"]:
                current["portname"] = value
            elif key in ("switchid", "phys_switch_id") and not current["switchid"]:
                current["switchid"] = value
    return ifaces


def netdev_names(ifaces: Sequence[Mapping[str, Any]]) -> List[str]:
    """Plain netdev name list, e.g. for `details.ip_netdevs`."""
    return [str(i["netdev"]) for i in ifaces if i.get("netdev")]


def build_inventory(
    *,
    lspci: Optional[str] = None,
    ip_link: Optional[str] = None,
    devlink_dev: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    cfg = dict(config or {})
    rows = parse_lspci_nn(lspci)
    dl = parse_devlink_dev_show(devlink_dev)
    ifaces = parse_ip_d_link(ip_link)
    devices: List[DPUDevice] = []
    pfs: List[PhysicalFunction] = []
    for row in rows:
        pci = row["pci_address"]

        # Correlate netdevs whose `ip -d link show` parentdev matches this
        # PCI address. The plain-named netdev (no `d<N>` suffix) is the PF
        # uplink; `d<N>`-suffixed siblings are sub-function/representor-
        # style netdevs (see `_IPU_SUBFUNCTION_SUFFIX`) -- reported for
        # visibility only, never asserted as proven representors.
        pf_netdev: Optional[str] = None
        subfunction_netdevs: List[str] = []
        if pci:
            for iface in ifaces:
                parent = normalize_pci_address(iface.get("parentdev"))
                name = iface.get("netdev")
                if parent != pci or not name:
                    continue
                if _IPU_SUBFUNCTION_SUFFIX.search(name):
                    subfunction_netdevs.append(name)
                elif pf_netdev is None:
                    pf_netdev = name

        vendor_extensions: Dict[str, Any] = {"lspci": row["description"]}
        if subfunction_netdevs:
            vendor_extensions["subfunction_netdevs"] = subfunction_netdevs

        cap = VendorCapability(
            vendor=VENDOR,
            sriov=None,
            switchdev=None,
            vdpa=None,
            ovs_hw_offload=None,
            opi_native=None,
            notes="IPU hint from lspci; datapath/OVS not assumed",
        )
        dev = DPUDevice(
            id=("intel-%s" % pci) if pci else None,
            vendor=VENDOR,
            model=row["model"],
            serial_number=None,
            firmware_version=None,
            pci_address=pci,
            management_address=cfg.get("imc_address") or cfg.get("redfish_base"),
            capabilities=cap,
            vendor_extensions=vendor_extensions,
        )
        devices.append(dev)
        if pci:
            pfs.append(
                PhysicalFunction(
                    id="pf-%s" % pci,
                    dpu_device_id=dev.id,
                    pci_address=pci,
                    netdev=pf_netdev,
                    eswitch_mode=None,
                )
            )

    redfish = cfg.get("redfish_base")
    opi_ep = cfg.get("opi_endpoint")
    mgmt_cli = cfg.get("management_cli") or cfg.get("management_tool")
    if not devices and not redfish and not opi_ep:
        status = STATUS_SKIPPED
        message = "no IPU/E2100 hints in lspci and no Redfish/OPI config"
    else:
        status = STATUS_OK
        message = "Intel IPU discovery (OVS-DOCA-like datapath not assumed)"
        if not devices and (redfish or opi_ep):
            message = (
                "no PCI IPU hint; Redfish/OPI endpoint configured only "
                "(ice NIC is not an IPU)"
            )

    LOG.info("intel discovery devices=%s redfish=%s", len(devices), bool(redfish))
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
        capabilities=VendorCapability(
            vendor=VENDOR,
            switchdev=None,
            ovs_hw_offload=None,
            opi_native=True if opi_ep else None,
            notes="OPI/OpenShift-first; kernel switchdev discovery-required",
        ),
        details={
            "config": config_public_view(cfg),
            "devlink_devices": dl,
            "ip_netdevs": netdev_names(ifaces),
            "redfish_configured": bool(redfish),
            "opi_configured": bool(opi_ep),
            "management_cli": mgmt_cli,
            "management_cli_executed": False,
            "ice_not_ipu": True,
        },
    )


def collect_probes(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    config_path: Optional[Union[str, Path]] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = dict(config) if config is not None else load_config(config_path)
    if samples is not None:
        return {
            "lspci": samples.get("lspci"),
            "ip_link": samples.get("ip_link"),
            "devlink_dev": samples.get("devlink_dev"),
            "config": samples.get("config", cfg),
            "commands": [],
        }
    commands: List[CommandResult] = []

    def probe(argv: Sequence[str]) -> CommandResult:
        if not command_exists(argv[0]):
            result = CommandResult(
                argv=list(argv), executed=False, reason="command not found"
            )
            commands.append(result)
            return result
        result = run(list(argv), dry_run=dry_run, timeout=PROBE_TIMEOUT)
        commands.append(result)
        return result

    lspci = probe(["lspci", "-nn"])
    ip_link = probe(["ip", "-d", "link", "show"])
    devlink = probe(["devlink", "dev", "show"])
    return {
        "lspci": lspci.stdout,
        "ip_link": ip_link.stdout,
        "devlink_dev": devlink.stdout,
        "config": cfg,
        "commands": commands,
    }


def discover(
    *,
    samples: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    config_path: Optional[Union[str, Path]] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> OperationResult:
    packed = collect_probes(
        samples=samples, dry_run=dry_run, config_path=config_path, config=config
    )
    result = build_inventory(
        lspci=packed.get("lspci"),
        ip_link=packed.get("ip_link"),
        devlink_dev=packed.get("devlink_dev"),
        config=packed.get("config") or {},
    )
    result.commands = list(packed.get("commands") or [])
    if dry_run:
        result.executed = False
        result.message = "dry-run: " + result.message
    return result


def _model_from_lspci(line: str) -> Optional[str]:
    """Vendor+device description from an `lspci -nn` line.

    Must not split on the first bare ":" -- the PCI address itself
    contains one (`0a:00.0`, or `0000:0a:00.0` with a domain), so a naive
    `line.split(":", 1)` grabs a fragment of the address/class instead of
    the description. The device-class code is always closed with "]: "
    right before the vendor+device description, so split there instead.
    """
    if "]: " not in line:
        return None
    desc = line.split("]: ", 1)[1].strip()
    desc = re.sub(
        r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]\s*(?:\(rev\s+[0-9a-fA-F]+\))?\s*$",
        "",
        desc,
    )
    return desc or None
