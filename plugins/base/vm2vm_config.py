"""Loader/validator for the VM-to-VM single-DPU lab config.

Schema: config/lab-vm2vm-single-dpu.schema.yaml (odse-lab-vm2vm/v1alpha1).
Distinct from plugins.base.lab_config (odse-lab-config/v1alpha1), which is
the general multi-host/multi-vendor inventory used by tools/vendorctl.py.
This module is used by tools/labctl.py and tools/forwarding_intent.py.

Unknown/omitted optional facts stay None; nothing here invents a PF, VF,
PCI address, or representor that was not present in the config or in a
previously-saved discovery/representor-mapping result.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .lab_config import intel_plugin_config as _lab_config_intel_plugin_config
from .lab_config import normalize_vendor, parse_simple_yaml
from .models import ForwardingEndpoint, ForwardingIntent, ForwardingOffloadRequest, ForwardingPolicy

LOG = logging.getLogger(__name__)

SCHEMA_ID = "odse-lab-vm2vm/v1alpha1"
_PCI_BDF = re.compile(
    r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$"
)
_IPV4_LITERAL = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
NVIDIA_DPU_PROFILE = "nvidia-bluefield-dpu-mode"
NVIDIA_HOST_SWITCHDEV_PROFILE = "nvidia-connectx-host-switchdev"
NVIDIA_DPU_MODE_ERROR = (
    "Invalid NVIDIA DPU-mode configuration: this profile requires "
    "DPU-side OVS/OVS-DOCA ownership. Host OVS must not own VF "
    "representors for forwarding."
)

_REQUIRED_HOST_FIELDS = ("name", "management_interface", "ssh_user", "os_family")
_REQUIRED_DPU_FIELDS = (
    "pf_name",
    "pf_pci",
    "num_vfs",
    "enable_switchdev",
    "enable_ovs_hw_offload",
    "preferred_datapath",
)
_VALID_DATAPATHS = ("system", "netdev", "doca", "dao", "opi", "discovery")
_REQUIRED_VM_FIELDS = ("name", "image", "memory_mb", "vcpus", "vf_index", "ip_cidr", "ssh_user")
_REQUIRED_TRAFFIC_FIELDS = ("type", "duration_seconds", "iperf_parallel_streams")
_REQUIRED_VALIDATION_FIELDS = (
    "collect_ovs_flows",
    "collect_tc_filters",
    "collect_ethtool_stats",
    "collect_cpu_stats",
    "collect_vendor_telemetry",
)
_REQUIRED_SAFETY_FIELDS = (
    "dry_run_default",
    "require_apply",
    "forbid_management_interface_changes",
    "cleanup_on_failure",
)


def load_vm2vm_config(path: Union[str, Path]) -> Dict[str, Any]:
    """Read and parse a VM2VM lab YAML file. Does not validate contents."""
    target = Path(path)
    raw = target.read_text(encoding="utf-8")
    data: Any = None
    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    if data is None:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(raw)
        except ImportError:
            LOG.info("PyYAML not installed; using simple YAML parser for %s", target)
            data = parse_simple_yaml(raw)
    if not isinstance(data, dict):
        raise ValueError("%s must contain a YAML mapping" % target)
    return data


def validate_vm2vm_config(config: Optional[Dict[str, Any]]) -> List[str]:
    """Return a list of validation problems. Empty list == valid.

    Deliberately conservative: flags missing/wrong-typed required fields
    and known-dangerous combinations (num_vfs != 2, duplicate vf_index,
    management interface reused as the PF, safety fields relaxed in the
    config itself) rather than silently accepting them.
    """
    issues: List[str] = []
    if not isinstance(config, dict):
        return ["config must be a YAML mapping"]

    if config.get("schema") != SCHEMA_ID:
        issues.append(
            "schema must be %r (got %r)" % (SCHEMA_ID, config.get("schema"))
        )

    host = config.get("host") or {}
    if not isinstance(host, dict):
        issues.append("host must be a mapping")
        host = {}
    for field_name in _REQUIRED_HOST_FIELDS:
        if not host.get(field_name):
            issues.append("host.%s is required" % field_name)
    mgmt_iface = host.get("management_interface")
    if mgmt_iface and "<" not in str(mgmt_iface) and _IPV4_LITERAL.match(str(mgmt_iface).strip()):
        issues.append(
            "host.management_interface must be a netdev name (e.g. ens1f0np0 or "
            "eno1), not an IP address (got %r). preserved_interfaces() and "
            "tools/lab_cleanup.py compare this value literally against real "
            "PF/bridge/port names to decide what labctl must never touch -- an "
            "IP address here never matches anything and silently disables that "
            "safety gate. Put the IP under dpu.management_ip/host discovery "
            "notes instead and use the actual interface name here." % mgmt_iface
        )

    vendor = config.get("vendor") or {}
    if not isinstance(vendor, dict):
        issues.append("vendor must be a mapping")
        vendor = {}
    vendor_type = normalize_vendor(vendor.get("type"))
    valid_vendor_types = {"nvidia-bluefield", "intel-ipu", "marvell-octeon", "amd-pensando"}
    if vendor_type not in valid_vendor_types:
        issues.append(
            "vendor.type must be one of %s (got %r)"
            % (sorted(valid_vendor_types), vendor.get("type"))
        )

    dpu = config.get("dpu") or {}
    if not isinstance(dpu, dict):
        issues.append("dpu must be a mapping")
        dpu = {}
    for field_name in _REQUIRED_DPU_FIELDS:
        if dpu.get(field_name) is None:
            issues.append("dpu.%s is required" % field_name)
    num_vfs = dpu.get("num_vfs")
    if num_vfs is not None and num_vfs != 2:
        issues.append(
            "dpu.num_vfs must be exactly 2 for this VM-to-VM single-DPU use "
            "case (got %r); this workflow is not designed for other counts" % num_vfs
        )
    if dpu.get("preferred_datapath") not in (None, *_VALID_DATAPATHS):
        issues.append(
            "dpu.preferred_datapath must be one of %s (got %r)"
            % (_VALID_DATAPATHS, dpu.get("preferred_datapath"))
        )
    if host.get("management_interface") and dpu.get("pf_name"):
        if host["management_interface"] == dpu["pf_name"]:
            issues.append(
                "host.management_interface must not be the same netdev as "
                "dpu.pf_name -- see config/README-lab-vm2vm.md safety section"
            )
    if host.get("management_interface") and dpu.get("uplink_name"):
        if host["management_interface"] == dpu["uplink_name"]:
            issues.append(
                "host.management_interface must not be the same netdev as "
                "dpu.uplink_name"
            )
    pf_pci = dpu.get("pf_pci")
    if pf_pci not in (None, "") and "<" not in str(pf_pci):
        if not _PCI_BDF.match(str(pf_pci).strip()):
            issues.append(
                "dpu.pf_pci must be domain:bus:dev.func with colons "
                "(e.g. 0000:0d:00.0), not %r" % pf_pci
            )

    ovs = config.get("ovs") or {}
    if not isinstance(ovs, dict) or not ovs.get("bridge"):
        issues.append("ovs.bridge is required")

    vms = config.get("vms")
    if not isinstance(vms, list) or len(vms) != 2:
        issues.append("vms must be a list of exactly 2 entries (got %r)" % (vms,))
        vms = vms if isinstance(vms, list) else []
    seen_vf_indexes: List[int] = []
    for i, vm in enumerate(vms):
        if not isinstance(vm, dict):
            issues.append("vms[%d] must be a mapping" % i)
            continue
        for field_name in _REQUIRED_VM_FIELDS:
            if vm.get(field_name) is None:
                issues.append("vms[%d].%s is required" % (i, field_name))
        vf_index = vm.get("vf_index")
        if isinstance(vf_index, int):
            if vf_index not in (0, 1):
                issues.append(
                    "vms[%d].vf_index must be 0 or 1 for a 2-VF lab (got %r)"
                    % (i, vf_index)
                )
            elif vf_index in seen_vf_indexes:
                issues.append(
                    "vms[%d].vf_index=%r duplicates another vms[].vf_index "
                    "-- each VM must use a distinct VF" % (i, vf_index)
                )
            else:
                seen_vf_indexes.append(vf_index)
        memory_mb = vm.get("memory_mb")
        if isinstance(memory_mb, int) and memory_mb <= 0:
            issues.append("vms[%d].memory_mb must be > 0" % i)
        vcpus = vm.get("vcpus")
        if isinstance(vcpus, int) and vcpus <= 0:
            issues.append("vms[%d].vcpus must be > 0" % i)
        ip_cidr = vm.get("ip_cidr")
        if isinstance(ip_cidr, str) and "/" not in ip_cidr:
            issues.append(
                "vms[%d].ip_cidr should be in CIDR form (e.g. 198.51.100.11/24), got %r"
                % (i, ip_cidr)
            )

    traffic = config.get("traffic") or {}
    if not isinstance(traffic, dict):
        issues.append("traffic must be a mapping")
        traffic = {}
    for field_name in _REQUIRED_TRAFFIC_FIELDS:
        if traffic.get(field_name) is None:
            issues.append("traffic.%s is required" % field_name)

    validation = config.get("validation") or {}
    if not isinstance(validation, dict):
        issues.append("validation must be a mapping")
        validation = {}
    for field_name in _REQUIRED_VALIDATION_FIELDS:
        if not isinstance(validation.get(field_name), bool):
            issues.append("validation.%s must be a boolean" % field_name)

    safety = config.get("safety") or {}
    if not isinstance(safety, dict):
        issues.append("safety must be a mapping")
        safety = {}
    for field_name in _REQUIRED_SAFETY_FIELDS:
        if not isinstance(safety.get(field_name), bool):
            issues.append("safety.%s must be a boolean" % field_name)
    if safety.get("require_apply") is False:
        issues.append(
            "safety.require_apply must be true -- labctl does not support "
            "relaxing this from a config file"
        )
    if safety.get("forbid_management_interface_changes") is False:
        issues.append(
            "safety.forbid_management_interface_changes must be true -- "
            "labctl does not support relaxing this from a config file"
        )

    issues.extend(validate_nvidia_dpu_mode(config))

    return issues


def effective_nvidia_profile(config: Optional[Dict[str, Any]]) -> Optional[str]:
    vendor = (config or {}).get("vendor") or {}
    if normalize_vendor(vendor.get("type")) != "nvidia-bluefield":
        return None
    profile = vendor.get("profile")
    if not profile:
        return NVIDIA_DPU_PROFILE
    return str(profile)


def validate_nvidia_dpu_mode(config: Optional[Dict[str, Any]]) -> List[str]:
    """Enforce DPU-side OVS ownership when the NVIDIA DPU-mode profile is active.

    nvidia-bluefield with no profile defaults to nvidia-bluefield-dpu-mode
    so host OVS cannot silently own representors.
    """
    if not isinstance(config, dict):
        return []
    vendor = config.get("vendor") or {}
    if normalize_vendor(vendor.get("type")) != "nvidia-bluefield":
        return []
    profile = effective_nvidia_profile(config)
    if profile == NVIDIA_HOST_SWITCHDEV_PROFILE:
        return []
    if profile != NVIDIA_DPU_PROFILE:
        return [
            "vendor.profile %r is not a known NVIDIA profile (%s or %s)"
            % (profile, NVIDIA_DPU_PROFILE, NVIDIA_HOST_SWITCHDEV_PROFILE)
        ]

    nvidia = config.get("nvidia") if isinstance(config.get("nvidia"), dict) else {}
    host_fwd = (
        config.get("host_forwarding_control")
        if isinstance(config.get("host_forwarding_control"), dict)
        else {}
    )
    dpu_fwd = (
        config.get("dpu_forwarding_control")
        if isinstance(config.get("dpu_forwarding_control"), dict)
        else {}
    )
    failures: List[str] = []
    if nvidia.get("mode") != "dpu":
        failures.append("nvidia.mode must be dpu")
    if nvidia.get("ovs_location") != "dpu":
        failures.append("nvidia.ovs_location must be dpu")
    if nvidia.get("representor_location") != "dpu":
        failures.append("nvidia.representor_location must be dpu")
    if nvidia.get("allow_host_ovs_for_forwarding") is not False:
        failures.append("nvidia.allow_host_ovs_for_forwarding must be false")
    if host_fwd.get("enabled") is not False:
        failures.append("host_forwarding_control.enabled must be false")
    if dpu_fwd.get("enabled") is not True:
        failures.append("dpu_forwarding_control.enabled must be true")
    if failures:
        return [NVIDIA_DPU_MODE_ERROR + " " + "; ".join(failures)]
    return []


def representor_mapping_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Where VF representors are mapped: DPU (default NVIDIA) or host.

    Host mapping is for the advanced nvidia-connectx-host-switchdev profile
    (or an explicit representor_mapping.location: host). DPU mode must not
    silently scan host sysfs for representors.
    """
    cfg = config or {}
    rmap = cfg.get("representor_mapping") if isinstance(cfg.get("representor_mapping"), dict) else {}
    nvidia = cfg.get("nvidia") if isinstance(cfg.get("nvidia"), dict) else {}
    vendor = cfg.get("vendor") if isinstance(cfg.get("vendor"), dict) else {}
    profile = effective_nvidia_profile(cfg)
    location = rmap.get("location")
    if location not in ("dpu", "host"):
        if nvidia.get("representor_location") in ("dpu", "host"):
            location = nvidia.get("representor_location")
        elif profile == NVIDIA_HOST_SWITCHDEV_PROFILE:
            location = "host"
        elif nvidia.get("mode") == "dpu":
            location = "dpu"
        elif normalize_vendor(vendor.get("type")) == "marvell-octeon":
            location = "dpu"
        else:
            location = "host"
    nvidia_vendor = normalize_vendor(vendor.get("type")) == "nvidia-bluefield"
    marvell_vendor = normalize_vendor(vendor.get("type")) == "marvell-octeon"
    strategy = rmap.get("strategy")
    if not strategy:
        if location == "dpu" and nvidia_vendor:
            strategy = "nvidia-portname"
        elif marvell_vendor or str(rmap.get("model") or "").lower() == "dpdk":
            strategy = "marvell-dpdk-representor"
        else:
            strategy = "sysfs-devlink"
    model = rmap.get("model")
    if not model:
        model = "dpdk" if marvell_vendor else "netdev"
    require_high = rmap.get("require_high_confidence")
    if require_high is None:
        require_high = location == "dpu"
    return {
        "location": location,
        "strategy": str(strategy),
        "model": str(model),
        "require_high_confidence": bool(require_high),
    }


