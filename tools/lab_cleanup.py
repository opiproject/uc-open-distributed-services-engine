#!/usr/bin/env python3
"""lab_cleanup.py — teardown for the VM-to-VM single-DPU lab.

Companion to tools/labctl.py (the `cleanup` stage delegates here once this
file exists). Standalone and runnable on its own:

Usage:
    python3 tools/lab_cleanup.py --config config/lab-nvidia-vm2vm.yaml --dry-run
    python3 tools/lab_cleanup.py --config config/lab-nvidia-vm2vm.yaml --apply

Default is dry-run (same gating as labctl.py: --dry-run always wins;
--apply only takes effect if the loaded config also sets
safety.dry_run_default: false). NEVER touches host.management_interface
or host.preserve_interfaces (plugins.base.vm2vm_config.preserved_interfaces),
regardless of flags. See docs/operations/cleanup-and-rollback.md for what
this does and does not guarantee, and how to recover manually if a step
fails partway through.

Stages (always run in this order; several are read-only/best-effort and
never fail the whole run):

 1. stop_traffic_tools        SSH into each VM and stop iperf3 (best-effort).
 2. stop_vms                  `virsh destroy`; `virsh undefine` only with --undefine-vms.
 3. detach_vfs                `virsh detach-device` the VF hostdev interface, if vf_pci is known.
 4. restore_vf_driver         Rebind each VF from vfio-pci to its bus driver, if still bound to vfio-pci.
 5. remove_ovs_ports          `ovs-vsctl del-port` for every representor/static port this lab configured.
 6. delete_ovs_bridge         `ovs-vsctl del-br` for ovs.bridge.
 7. reset_sriov_numvfs        Write 0 to sriov_numvfs -- ONLY if a matching prepare_vfs.json state file exists.
 8. restore_switchdev_legacy  Opt-in (--restore-switchdev-legacy): eswitch mode -> legacy.
 9. restart_ovs               Opt-in (--restart-ovs): restart the OVS service.
10. collect_final_state       Read-only dpu_inventory.py + `ovs-vsctl show` after the above.

Evidence (the full JSON report, always) is written to
tests/results/<vendor>/<run-id>/cleanup/cleanup-summary.json regardless of
--dry-run/--apply, and the same report is also the tool's stdout (so
tools/labctl.py's `cleanup` stage can parse it, matching the convention
used by tools/topology-discovery/vf_to_representor_map.py and
tools/offload-checker/ovs_offload_checker.py: JSON on stdout by default,
no mixed human text).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.commands import command_exists, run  # noqa: E402
from plugins.base.lab_config import normalize_vendor  # noqa: E402
from plugins.base.remote import RemoteCommandRunner, is_dpu_mode, ssh_params  # noqa: E402
from plugins.base.vm2vm_config import (  # noqa: E402
    config_dry_run,
    load_vm2vm_config,
    preserved_interfaces,
    validate_vm2vm_config,
)

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import lab_state  # noqa: E402

LOG = logging.getLogger("lab_cleanup")

SCHEMA_VERSION = "odse-lab-cleanup/v1alpha1"
RESULTS_ROOT = ROOT / "tests" / "results"

CLEANUP_STAGES = (
    "stop_traffic_tools",
    "stop_vms",
    "detach_vfs",
    "restore_vf_driver",
    "remove_ovs_ports",
    "delete_ovs_bridge",
    "reset_sriov_numvfs",
    "restore_switchdev_legacy",
    "restart_ovs",
    "collect_final_state",
)


# ---------------------------------------------------------------------------
# Run directory helpers (same shape as tools/labctl.py:
# tests/results/<vendor>/<run-id>/<stage>.json, plus a cleanup/ subdirectory)
# ---------------------------------------------------------------------------

def _vendor_results_dir(vendor_type: str) -> Path:
    return RESULTS_ROOT / vendor_type


def resolve_run_id(vendor_type: str, explicit: Optional[str]) -> str:
    """Explicit --run-id wins; otherwise reuse the vendor's latest labctl
    run (so cleanup evidence lands next to the run it is tearing down),
    else start a fresh timestamp.
    """
    if explicit:
        return explicit
    latest_file = _vendor_results_dir(vendor_type) / ".latest"
    if latest_file.exists():
        existing = latest_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_dir_for(vendor_type: str, run_id: str) -> Path:
    return _vendor_results_dir(vendor_type) / run_id


def load_stage_json(run_dir: Path, stage: str) -> Optional[Dict[str, Any]]:
    """Read a prior labctl stage's saved JSON. Missing -> None (never invented)."""
    data = lab_state.load_stage(run_dir, stage)
    if data is not None:
        return data
    path = run_dir / ("%s.json" % stage.replace("-", "_"))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# dry-run / apply gating (identical semantics to tools/labctl.py)
