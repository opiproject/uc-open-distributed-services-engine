"""NVIDIA mlx5 eswitch mode helpers (devlink first).

Default dry_run=True. Mode changes are refused if VFs look bound unless
force=True. Sysfs fallback is off unless allow_sysfs=True (and a netdev
is supplied). No mlxconfig.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..base import sriov as _sriov
from ..base.commands import command_exists, read_file, run, write_file
from ..base.models import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    CommandResult,
    OperationResult,
    PhysicalFunction,
)

LOG = logging.getLogger(__name__)
VENDOR = "nvidia"

_PCI_RE = re.compile(
    r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$"
)
_MODE_RE = re.compile(r"\bmode\s+(\S+)", re.IGNORECASE)
_VALID_MODES = frozenset({"legacy", "switchdev"})

SYSFS_ESWITCH_MODE = "/sys/class/net/{netdev}/compat/devlink/mode"
SYSFS_PCI_DEVICES = "/sys/bus/pci/devices"
SYSFS_PCI_DRIVERS = "/sys/bus/pci/drivers"
SYSFS_NET_ROOT = "/sys/class/net"
SYSFS_SRIOV_NUMVFS = "{net_root}/{netdev}/device/sriov_numvfs"
SYSFS_SRIOV_TOTALVFS = "{net_root}/{netdev}/device/sriov_totalvfs"

RunFn = Callable[..., CommandResult]
WriteFn = Callable[..., CommandResult]


def normalize_pci(pci_addr: Optional[str]) -> Optional[str]:
    if not pci_addr:
        return None
    text = pci_addr.strip().lower()
    if text.startswith("pci/"):
        text = text[4:]
    if not _PCI_RE.match(text):
        return None
    return text


def parse_eswitch_show(raw: Optional[str]) -> Optional[str]:
    """Extract eswitch mode from `devlink dev eswitch show` text."""
    if not raw:
        return None
    match = _MODE_RE.search(raw)
    if not match:
        return None
    mode = match.group(1).lower().rstrip(",")
    if mode not in _VALID_MODES:
        return None
    return mode


def list_bound_vfs(
    pci_addr: str,
    *,
    pci_sysfs_root: Optional[str] = None,
    bound_vfs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """List VFs that appear to have a driver bound.

    If bound_vfs is passed (including []), that list is trusted (tests).
    If sysfs is missing, probed=False and bound=[] — callers must refuse
    mode change without force rather than assume unbound.
    """
    if bound_vfs is not None:
        bound = [v for v in bound_vfs if v]
        return {"bound": bound, "probed": True, "method": "injected"}
    pci = normalize_pci(pci_addr)
    root = Path(pci_sysfs_root or SYSFS_PCI_DEVICES)
    pf_dir = root / (pci or "")
    if not pci or not pf_dir.is_dir():
        LOG.info("list_bound_vfs: sysfs %s missing; bind state unknown", pf_dir)
        return {"bound": [], "probed": False, "method": "sysfs_missing"}
    bound: List[str] = []
    for virtfn in sorted(pf_dir.glob("virtfn*")):
        driver = virtfn / "driver"
        if driver.exists() or driver.is_symlink():
            bound.append(virtfn.name)
    return {"bound": bound, "probed": True, "method": "sysfs"}


def get_eswitch_mode(
    pci_addr: str,
    *,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[Callable[[str], bool]] = None,
    allow_sysfs: bool = False,
    netdev: Optional[str] = None,
    read_file_fn: Optional[Callable[[str], Optional[str]]] = None,
) -> OperationResult:
    """Read-only: `devlink dev eswitch show pci/<pci>`."""
    pci = normalize_pci(pci_addr)
    if not pci:
        return OperationResult(
            operation="get_eswitch_mode",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="invalid pci address %r" % pci_addr,
            executed=False,
        )
    exists = command_exists_fn or command_exists
    run_fn = runner or run
    commands: List[CommandResult] = []
    if exists("devlink"):
        argv = ["devlink", "dev", "eswitch", "show", "pci/%s" % pci]
        cmd = run_fn(argv, dry_run=False, timeout=15.0, reason="eswitch show")
        commands.append(cmd)
        mode = parse_eswitch_show(cmd.stdout)
        pf = PhysicalFunction(pci_address=pci, eswitch_mode=mode, netdev=netdev)
        status = STATUS_OK if cmd.executed and cmd.returncode == 0 else STATUS_SKIPPED
        if cmd.executed and cmd.returncode not in (0, None) and mode is None:
            status = STATUS_ERROR
        if mode is not None:
            status = STATUS_OK
        return OperationResult(
            operation="get_eswitch_mode",
            vendor=VENDOR,
            status=status,
            message="eswitch mode=%s" % mode,
            executed=bool(cmd.executed),
            destructive=False,
            physical_functions=[pf],
            commands=commands,
            details={"mode": mode, "method": "devlink", "pci": pci},
        )
    if allow_sysfs and netdev:
        reader = read_file_fn or read_file
        path = SYSFS_ESWITCH_MODE.format(netdev=netdev)
        raw = reader(path)
        mode = None
        if raw:
            token = raw.strip().split()[0].lower()
            mode = token if token in _VALID_MODES else None
        return OperationResult(
            operation="get_eswitch_mode",
            vendor=VENDOR,
            status=STATUS_OK if mode else STATUS_SKIPPED,
            message="sysfs eswitch mode=%s (allow_sysfs)" % mode,
            executed=True,
            destructive=False,
            physical_functions=[PhysicalFunction(pci_address=pci, eswitch_mode=mode, netdev=netdev)],
            details={"mode": mode, "method": "sysfs", "path": path, "pci": pci},
        )
    return OperationResult(
        operation="get_eswitch_mode",
        vendor=VENDOR,
        status=STATUS_SKIPPED,
        message="devlink not available; sysfs fallback disabled (pass allow_sysfs=True)",
        executed=False,
        physical_functions=[PhysicalFunction(pci_address=pci, eswitch_mode=None, netdev=netdev)],
        details={"mode": None, "method": None, "pci": pci},
    )


def set_eswitch_mode_switchdev(
    pci_addr: str,
    dry_run: bool = True,
    *,
    force: bool = False,
    allow_sysfs: bool = False,
    netdev: Optional[str] = None,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[Callable[[str], bool]] = None,
    bound_vfs: Optional[Sequence[str]] = None,
    pci_sysfs_root: Optional[str] = None,
    write_file_fn: Optional[WriteFn] = None,
) -> OperationResult:
    return _set_eswitch_mode(
        pci_addr,
        "switchdev",
        dry_run=dry_run,
        force=force,
        allow_sysfs=allow_sysfs,
        netdev=netdev,
        runner=runner,
        command_exists_fn=command_exists_fn,
        bound_vfs=bound_vfs,
        pci_sysfs_root=pci_sysfs_root,
        write_file_fn=write_file_fn,
    )


def set_eswitch_mode_legacy(
    pci_addr: str,
    dry_run: bool = True,
    *,
    force: bool = False,
    allow_sysfs: bool = False,
    netdev: Optional[str] = None,
    runner: Optional[RunFn] = None,
    command_exists_fn: Optional[Callable[[str], bool]] = None,
    bound_vfs: Optional[Sequence[str]] = None,
    pci_sysfs_root: Optional[str] = None,
    write_file_fn: Optional[WriteFn] = None,
) -> OperationResult:
    return _set_eswitch_mode(
        pci_addr,
        "legacy",
        dry_run=dry_run,
        force=force,
        allow_sysfs=allow_sysfs,
        netdev=netdev,
        runner=runner,
        command_exists_fn=command_exists_fn,
        bound_vfs=bound_vfs,
        pci_sysfs_root=pci_sysfs_root,
        write_file_fn=write_file_fn,
    )


def unbind_vf(
    pci_addr: str,
    dry_run: bool = True,
    *,
    driver: Optional[str] = None,
    write_file_fn: Optional[WriteFn] = None,
    current_driver: Optional[str] = None,
    pci_sysfs_root: Optional[str] = None,
) -> OperationResult:
    pci = normalize_pci(pci_addr)
    if not pci:
        return OperationResult(
            operation="unbind_vf",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="invalid pci address %r" % pci_addr,
            destructive=True,
            executed=False,
        )
    drv = driver or current_driver or _driver_from_sysfs(pci, pci_sysfs_root)
    if not drv:
        return OperationResult(
            operation="unbind_vf",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="cannot unbind %s: driver unknown (not guessed)" % pci,
            destructive=True,
            executed=False,
            details={"pci": pci, "driver": None},
        )
    path = "%s/%s/unbind" % (SYSFS_PCI_DRIVERS, drv)
    writer = write_file_fn or write_file
    cmd = writer(path, pci + "\n", dry_run=dry_run)
    LOG.info("unbind_vf pci=%s driver=%s dry_run=%s", pci, drv, dry_run)
    return OperationResult(
        operation="unbind_vf",
        vendor=VENDOR,
        status=STATUS_OK if (cmd.dry_run or cmd.returncode == 0) else STATUS_ERROR,
        message="unbind %s from %s dry_run=%s" % (pci, drv, dry_run),
        destructive=True,
        executed=bool(cmd.executed),
        commands=[cmd],
        details={"pci": pci, "driver": drv, "path": path, "dry_run": dry_run},
    )


def bind_vf(
    pci_addr: str,
    driver: str = "mlx5_core",
    dry_run: bool = True,
    *,
    write_file_fn: Optional[WriteFn] = None,
) -> OperationResult:
    pci = normalize_pci(pci_addr)
    if not pci:
        return OperationResult(
            operation="bind_vf",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="invalid pci address %r" % pci_addr,
            destructive=True,
            executed=False,
        )
    if not driver:
        return OperationResult(
            operation="bind_vf",
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="driver required",
            destructive=True,
            executed=False,
        )
    path = "%s/%s/bind" % (SYSFS_PCI_DRIVERS, driver)
    writer = write_file_fn or write_file
    cmd = writer(path, pci + "\n", dry_run=dry_run)
    LOG.info("bind_vf pci=%s driver=%s dry_run=%s", pci, driver, dry_run)
    return OperationResult(
        operation="bind_vf",
        vendor=VENDOR,
        status=STATUS_OK if (cmd.dry_run or cmd.returncode == 0) else STATUS_ERROR,
        message="bind %s to %s dry_run=%s" % (pci, driver, dry_run),
        destructive=True,
        executed=bool(cmd.executed),
        commands=[cmd],
        details={"pci": pci, "driver": driver, "path": path, "dry_run": dry_run},
    )


def discover_representors(
    pf: Optional[str] = None,
    **kwargs: Any,
) -> OperationResult:
    """Delegate to nvidia discovery (devlink/portname)."""
    from . import discovery as nvidia_discovery

    return nvidia_discovery.discover_representors(pf=pf, **kwargs)


def plan_configure(pf: Optional[str] = None) -> OperationResult:
    """Backend hook: always dry-run switchdev set unless caller uses set_*."""
    if not pf:
        return OperationResult(
            operation="configure_switchdev",
            vendor=VENDOR,
            status=STATUS_SKIPPED,
            message="need PF PCI address",
            destructive=True,
            executed=False,
        )
    result = set_eswitch_mode_switchdev(pf, dry_run=True, force=False)
    result.operation = "configure_switchdev"
    return result


def read_sriov_vf_counts(
    netdev: str,
    *,
    net_sysfs_root: Optional[str] = None,
    read_file_fn: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Read-only sriov_numvfs / sriov_totalvfs for a PF netdev.

    None means "unreadable" (missing sysfs, wrong netdev, no such
    hardware) — never guessed as 0. Thin wrapper: the sysfs file itself
    is a generic kernel SR-IOV mechanism (see plugins/base/sriov.py),
    not NVIDIA-specific; kept here under its historical name/signature
    so existing callers and tests are unaffected.
    """
    return _sriov.read_sriov_vf_counts(
        netdev,
        net_sysfs_root=net_sysfs_root,
        read_file_fn=read_file_fn,
    )