def vf_indexes_from_config(config: Optional[Dict[str, Any]]) -> List[int]:
    indexes: List[int] = []
    for vm in (config or {}).get("vms") or []:
        if not isinstance(vm, dict):
            continue
        idx = vm.get("vf_index")
        if isinstance(idx, int) and idx not in indexes:
            indexes.append(idx)
    if indexes:
        return indexes
    num = ((config or {}).get("dpu") or {}).get("num_vfs")
    if isinstance(num, int) and num > 0:
        return list(range(num))
    return [0, 1]


def host_vf_pci_by_index(config: Optional[Dict[str, Any]]) -> Dict[int, str]:
    """Host VF PCI from vms[].vf_pci when present and not a placeholder."""
    out: Dict[int, str] = {}
    for vm in (config or {}).get("vms") or []:
        if not isinstance(vm, dict):
            continue
        idx = vm.get("vf_index")
        pci = vm.get("vf_pci")
        if not isinstance(idx, int) or not pci:
            continue
        text = str(pci).strip()
        if not text or text.startswith("<"):
            continue
        out[idx] = text
    return out


def config_dry_run(config: Optional[Dict[str, Any]]) -> bool:
    """True unless safety.dry_run_default is explicitly false."""
    if not config:
        return True
    safety = config.get("safety") or {}
    value = safety.get("dry_run_default")
    if value is None:
        return True
    return bool(value)