# ---------------------------------------------------------------------------

def _dry_run(args: argparse.Namespace, config: Optional[Dict[str, Any]]) -> bool:
    if getattr(args, "dry_run", False):
        return True
    if config_dry_run(config):
        return True
    if getattr(args, "apply", False):
        return False
    return True


def _apply_allowed(args: argparse.Namespace, config: Optional[Dict[str, Any]]) -> bool:
    return (not _dry_run(args, config)) and bool(getattr(args, "apply", False))


class CleanupContext:
    """Bundled state passed to every cleanup stage function."""

    def __init__(
        self,
        args: argparse.Namespace,
        config: Dict[str, Any],
        vendor_type: str,
        run_dir: Path,
        run_id: str,
    ) -> None:
        self.args = args
        self.config = config
        self.vendor_type = vendor_type
        self.run_dir = run_dir
        self.run_id = run_id
        self.dry_run = _dry_run(args, config)
        self.apply_allowed = _apply_allowed(args, config)
        self.preserved = preserved_interfaces(config)

    def load_prior(self, stage: str) -> Optional[Dict[str, Any]]:
        return load_stage_json(self.run_dir, stage)

    @property
    def dpu_mode(self) -> bool:
        return is_dpu_mode(self.config)

    def ovs_run(self, argv: List[str], *, reason: str, timeout: float = 30.0):
        """Run ovs-* on the DPU in DPU mode; otherwise local. Dry-run default."""
        if not self.dpu_mode:
            return run(argv, dry_run=self.dry_run, timeout=timeout, reason=reason)
        params = ssh_params(self.config)
        host, user = params.get("host"), params.get("user")
        if not host or not user:
            return run(argv, dry_run=True, timeout=timeout, reason=reason + " (no DPU SSH; planned only)")
        return RemoteCommandRunner().run_ssh(
            host,
            user,
            argv,
            key_path=params.get("key_path"),
            port=int(params.get("port") or 22),
            dry_run=self.dry_run,
            timeout=timeout,
            reason=reason,
            sudo=bool(params.get("sudo")),
        )


def _lab_owned_bridge(ctx: CleanupContext) -> bool:
    """True only when this run created the bridge or ovs.lab_owned is explicit."""
    ovs_cfg = ctx.config.get("ovs") or {}
    if ovs_cfg.get("lab_owned") is True:
        return True
    prepare = ctx.load_prior("prepare-ovs") or {}
    if prepare.get("lab_owned") is True:
        return True
    if prepare.get("refused"):
        return False
    if prepare.get("stage") == "prepare-ovs" and prepare.get("bridge"):
        return True
    forwarding = ctx.load_prior("configure-forwarding") or {}
    result = forwarding.get("result") or {}
    details = result.get("details") if isinstance(result, dict) else {}
    if isinstance(details, dict) and details.get("bridge") and result.get("configured_ports"):
        return True
    return False


def _split_pci_address(pci: str) -> Optional[Dict[str, str]]:
    """'0000:03:00.2' -> {'domain': '0000', 'bus': '03', 'slot': '00', 'function': '2'}.

    Returns None (never guesses) if pci does not match the expected shape.
    Duplicated from tools/labctl.py rather than imported so this tool has
    no dependency on labctl.py and can run standalone.
    """
    match = re.match(
        r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-9a-fA-F])$",
        (pci or "").strip(),
    )
    if not match:
        return None
    domain, bus, slot, function = match.groups()
    return {"domain": domain, "bus": bus, "slot": slot, "function": function}


