"""VM XML + VFIO assignment planning for the VM-to-VM lab.

Pure helpers used by labctl create-vms / attach-vfs. Unknown IOMMU/VFIO
facts stay None. Duplicate VF PCI assignments are refused. Dry-run does
not call virsh.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Fixed namespace so the same VM name always derives the same UUID across
# separate labctl invocations (uuid.uuid5 is deterministic given the same
# namespace + name). This makes `virsh define` idempotent: re-running
# create-vms after a partial failure (e.g. define succeeded, start failed
# on an unrelated issue) redefines the same domain/UUID pair instead of
# colliding with the already-persisted domain under a freshly-generated
# random UUID ("operation failed: domain '<name>' already exists with
# uuid <other-uuid>").
_VM_UUID_NAMESPACE = uuid.UUID("6f5d6f1e-6f2e-4b8e-9c1a-7f6b1a0d5c00")


def deterministic_domain_uuid(vm_name: str) -> str:
    """Stable libvirt domain UUID for a given lab VM name.

    Not a secret and not security-sensitive -- purely so repeated
    `virsh define` calls for the same vms[].name are idempotent instead
    of colliding on a fresh random UUID each render.
    """
    return str(uuid.uuid5(_VM_UUID_NAMESPACE, "odse-vm2vm-lab:%s" % vm_name))

GUEST_SSH_REQUIRED = "GUEST_SSH_REQUIRED"

_PCI_RE = re.compile(
    r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-9a-fA-F])$"
)
_HOSTDEV_REGION = re.compile(
    r"[ \t]*<!-- ODSE_VF_HOSTDEV_BEGIN -->.*?<!-- ODSE_VF_HOSTDEV_END -->[ \t]*\n?",
    re.DOTALL,
)


def split_pci_address(pci: Optional[str]) -> Optional[Dict[str, str]]:
    if not pci:
        return None
    match = _PCI_RE.match(str(pci).strip())
    if not match:
        return None
    domain, bus, slot, function = match.groups()
    return {"domain": domain, "bus": bus, "slot": slot, "function": function}


def is_usable_pci(pci: Optional[str]) -> bool:
    if not pci:
        return False
    text = str(pci).strip()
    if not text or text.startswith("<"):
        return False
    return split_pci_address(text) is not None


def resolve_vf_pci(
    vm: Mapping[str, Any],
    mapping: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    raw = vm.get("vf_pci")
    if is_usable_pci(raw if isinstance(raw, str) else None):
        return str(raw).strip()
    vf_index = vm.get("vf_index")
    if mapping and isinstance(mapping, dict) and vf_index is not None:
        for entry in mapping.get("mappings") or mapping.get("vfs") or []:
            if not isinstance(entry, dict) or entry.get("vf_index") != vf_index:
                continue
            for key in ("vf_pci", "vf_pci_address"):
                cand = entry.get(key)
                if is_usable_pci(cand if isinstance(cand, str) else None):
                    return str(cand).strip()
    return None


def assignment_conflicts(vms: Sequence[Mapping[str, Any]]) -> List[str]:
    """Refuse duplicate VM names and duplicate VF PCI assignments."""
    errors: List[str] = []
    names: Dict[str, int] = {}
    pcis: Dict[str, str] = {}
    for vm in vms:
        name = vm.get("name")
        if not name:
            errors.append("VM entry missing name")
            continue
        if name in names:
            errors.append("duplicate VM name %r; domains must be unique" % name)
        names[name] = names.get(name, 0) + 1
        pci = vm.get("vf_pci")
        if is_usable_pci(pci if isinstance(pci, str) else None):
            key = str(pci).strip().lower()
            if key in pcis:
                errors.append(
                    "VF PCI %s already assigned to %s; not assigning to %s"
                    % (pci, pcis[key], name)
                )
            else:
                pcis[key] = str(name)
    return errors


def _iommu_groups_dir_has_entries(path: str) -> bool:
    """True only if `path` exists and contains at least one IOMMU group.

    A directory that exists but is empty is not evidence of a working
    IOMMU: some kernels create the generic /sys/kernel/iommu_groups stub
    even when the intel-iommu/amd-iommu driver never actually attached a
    device to a group (e.g. VT-d/AMD-Vi disabled in firmware, or the
    DMAR/IVRS ACPI table is missing, despite intel_iommu=on/amd_iommu=on
    being present on the kernel command line). `os.path.isdir()` alone
    cannot tell these two cases apart; listing the directory can.
    """
    try:
        return len(os.listdir(path)) > 0
    except OSError:
        return False


def iommu_vfio_readiness(
    *,
    samples: Optional[Mapping[str, str]] = None,
    sys_root: str = "/sys",
) -> Dict[str, Any]:
    """Observe IOMMU groups and vfio-pci. Never claims ready without evidence.

    If the sysfs tree cannot be inspected (missing sys_root, container
    without /sys), facts stay None / UNKNOWN. Absence of a fake sys_root
    is not proof that IOMMU is disabled.
    """
    samples = dict(samples or {})
    sys_present = os.path.isdir(sys_root)
    inspect_live_proc = sys_present and os.path.abspath(sys_root) == os.path.abspath("/sys")

    cmdline = samples.get("cmdline") if "cmdline" in samples else None
    if "cmdline" not in samples:
        if inspect_live_proc:
            cmdline_path = Path("/proc/cmdline")
            cmdline = cmdline_path.read_text(encoding="utf-8") if cmdline_path.exists() else None
        else:
            cmdline = None

    groups_dir = samples.get("iommu_groups_dir") or os.path.join(sys_root, "kernel", "iommu_groups")
    vfio_pci = samples.get("vfio_pci_module")
    sys_vfio_inspected = False
    if vfio_pci is None and sys_present:
        mod = Path(sys_root) / "module" / "vfio_pci"
        vfio_pci = "present" if mod.exists() else "absent"
        sys_vfio_inspected = True

    iommu_on_cmdline = None
    if cmdline is not None:
        blob = cmdline.lower()
        iommu_on_cmdline = ("intel_iommu=on" in blob) or ("amd_iommu=on" in blob) or ("iommu=pt" in blob)

    groups_exist = None
    if samples.get("iommu_groups_dir") is not None:
        # Inspect the caller-provided directory for real (fixtures use a
        # temp dir with fake group subdirs); do not trust the key's mere
        # presence as proof, same reasoning as the live sys_root branch.
        groups_exist = _iommu_groups_dir_has_entries(groups_dir)
    elif sys_present:
        groups_exist = _iommu_groups_dir_has_entries(groups_dir)

    vfio_present = None
    if vfio_pci == "present":
        vfio_present = True
    elif vfio_pci == "absent":
        vfio_present = False

    ready = None
    status = "UNKNOWN"
    if iommu_on_cmdline is True and groups_exist is True and vfio_present is True:
        ready = True
        status = "READY"
    elif (
        iommu_on_cmdline is False
        or groups_exist is False
        or vfio_present is False
    ) and (cmdline is not None or groups_exist is not None or sys_vfio_inspected or "vfio_pci_module" in samples):
        # Negative only when we actually inspected a source (samples or real sysfs).
        ready = False
        status = "NOT_READY"

    return {
        "iommu_on_cmdline": iommu_on_cmdline,
        "iommu_groups_present": groups_exist,
        "vfio_pci_present": vfio_present,
        "ready": ready,
        "status": status,
        "message": (
            "IOMMU/VFIO confirmed"
            if ready is True
            else (
                "IOMMU/VFIO not confirmed on this host; dry-run still ok"
                if ready is None
                else "IOMMU/VFIO evidence is negative"
            )
        ),
    }


def render_libvirt_xml(
    template_text: str,
    vm: Mapping[str, Any],
    *,
    include_vf_hostdev: bool = True,
) -> str:
    vf_pci = vm.get("vf_pci") or ""
    pci_fields = split_pci_address(vf_pci) if vf_pci else None
    if vf_pci and include_vf_hostdev and pci_fields is None:
        raise ValueError(
            "vms[%r].vf_pci=%r is not a valid 0000:bb:dd.f PCI address"
            % (vm.get("name"), vf_pci)
        )
    pci_fields = pci_fields or {
        "domain": "@VF_DOMAIN@",
        "bus": "@VF_BUS@",
        "slot": "@VF_SLOT@",
        "function": "@VF_FUNCTION@",
    }
    memory_mb = vm.get("memory_mb")
    vm_name = vm.get("name") or ""
    vm_uuid = vm.get("uuid") or (deterministic_domain_uuid(vm_name) if vm_name else "@VM_UUID@")
    replacements = {
        "@VM_NAME@": vm_name,
        "@VM_UUID@": vm_uuid,
        "@MEMORY_KIB@": str(int(memory_mb) * 1024) if memory_mb else "@MEMORY_KIB@",
        "@VCPUS@": str(vm.get("vcpus") or "") or "@VCPUS@",
        "@DISK_IMAGE_PATH@": vm.get("image") or "",
        "@VF_PCI_ADDRESS@": vf_pci or "@VF_PCI_ADDRESS@",
        "@VF_DOMAIN@": pci_fields["domain"],
        "@VF_BUS@": pci_fields["bus"],
        "@VF_SLOT@": pci_fields["slot"],
        "@VF_FUNCTION@": pci_fields["function"],
        "@MAC_ADDRESS@": vm.get("mac") or "@MAC_ADDRESS@",
    }
    text = template_text
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, str(value))
    if not include_vf_hostdev:
        text = _HOSTDEV_REGION.sub("", text)
    return text


def hostdev_xml_snippet(vf_pci: str, mac: Optional[str] = None) -> str:
    fields = split_pci_address(vf_pci)
    if fields is None:
        raise ValueError("invalid VF PCI address %r" % vf_pci)
    mac_line = ""
    if mac:
        mac_line = "      <mac address='%s'/>\n" % mac
    return (
        "<interface type='hostdev' managed='yes'>\n"
        "  <source>\n"
        "    <address type='pci' domain='0x%(domain)s' bus='0x%(bus)s' "
        "slot='0x%(slot)s' function='0x%(function)s'/>\n"
        "  </source>\n"
        "%(mac)s"
        "</interface>\n"
        % {
            "domain": fields["domain"],
            "bus": fields["bus"],
            "slot": fields["slot"],
            "function": fields["function"],
            "mac": mac_line,
        }
    )


def wait_for_boot_requested(config: Optional[Mapping[str, Any]]) -> bool:
    block = (config or {}).get("create_vms") if isinstance(config, dict) else None
    if isinstance(block, dict) and block.get("wait_for_boot") is True:
        return True
    return False


def start_vms_requested(config: Optional[Mapping[str, Any]]) -> bool:
    block = (config or {}).get("create_vms") if isinstance(config, dict) else None
    if isinstance(block, dict) and "start" in block:
        return bool(block.get("start"))
    return True