def get_vm(config: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for vm in (config or {}).get("vms") or []:
        if isinstance(vm, dict) and vm.get("name") == name:
            return vm
    return None


def get_vm_by_vf_index(config: Optional[Dict[str, Any]], vf_index: int) -> Optional[Dict[str, Any]]:
    for vm in (config or {}).get("vms") or []:
        if isinstance(vm, dict) and vm.get("vf_index") == vf_index:
            return vm
    return None


def preserved_interfaces(config: Optional[Dict[str, Any]]) -> List[str]:
    """Every netdev labctl must never touch: management_interface + preserve_interfaces."""
    host = (config or {}).get("host") or {}
    out: List[str] = []
    mgmt = host.get("management_interface")
    if mgmt:
        out.append(mgmt)
    for extra in host.get("preserve_interfaces") or []:
        if extra and extra not in out:
            out.append(extra)
    return out


def validate_forwarding_section(config: Optional[Dict[str, Any]]) -> List[str]:
    """Validate config['forwarding'] (prompt I). Independent of validate_vm2vm_config
    so tools/forwarding_intent.py can validate just this section."""
    issues: List[str] = []
    fwd = (config or {}).get("forwarding")
    if not isinstance(fwd, dict):
        return ["forwarding section is required and must be a mapping"]

    if not fwd.get("name"):
        issues.append("forwarding.name is required")
    if fwd.get("type") not in ("l2-vm-to-vm", "l2-segment"):
        issues.append(
            "forwarding.type must be l2-vm-to-vm or l2-segment (got %r)" % fwd.get("type")
        )
    if fwd.get("mode") != "bridge":
        issues.append(
            "forwarding.mode must be 'bridge' (got %r) -- the only mode implemented today"
            % fwd.get("mode")
        )

    endpoints = fwd.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) < 2:
        issues.append("forwarding.endpoints must be a list of at least 2 entries")
        endpoints = endpoints if isinstance(endpoints, list) else []

    vms_by_name = {
        vm.get("name"): vm for vm in (config or {}).get("vms") or [] if isinstance(vm, dict)
    }
    for i, ep in enumerate(endpoints):
        if not isinstance(ep, dict):
            issues.append("forwarding.endpoints[%d] must be a mapping" % i)
            continue
        if not ep.get("name"):
            issues.append("forwarding.endpoints[%d].name is required" % i)
        vm_name = ep.get("vm")
        if not vm_name:
            issues.append("forwarding.endpoints[%d].vm is required" % i)
        elif vm_name not in vms_by_name:
            issues.append(
                "forwarding.endpoints[%d].vm=%r does not match any vms[].name" % (i, vm_name)
            )
        else:
            vm = vms_by_name[vm_name]
            ep_vf = ep.get("vf_index")
            vm_vf = vm.get("vf_index")
            if ep_vf is not None and vm_vf is not None and ep_vf != vm_vf:
                issues.append(
                    "forwarding.endpoints[%d].vf_index=%r does not match "
                    "vms[%r].vf_index=%r" % (i, ep_vf, vm_name, vm_vf)
                )

    policy = fwd.get("policy") or {}
    if policy.get("action") != "allow":
        issues.append("forwarding.policy.action must be 'allow' (got %r)" % policy.get("action"))
    if not isinstance(policy.get("bidirectional"), bool):
        issues.append("forwarding.policy.bidirectional must be a boolean")

    offload = fwd.get("offload") or {}
    for field_name in ("requested", "fallback_to_software", "require_confirmed_offload"):
        if not isinstance(offload.get(field_name), bool):
            issues.append("forwarding.offload.%s must be a boolean" % field_name)

    explicit_flows = fwd.get("explicit_flows") or {}
    if not isinstance(explicit_flows.get("enabled"), bool):
        issues.append("forwarding.explicit_flows.enabled must be a boolean")

    return issues