def _vm_management_host(vm: Dict[str, Any]) -> Optional[str]:
    """Same resolution order as tools/labctl.py's run-traffic/configure-vm-ips:
    vms[].management_ip, else a read-only `virsh domifaddr` lookup. Never
    falls back to vms[].ip_cidr (the VF-under-test address).
    """
    explicit = vm.get("management_ip")
    if explicit:
        return explicit
    name = vm.get("name")
    if not name or not command_exists("virsh"):
        return None
    cmd = run(
        ["virsh", "domifaddr", name],
        dry_run=False,
        reason="lab_cleanup: virsh domifaddr %s" % name,
    )
    if not cmd.executed or cmd.returncode != 0 or not cmd.stdout:
        return None
    for line in cmd.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "ipv4":
            return parts[3].split("/", 1)[0]
    return None


# ---------------------------------------------------------------------------
# Cleanup stages
# ---------------------------------------------------------------------------

def stage_stop_traffic_tools(ctx: CleanupContext) -> Dict[str, Any]:
    vms = ctx.config.get("vms") or []
    results: List[Dict[str, Any]] = []
    for vm in vms:
        name = vm.get("name")
        ssh_user = vm.get("ssh_user")
        host = _vm_management_host(vm)
        entry: Dict[str, Any] = {"vm": name, "management_host": host}
        if not host or not ssh_user:
            entry["skipped"] = True
            entry["message"] = (
                "management host unreachable (read-only virsh domifaddr "
                "lookup found nothing and management_ip is not set); "
                "nothing to stop via SSH"
            )
            results.append(entry)
            continue
        cmd = run(
            ["ssh", "%s@%s" % (ssh_user, host), "pkill", "-f", "iperf3", "||", "true"],
            dry_run=ctx.dry_run,
            reason="lab_cleanup stop-traffic-tools: pkill iperf3 on %s" % name,
        )
        entry["command"] = _jsonable(cmd)
        results.append(entry)
    return {"stage": "stop_traffic_tools", "vms": results, "dry_run": ctx.dry_run}


def stage_stop_vms(ctx: CleanupContext) -> Dict[str, Any]:
    vms = ctx.config.get("vms") or []
    results: List[Dict[str, Any]] = []
    undefine_vms = bool(getattr(ctx.args, "undefine_vms", False))
    for vm in vms:
        name = vm.get("name") or ""
        entry: Dict[str, Any] = {"vm": name}
        destroy_cmd = run(
            ["virsh", "destroy", name],
            dry_run=ctx.dry_run,
            reason="lab_cleanup stop-vms: virsh destroy %s" % name,
        )
        entry["destroy"] = _jsonable(destroy_cmd)
        if undefine_vms:
            undefine_cmd = run(
                ["virsh", "undefine", name],
                dry_run=ctx.dry_run,
                reason="lab_cleanup stop-vms: virsh undefine %s" % name,
            )
            entry["undefine"] = _jsonable(undefine_cmd)
        else:
            entry["undefine"] = {
                "skipped": True,
                "message": "pass --undefine-vms to also remove the libvirt domain definition",
            }
        results.append(entry)
    return {"stage": "stop_vms", "vms": results, "dry_run": ctx.dry_run}


def _vf_hostdev_detach_xml(vf_pci: str) -> Optional[str]:
    fields = _split_pci_address(vf_pci)
    if not fields:
        return None
    return (
        "<interface type='hostdev' managed='yes'>\n"
        "  <source>\n"
        "    <address type='pci' domain='0x%(domain)s' bus='0x%(bus)s' "
        "slot='0x%(slot)s' function='0x%(function)s'/>\n"
        "  </source>\n"
        "</interface>\n" % fields
    )


def stage_detach_vfs(ctx: CleanupContext) -> Dict[str, Any]:
    vms = ctx.config.get("vms") or []
    results: List[Dict[str, Any]] = []
    for vm in vms:
        name = vm.get("name") or ""
        vf_pci = vm.get("vf_pci")
        entry: Dict[str, Any] = {"vm": name, "vf_pci": vf_pci}
        if not vf_pci:
            entry["skipped"] = True
            entry["message"] = "vf_pci unknown; nothing to detach"
            results.append(entry)
            continue
        xml = _vf_hostdev_detach_xml(vf_pci)
        if xml is None:
            entry["skipped"] = True
            entry["message"] = "vf_pci %r is not a valid 0000:bb:dd.f PCI address" % vf_pci
            results.append(entry)
            continue
        cmd = run(
            ["virsh", "detach-device", name, "/dev/stdin", "--config", "--live"],
            dry_run=ctx.dry_run,
            input_text=xml,
            reason="lab_cleanup detach-vfs: virsh detach-device %s" % name,
        )
        entry["command"] = _jsonable(cmd)
        results.append(entry)
    return {"stage": "detach_vfs", "vms": results, "dry_run": ctx.dry_run}