def configure_sriov_numvfs(
    netdev: Optional[str],
    numvfs: Optional[int],
    dry_run: bool = True,
    *,
    force: bool = False,
    net_sysfs_root: Optional[str] = None,
    read_file_fn: Optional[Callable[[str], Optional[str]]] = None,
    write_file_fn: Optional[WriteFn] = None,
) -> OperationResult:
    """Set sriov_numvfs on a PF via sysfs (mlx5_core convention).

    Thin wrapper (vendor="nvidia") around the vendor-neutral read/
    refuse/write decision tree in plugins/base/sriov.py (unreadable-
    without-force, above-totalvfs, already-satisfied, reset-before-
    nonzero-to-nonzero, apply-vs-dry-run). Kept here under its
    historical name/signature so existing callers and tests are
    unaffected.
    """
    return _sriov.configure_sriov_numvfs(
        netdev,
        numvfs,
        dry_run=dry_run,
        vendor=VENDOR,
        force=force,
        net_sysfs_root=net_sysfs_root,
        read_file_fn=read_file_fn,
        write_file_fn=write_file_fn,
    )


def _set_eswitch_mode(
    pci_addr: str,
    mode: str,
    *,
    dry_run: bool,
    force: bool,
    allow_sysfs: bool,
    netdev: Optional[str],
    runner: Optional[RunFn],
    command_exists_fn: Optional[Callable[[str], bool]],
    bound_vfs: Optional[Sequence[str]],
    pci_sysfs_root: Optional[str],
    write_file_fn: Optional[WriteFn],
) -> OperationResult:
    pci = normalize_pci(pci_addr)
    op = "set_eswitch_mode_%s" % mode
    if not pci:
        return OperationResult(
            operation=op,
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="invalid pci address %r" % pci_addr,
            destructive=True,
            executed=False,
        )
    bind_state = list_bound_vfs(
        pci, pci_sysfs_root=pci_sysfs_root, bound_vfs=bound_vfs
    )
    if not force and (
        bind_state["bound"] or not bind_state["probed"]
    ):
        reason = (
            "VFs appear bound: %s" % bind_state["bound"]
            if bind_state["bound"]
            else "VF bind state unknown (sysfs not probed); refusing without force=True"
        )
        LOG.warning("refuse eswitch %s on %s: %s", mode, pci, reason)
        return OperationResult(
            operation=op,
            vendor=VENDOR,
            status=STATUS_ERROR,
            message="refused eswitch mode=%s on %s: %s" % (mode, pci, reason),
            destructive=True,
            executed=False,
            details={
                "pci": pci,
                "mode": mode,
                "force": False,
                "bound_vfs": list(bind_state["bound"]),
                "bind_probed": bind_state["probed"],
                "dry_run": dry_run,
            },
        )

    exists = command_exists_fn or command_exists
    run_fn = runner or run
    commands: List[CommandResult] = []
    if exists("devlink"):
        argv = ["devlink", "dev", "eswitch", "set", "pci/%s" % pci, "mode", mode]
        cmd = run_fn(
            argv,
            dry_run=dry_run,
            timeout=15.0,
            reason="eswitch set %s" % mode,
        )
        commands.append(cmd)
        LOG.info("eswitch set mode=%s pci=%s dry_run=%s force=%s", mode, pci, dry_run, force)
        return OperationResult(
            operation=op,
            vendor=VENDOR,
            status=STATUS_OK,
            message="devlink eswitch set mode %s pci/%s dry_run=%s" % (mode, pci, dry_run),
            destructive=True,
            executed=bool(cmd.executed),
            commands=commands,
            physical_functions=[PhysicalFunction(pci_address=pci, eswitch_mode=mode if dry_run else None)],
            details={
                "pci": pci,
                "mode": mode,
                "method": "devlink",
                "dry_run": dry_run,
                "force": force,
                "sysfs_fallback": False,
            },
        )

    if allow_sysfs:
        if not netdev:
            return OperationResult(
                operation=op,
                vendor=VENDOR,
                status=STATUS_SKIPPED,
                message="devlink missing; sysfs fallback needs netdev= (not guessed)",
                destructive=True,
                executed=False,
                details={"pci": pci, "allow_sysfs": True, "netdev": None},
            )
        path = SYSFS_ESWITCH_MODE.format(netdev=netdev)
        writer = write_file_fn or write_file
        cmd = writer(path, mode + "\n", dry_run=dry_run)
        commands.append(cmd)
        LOG.warning("eswitch sysfs fallback path=%s (explicit allow_sysfs)", path)
        return OperationResult(
            operation=op,
            vendor=VENDOR,
            status=STATUS_OK,
            message="sysfs eswitch placeholder write %s dry_run=%s" % (path, dry_run),
            destructive=True,
            executed=bool(cmd.executed),
            commands=commands,
            details={
                "pci": pci,
                "mode": mode,
                "method": "sysfs",
                "path": path,
                "dry_run": dry_run,
            },
        )

    return OperationResult(
        operation=op,
        vendor=VENDOR,
        status=STATUS_SKIPPED,
        message=(
            "devlink not available; sysfs fallback disabled "
            "(pass allow_sysfs=True and netdev=...)"
        ),
        destructive=True,
        executed=False,
        details={"pci": pci, "mode": mode, "method": None, "sysfs_fallback": False},
    )


def _driver_from_sysfs(pci: str, pci_sysfs_root: Optional[str]) -> Optional[str]:
    root = Path(pci_sysfs_root or SYSFS_PCI_DEVICES)
    link = root / pci / "driver"
    try:
        if link.is_symlink() or link.exists():
            return link.resolve().name
    except OSError as exc:
        LOG.info("driver symlink %s: %s", link, exc)
    return None