def build_forwarding_intent(config: Optional[Dict[str, Any]]) -> ForwardingIntent:
    """Build a ForwardingIntent from config['forwarding'] + ovs./dpu. context.

    Does not validate; call validate_forwarding_section() first. Missing
    vf_pci/representor fields stay None -- callers (labctl) are expected
    to fill them from a prior discover-representors result, not guess.
    """
    fwd = (config or {}).get("forwarding") or {}
    endpoints: List[ForwardingEndpoint] = []
    for ep in fwd.get("endpoints") or []:
        if not isinstance(ep, dict):
            continue
        endpoints.append(
            ForwardingEndpoint(
                name=ep.get("name") or "",
                vm=ep.get("vm"),
                vf_index=ep.get("vf_index"),
                vf_pci=ep.get("vf_pci"),
                representor=ep.get("representor"),
                mac=ep.get("mac"),
                ip_cidr=ep.get("ip_cidr"),
            )
        )

    policy_raw = fwd.get("policy") or {}
    policy = ForwardingPolicy(
        action=policy_raw.get("action") or "allow",
        bidirectional=bool(policy_raw.get("bidirectional", True)),
    )

    offload_raw = fwd.get("offload") or {}
    offload = ForwardingOffloadRequest(
        requested=bool(offload_raw.get("requested", False)),
        fallback_to_software=bool(offload_raw.get("fallback_to_software", True)),
        require_confirmed_offload=bool(offload_raw.get("require_confirmed_offload", False)),
    )

    ovs_cfg = (config or {}).get("ovs") or {}
    dpu_cfg = (config or {}).get("dpu") or {}
    details: Dict[str, Any] = {}
    if dpu_cfg.get("preferred_datapath"):
        details["datapath_type"] = dpu_cfg.get("preferred_datapath")

    return ForwardingIntent(
        name=fwd.get("name") or "",
        type=fwd.get("type") or "l2-vm-to-vm",
        mode=fwd.get("mode") or "bridge",
        endpoints=endpoints,
        policy=policy,
        offload=offload,
        explicit_flows=dict(fwd.get("explicit_flows") or {"enabled": False}),
        bridge=ovs_cfg.get("bridge"),
        details=details,
    )