def _current_pci_driver(pci: str) -> Optional[str]:
    link = Path("/sys/bus/pci/devices") / pci / "driver"
    try:
        if link.is_symlink():
            return os.path.basename(os.readlink(str(link)))
    except OSError:
        return None
    return None


def stage_restore_vf_driver(ctx: CleanupContext) -> Dict[str, Any]:
    """Best-effort: libvirt's managed='yes' hostdev binding (used by
    qemu/libvirt-xml/vf-passthrough.xml.template and vm-vfio-template.xml)
    already rebinds a VF to its original driver on detach/shutdown. This
    stage only acts when the VF is still bound to vfio-pci afterwards
    (e.g. managed='no', or the guest was killed instead of cleanly
    stopped), and reports the current driver either way as evidence.
    """
    vms = ctx.config.get("vms") or []
    results: List[Dict[str, Any]] = []
    for vm in vms:
        vf_pci = vm.get("vf_pci")
        entry: Dict[str, Any] = {"vm": vm.get("name"), "vf_pci": vf_pci}
        if not vf_pci:
            entry["skipped"] = True
            entry["message"] = "vf_pci unknown; nothing to restore"
            results.append(entry)
            continue
        current = _current_pci_driver(vf_pci)
        entry["current_driver"] = current
        if current != "vfio-pci":
            entry["skipped"] = True
            entry["message"] = (
                "current driver is %r, not vfio-pci; no rebind needed" % current
            )
            results.append(entry)
            continue
        script = (
            "echo %(pci)s > /sys/bus/pci/devices/%(pci)s/driver/unbind && "
            "echo %(pci)s > /sys/bus/pci/drivers_probe" % {"pci": vf_pci}
        )
        cmd = run(
            script,
            dry_run=ctx.dry_run,
            shell=True,
            reason="lab_cleanup restore-vf-driver: rebind %s from vfio-pci" % vf_pci,
        )
        entry["command"] = _jsonable(cmd)
        results.append(entry)
    return {"stage": "restore_vf_driver", "vms": results, "dry_run": ctx.dry_run}


def _ports_to_remove(ctx: CleanupContext) -> List[str]:
    """Every representor/static port this lab is known to have added to
    the bridge, from the same run's prior stage evidence -- never
    guessed from netdev naming conventions. Excludes anything in
    ctx.preserved (host.management_interface / host.preserve_interfaces).
    """
    ports: List[str] = []
    seen = set()

    def _add(name: Optional[str]) -> None:
        if name and name not in seen and name not in ctx.preserved:
            seen.add(name)
            ports.append(name)

    forwarding = ctx.load_prior("configure-forwarding")
    if forwarding:
        for port in (forwarding.get("result") or {}).get("configured_ports") or []:
            _add(port)
    mapping = ctx.load_prior("discover-representors")
    if mapping:
        vf_map = mapping.get("vf_to_representor_map") or {}
        for entry in vf_map.get("mappings") or vf_map.get("vfs") or []:
            if isinstance(entry, dict):
                _add(entry.get("representor_netdev"))
    ovs_cfg = ctx.config.get("ovs") or {}
    for port in ovs_cfg.get("ports") or []:
        _add(port)
    return ports


def _is_marvell(ctx: CleanupContext) -> bool:
    return ctx.vendor_type in ("marvell-octeon", "marvell") or normalize_vendor(
        (ctx.config.get("vendor") or {}).get("type")
    ) == "marvell-octeon"


def _marvell_dao_cleanup_stage(ctx: CleanupContext, stage: str) -> Dict[str, Any]:
    """Skip host ovs-vsctl; plan DAO cleanup only (lab-owned / tracked bindings)."""
    from plugins.marvell_octeon.cleanup import plan_dao_cleanup

    state: Dict[str, Any] = {}
    for name in ("discover-representors", "discover_representors", "prepare-ovs", "prepare_ovs"):
        prior = ctx.load_prior(name)
        if prior:
            state[name] = prior
    result = plan_dao_cleanup(
        ctx.config,
        run_state=state,
        apply=ctx.apply_allowed,
        dry_run=ctx.dry_run,
    )
    return {
        "stage": stage,
        "host_ovs_untouched": True,
        "marvell_dao_cleanup": _jsonable(result),
        "message": result.message,
        "dry_run": ctx.dry_run,
    }


def stage_remove_ovs_ports(ctx: CleanupContext) -> Dict[str, Any]:
    if _is_marvell(ctx):
        return _marvell_dao_cleanup_stage(ctx, "remove_ovs_ports")
    ovs_cfg = ctx.config.get("ovs") or {}
    bridge = ovs_cfg.get("bridge")
    if not bridge:
        return {
            "stage": "remove_ovs_ports",
            "skipped": True,
            "message": "ovs.bridge not set",
            "dry_run": ctx.dry_run,
        }
    ports = _ports_to_remove(ctx)
    results: List[Dict[str, Any]] = []
    forwarding = ctx.load_prior("configure-forwarding") or {}
    flows = ((forwarding.get("result") or {}).get("configured_flows")) or []
    if flows and _lab_owned_bridge(ctx):
        flow_cmd = ctx.ovs_run(
            ["ovs-ofctl", "del-flows", bridge],
            reason="lab_cleanup remove-ovs-ports: del-flows on lab-owned %s" % bridge,
        )
        results.append({"flows": flows, "command": _jsonable(flow_cmd)})
    if not ports and not results:
        return {
            "stage": "remove_ovs_ports",
            "skipped": True,
            "message": "no lab-owned representor/static ports in prior run state; refusing to guess",
            "location": "dpu" if ctx.dpu_mode else "host",
            "dry_run": ctx.dry_run,
        }
    for port in ports:
        cmd = ctx.ovs_run(
            ["ovs-vsctl", "--if-exists", "del-port", bridge, port],
            reason="lab_cleanup remove-ovs-ports: del-port %s %s" % (bridge, port),
        )
        results.append({"port": port, "command": _jsonable(cmd)})
    return {
        "stage": "remove_ovs_ports",
        "bridge": bridge,
        "ports": ports,
        "results": results,
        "location": "dpu" if ctx.dpu_mode else "host",
        "host_ovs_untouched": ctx.dpu_mode,
        "dry_run": ctx.dry_run,
    }


def stage_delete_ovs_bridge(ctx: CleanupContext) -> Dict[str, Any]:
    if _is_marvell(ctx):
        return _marvell_dao_cleanup_stage(ctx, "delete_ovs_bridge")
    ovs_cfg = ctx.config.get("ovs") or {}
    bridge = ovs_cfg.get("bridge")
    if not bridge:
        return {
            "stage": "delete_ovs_bridge",
            "skipped": True,
            "message": "ovs.bridge not set",
            "dry_run": ctx.dry_run,
        }
    if bridge in ctx.preserved:
        return {
            "stage": "delete_ovs_bridge",
            "skipped": True,
            "message": "ovs.bridge %r matches a preserved interface; refusing" % bridge,
            "dry_run": ctx.dry_run,
        }
    if not _lab_owned_bridge(ctx):
        return {
            "stage": "delete_ovs_bridge",
            "skipped": True,
            "refused": True,
            "error": "UNKNOWN_BRIDGE_REFUSED",
            "bridge": bridge,
            "message": (
                "will not delete bridge %r: not created by this run and "
                "ovs.lab_owned is not true" % bridge
            ),
            "location": "dpu" if ctx.dpu_mode else "host",
            "dry_run": ctx.dry_run,
        }
    cmd = ctx.ovs_run(
        ["ovs-vsctl", "--if-exists", "del-br", bridge],
        reason="lab_cleanup delete-ovs-bridge: del-br %s" % bridge,
    )
    return {
        "stage": "delete_ovs_bridge",
        "bridge": bridge,
        "command": _jsonable(cmd),
        "lab_owned": True,
        "location": "dpu" if ctx.dpu_mode else "host",
        "host_ovs_untouched": ctx.dpu_mode,
        "dry_run": ctx.dry_run,
    }