def intel_backend_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """dpu.opi_endpoint/redfish_endpoint -> plugins.intel_ipu.IntelIpuBackend(config=...) kwargs.

    Reuses plugins.base.lab_config.intel_plugin_config() so both config
    schemas produce the same backend-facing shape.
    """
    dpu = (config or {}).get("dpu") or {}
    device_like = {
        "redfish_endpoint": dpu.get("redfish_endpoint"),
        "opi_endpoint": dpu.get("opi_endpoint"),
        "management_ip": dpu.get("management_ip"),
    }
    return _lab_config_intel_plugin_config(device_like)


def public_vm2vm_view(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Safe snippet for embedding in stage JSON/reports (no secrets)."""
    if not config:
        return {}
    host = config.get("host") or {}
    vendor = config.get("vendor") or {}
    dpu = config.get("dpu") or {}
    return {
        "schema": config.get("schema"),
        "host_name": host.get("name"),
        "management_interface": host.get("management_interface"),
        "vendor_type": normalize_vendor(vendor.get("type")),
        "pf_name": dpu.get("pf_name"),
        "pf_pci": dpu.get("pf_pci"),
        "num_vfs": dpu.get("num_vfs"),
        "preferred_datapath": dpu.get("preferred_datapath"),
        "dry_run_default": config_dry_run(config),
        "vm_names": [vm.get("name") for vm in config.get("vms") or [] if isinstance(vm, dict)],
    }