def stage_reset_sriov_numvfs(ctx: CleanupContext) -> Dict[str, Any]:
    """Safety rule: never reset a PF unless (a) it is the PF named in the
    current --config, and (b) a prior prepare_vfs.json state file exists
    for this run recording that this same PF was configured here. This
    is a floor, not a full guarantee -- see
    docs/operations/cleanup-and-rollback.md for the exact limitation
    (vendor enablement backends are stubs today and may not populate
    every field prepare-vfs records).
    """
    dpu = ctx.config.get("dpu") or {}
    pf_name = dpu.get("pf_name")
    pf_pci = dpu.get("pf_pci")
    if not pf_name:
        return {
            "stage": "reset_sriov_numvfs",
            "skipped": True,
            "message": "dpu.pf_name not set",
            "dry_run": ctx.dry_run,
        }
    if pf_name in ctx.preserved:
        return {
            "stage": "reset_sriov_numvfs",
            "skipped": True,
            "message": "dpu.pf_name %r matches a preserved interface; refusing" % pf_name,
            "dry_run": ctx.dry_run,
        }
    prior = ctx.load_prior("prepare-vfs")
    if ctx.dpu_mode and not ((ctx.config.get("cleanup") or {}).get("reset_host_vfs") is True):
        return {
            "stage": "reset_sriov_numvfs",
            "skipped": True,
            "message": (
                "NVIDIA DPU mode: host sriov_numvfs reset skipped unless "
                "cleanup.reset_host_vfs is true"
            ),
            "location": "dpu",
            "dry_run": ctx.dry_run,
        }
    if not prior:
        return {
            "stage": "reset_sriov_numvfs",
            "skipped": True,
            "message": (
                "no prior prepare_vfs.json state file found in run %r; "
                "refusing to reset sriov_numvfs without evidence this run "
                "configured this PF" % ctx.run_id
            ),
            "dry_run": ctx.dry_run,
        }
    recorded_pf_pci = prior.get("pf_pci")
    if recorded_pf_pci and pf_pci and recorded_pf_pci != pf_pci:
        return {
            "stage": "reset_sriov_numvfs",
            "skipped": True,
            "message": (
                "prior prepare_vfs.json recorded pf_pci=%r but --config now "
                "has dpu.pf_pci=%r; refusing to reset a PF that does not "
                "match the state file" % (recorded_pf_pci, pf_pci)
            ),
            "dry_run": ctx.dry_run,
        }
    script = "echo 0 > /sys/class/net/%s/device/sriov_numvfs" % pf_name
    cmd = run(
        script,
        dry_run=ctx.dry_run,
        shell=True,
        reason="lab_cleanup reset-sriov-numvfs: %s -> 0" % pf_name,
    )
    return {
        "stage": "reset_sriov_numvfs",
        "pf_name": pf_name,
        "pf_pci": pf_pci,
        "command": _jsonable(cmd),
        "dry_run": ctx.dry_run,
    }


def stage_restore_switchdev_legacy(ctx: CleanupContext) -> Dict[str, Any]:
    if ctx.dpu_mode:
        return {
            "stage": "restore_switchdev_legacy",
            "skipped": True,
            "message": "NVIDIA DPU mode: host eswitch is not the representor owner; not setting legacy",
            "location": "dpu",
        }
    if not getattr(ctx.args, "restore_switchdev_legacy", False):
        return {
            "stage": "restore_switchdev_legacy",
            "skipped": True,
            "message": "pass --restore-switchdev-legacy to opt in",
        }
    dpu = ctx.config.get("dpu") or {}
    if not dpu.get("enable_switchdev"):
        return {
            "stage": "restore_switchdev_legacy",
            "skipped": True,
            "message": "dpu.enable_switchdev is false; switchdev was not requested by this config",
        }
    pf_pci = dpu.get("pf_pci")
    if not pf_pci:
        return {
            "stage": "restore_switchdev_legacy",
            "skipped": True,
            "message": "dpu.pf_pci not set",
        }
    cmd = run(
        ["devlink", "dev", "eswitch", "set", "pci/%s" % pf_pci, "mode", "legacy"],
        dry_run=ctx.dry_run,
        reason="lab_cleanup restore-switchdev-legacy: devlink eswitch legacy %s" % pf_pci,
    )
    return {
        "stage": "restore_switchdev_legacy",
        "pf_pci": pf_pci,
        "command": _jsonable(cmd),
        "dry_run": ctx.dry_run,
    }


def stage_restart_ovs(ctx: CleanupContext) -> Dict[str, Any]:
    if ctx.dpu_mode:
        return {
            "stage": "restart_ovs",
            "skipped": True,
            "message": "NVIDIA DPU mode: will not restart host OVS (host is not forwarding owner)",
            "location": "dpu",
        }
    if not getattr(ctx.args, "restart_ovs", False):
        return {
            "stage": "restart_ovs",
            "skipped": True,
            "message": "pass --restart-ovs to opt in",
        }
    attempts: List[Dict[str, Any]] = []
    for unit in ("openvswitch-switch", "openvswitch"):
        cmd = run(
            ["systemctl", "restart", unit],
            dry_run=ctx.dry_run,
            reason="lab_cleanup restart-ovs: systemctl restart %s" % unit,
        )
        attempts.append({"unit": unit, "command": _jsonable(cmd)})
        if not ctx.dry_run and cmd.executed and cmd.returncode == 0:
            break
    return {"stage": "restart_ovs", "attempts": attempts, "dry_run": ctx.dry_run}


def stage_collect_final_state(ctx: CleanupContext) -> Dict[str, Any]:
    """Always executed (read-only), regardless of --dry-run/--apply, same
    as tools/labctl.py's _run_tool convention for observation-only tools.
    """
    inventory_cmd = run(
        [sys.executable, str(ROOT / "tools" / "inventory" / "dpu_inventory.py")],
        dry_run=False,
        timeout=30.0,
        reason="lab_cleanup collect-final-state: dpu_inventory.py",
    )
    inventory = None
    if inventory_cmd.executed and inventory_cmd.returncode in (0, None) and inventory_cmd.stdout:
        try:
            inventory = json.loads(inventory_cmd.stdout)
        except ValueError:
            inventory = None
    vsctl_cmd = ctx.ovs_run(
        ["ovs-vsctl", "show"],
        reason="lab_cleanup collect-final-state: ovs-vsctl show",
        timeout=15.0,
    )
    # Observation-only: in DPU mode the command above is SSH (still dry-run
    # unless --apply). Host ovs-vsctl is not treated as forwarding evidence.
    return {
        "stage": "collect_final_state",
        "dpu_inventory": inventory,
        "dpu_inventory_error": None if inventory is not None else (inventory_cmd.stderr or inventory_cmd.reason),
        "ovs_vsctl_show": _jsonable(vsctl_cmd),
        "location": "dpu" if ctx.dpu_mode else "host",
        "host_ovs_untouched": ctx.dpu_mode,
    }


STAGE_FUNCS = {
    "stop_traffic_tools": stage_stop_traffic_tools,
    "stop_vms": stage_stop_vms,
    "detach_vfs": stage_detach_vfs,
    "restore_vf_driver": stage_restore_vf_driver,
    "remove_ovs_ports": stage_remove_ovs_ports,
    "delete_ovs_bridge": stage_delete_ovs_bridge,
    "reset_sriov_numvfs": stage_reset_sriov_numvfs,
    "restore_switchdev_legacy": stage_restore_switchdev_legacy,
    "restart_ovs": stage_restart_ovs,
    "collect_final_state": stage_collect_final_state,
}


# ---------------------------------------------------------------------------
# Rollback risks (Prompt F: "Print rollback risks before applying")
# ---------------------------------------------------------------------------

def rollback_risks(ctx: CleanupContext) -> List[str]:
    risks = [
        "Stopping/destroying VMs interrupts any in-progress SSH sessions or traffic tests against them.",
        "Removing OVS ports/bridge is not reversible from this tool; re-run "
        "'labctl vm2vm prepare-ovs', 'discover-representors', and "
        "'configure-forwarding' to rebuild them.",
    ]
    if getattr(ctx.args, "undefine_vms", False):
        risks.append(
            "--undefine-vms removes the libvirt domain definition; the "
            "qcow2 disk image itself is not deleted, but you must re-run "
            "'labctl vm2vm create-vms' (or virsh define) to recreate the domain."
        )
    dpu = ctx.config.get("dpu") or {}
    if dpu.get("pf_name"):
        risks.append(
            "Resetting sriov_numvfs=0 on %s destroys all VFs on that PF, "
            "including their PCI addresses and representors; confirm "
            "nothing else on the host depends on this PF's VFs first."
            % dpu.get("pf_name")
        )
    if getattr(ctx.args, "restore_switchdev_legacy", False):
        risks.append(
            "Returning eswitch mode to legacy removes hardware-offload "
            "capability on this PF until switchdev is re-enabled."
        )
    if getattr(ctx.args, "restart_ovs", False):
        risks.append(
            "Restarting the OVS service affects every bridge/port on this "
            "host, not just this lab's bridge, including any unrelated "
            "traffic already running on the same box."
        )
    return risks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lab_cleanup",
        description=(
            "Tear down the VM-to-VM single-DPU lab (see "
            "docs/operations/cleanup-and-rollback.md). Default: dry-run, no mutations."
        ),
    )
    parser.add_argument("--config", required=True, help="VM2VM lab YAML (see config/README-lab-vm2vm.md).")
    parser.add_argument("--dry-run", action="store_true", help="Read-only (default). Wins over --apply.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Allow destructive stages (also needs config safety.dry_run_default: false).",
    )
    parser.add_argument(
        "--run-id",
        help="Reuse a specific run id under tests/results/<vendor>/. Default: the vendor's latest labctl run, or a fresh timestamp.",
    )
    parser.add_argument(
        "--undefine-vms",
        action="store_true",
        help="Also `virsh undefine` each VM (stage 2). Default: destroy (stop) only, keep the domain definition.",
    )
    parser.add_argument(
        "--restore-switchdev-legacy",
        action="store_true",
        help="Opt in to stage 8: return the PF's eswitch mode to legacy (only if dpu.enable_switchdev is true).",
    )
    parser.add_argument(
        "--restart-ovs",
        action="store_true",
        help="Opt in to stage 9: restart the OVS service (openvswitch-switch, falling back to openvswitch).",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report.")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    try:
        config = load_vm2vm_config(args.config)
    except (OSError, ValueError) as exc:
        print("error: failed to load %s: %s" % (args.config, exc), file=sys.stderr)
        return 1

    issues = validate_vm2vm_config(config)
    if issues:
        print("error: config is invalid:", file=sys.stderr)
        for issue in issues:
            print("  - %s" % issue, file=sys.stderr)
        return 1

    vendor_type = normalize_vendor((config.get("vendor") or {}).get("type")) or "unknown"
    run_id = resolve_run_id(vendor_type, args.run_id)
    run_dir = run_dir_for(vendor_type, run_id)
    evidence_dir = run_dir / "cleanup"

    ctx = CleanupContext(args, config, vendor_type, run_dir, run_id)
    risks = rollback_risks(ctx)

    if ctx.apply_allowed:
        print("lab_cleanup: about to APPLY. Rollback risks:", file=sys.stderr)
        for risk in risks:
            print("  ! %s" % risk, file=sys.stderr)

    stages: Dict[str, Any] = {}
    for name in CLEANUP_STAGES:
        try:
            stages[name] = STAGE_FUNCS[name](ctx)
        except Exception as exc:  # keep going; one stage's failure must not hide the rest
            LOG.exception("cleanup stage %s failed", name)
            stages[name] = {"stage": name, "error": "unexpected failure: %s" % exc}

    report = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vendor": vendor_type,
        "run_id": run_id,
        "config": str(Path(args.config).resolve()),
        "dry_run": ctx.dry_run,
        "apply": ctx.apply_allowed,
        "preserved_interfaces": ctx.preserved,
        "rollback_risks": risks,
        "stages": stages,
        "evidence_dir": str(evidence_dir),
    }

    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "cleanup-summary.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
    except OSError as exc:
        print("warning: could not write evidence to %s: %s" % (evidence_dir, exc), file=sys.stderr)

    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
