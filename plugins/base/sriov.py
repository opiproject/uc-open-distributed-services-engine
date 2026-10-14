"""Generic Linux SR-IOV sysfs helpers (sriov_numvfs), vendor-parameterized.

/sys/class/net/<netdev>/device/sriov_numvfs (and .../sriov_totalvfs) is
the standard upstream-kernel PCI SR-IOV control file documented in
Documentation/PCI/pci-iov-howto.rst. It is not an NVIDIA-specific
mechanism -- it is implemented by every SR-IOV-capable PF driver,
including mlx5_core (NVIDIA ConnectX/BlueField), ice/idpf (Intel
E810/IPU), and others. This module holds the vendor-neutral read/write
logic once so every vendor backend (plugins/nvidia_bluefield,
plugins/intel_ipu, ...) can call the same well-tested code path instead
of re-implementing (or stubbing out) sysfs VF creation per vendor.

Default dry_run=True. Every write goes through write_file, so dry_run
never touches the filesystem.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .commands import CommandResult, read_file, write_file
from .models import STATUS_ERROR, STATUS_OK, OperationResult, PhysicalFunction

LOG = logging.getLogger(__name__)

SYSFS_NET_ROOT = "/sys/class/net"
SYSFS_SRIOV_NUMVFS = "{net_root}/{netdev}/device/sriov_numvfs"
SYSFS_SRIOV_TOTALVFS = "{net_root}/{netdev}/device/sriov_totalvfs"

WriteFn = Callable[..., CommandResult]


def _parse_vf_count(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return None


def read_sriov_vf_counts(
    netdev: str,
    *,
    net_sysfs_root: Optional[str] = None,
    read_file_fn: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Read-only sriov_numvfs / sriov_totalvfs for a PF netdev.

    None means "unreadable" (missing sysfs, wrong netdev, no such
    hardware) -- never guessed as 0.
    """
    reader = read_file_fn or read_file
    root = net_sysfs_root or SYSFS_NET_ROOT
    numvfs_path = SYSFS_SRIOV_NUMVFS.format(net_root=root, netdev=netdev)
    totalvfs_path = SYSFS_SRIOV_TOTALVFS.format(net_root=root, netdev=netdev)
    current = _parse_vf_count(reader(numvfs_path))
    total = _parse_vf_count(reader(totalvfs_path))
    return {
        "current": current,
        "total": total,
        "numvfs_path": numvfs_path,
        "totalvfs_path": totalvfs_path,
    }


def configure_sriov_numvfs(
    netdev: Optional[str],
    numvfs: Optional[int],
    dry_run: bool = True,
    *,
    vendor: str = "generic",
    force: bool = False,
    net_sysfs_root: Optional[str] = None,
    read_file_fn: Optional[Callable[[str], Optional[str]]] = None,
    write_file_fn: Optional[WriteFn] = None,
) -> OperationResult:
    """Set sriov_numvfs on a PF via sysfs (standard kernel SR-IOV convention).

    Reads current/total VF counts first (read-only) and never guesses:

    - pf/numvfs missing or invalid -> STATUS_ERROR, nothing touched.
    - current sriov_numvfs unreadable -> refuse unless force=True (the
      hardware/netdev may not exist on this host at all).
    - numvfs requested above sriov_totalvfs (the firmware/driver
      provisioned count) -> STATUS_ERROR, refuses to silently clamp.
    - current == numvfs already -> STATUS_OK, executed=False, no write.
    - otherwise writes 0 first when moving between two different
      non-zero values (most SR-IOV drivers, e.g. mlx5_core, refuse a
      direct nonzero->nonzero change), then writes the requested value.
      Every write goes through write_file (or write_file_fn), so
      dry_run=True (the default, and what tools/labctl.py passes until
      --apply clears the apply-readiness gate for the prepare-vfs stage)
      never touches the filesystem.

    vendor is metadata only (OperationResult.vendor / log lines); the
    sysfs mechanism itself is identical for every caller.
    """
    op = "configure_sriov"
    if not netdev:
        return OperationResult(
            operation=op,
            vendor=vendor,
            status=STATUS_ERROR,
            message="pf (PF netdev name) is required",
            destructive=True,
            executed=False,
        )
    if isinstance(numvfs, bool) or not isinstance(numvfs, int) or numvfs < 0:
        return OperationResult(
            operation=op,
            vendor=vendor,
            status=STATUS_ERROR,
            message="numvfs must be a non-negative integer (got %r)" % (numvfs,),
            destructive=True,
            executed=False,
            details={"pf": netdev},
        )

    reader = read_file_fn or read_file
    writer = write_file_fn or write_file
    root = net_sysfs_root or SYSFS_NET_ROOT
    numvfs_path = SYSFS_SRIOV_NUMVFS.format(net_root=root, netdev=netdev)
    totalvfs_path = SYSFS_SRIOV_TOTALVFS.format(net_root=root, netdev=netdev)
    current = _parse_vf_count(reader(numvfs_path))
    total = _parse_vf_count(reader(totalvfs_path))

    details: Dict[str, Any] = {
        "pf": netdev,
        "requested_numvfs": numvfs,
        "current_numvfs": current,
        "sriov_totalvfs": total,
        "numvfs_path": numvfs_path,
        "totalvfs_path": totalvfs_path,
        "dry_run": dry_run,
        "force": force,
    }

    if current is None and not force:
        LOG.warning(
            "refuse configure_sriov (%s) on %s: %s unreadable and force=False",
            vendor,
            netdev,
            numvfs_path,
        )
        return OperationResult(
            operation=op,
            vendor=vendor,
            status=STATUS_ERROR,
            message=(
                "current sriov_numvfs unknown (%s unreadable); refusing to write "
                "without force=True" % numvfs_path
            ),
            destructive=True,
            executed=False,
            details=details,
            physical_functions=[PhysicalFunction(netdev=netdev, max_vfs=total, current_vfs=None)],
        )

    if total is not None and numvfs > total:
        return OperationResult(
            operation=op,
            vendor=vendor,
            status=STATUS_ERROR,
            message=(
                "requested numvfs=%d exceeds sriov_totalvfs=%d for %s; raise the "
                "driver/firmware-provisioned VF count first -- this will not "
                "silently clamp" % (numvfs, total, netdev)
            ),
            destructive=True,
            executed=False,
            details=details,
            physical_functions=[
                PhysicalFunction(netdev=netdev, max_vfs=total, current_vfs=current)
            ],
        )

    already_satisfied = current is not None and current == numvfs
    if already_satisfied:
        return OperationResult(
            operation=op,
            vendor=vendor,
            status=STATUS_OK,
            message="sriov_numvfs on %s already == %d; no write needed" % (netdev, numvfs),
            destructive=True,
            executed=False,
            details=details,
            physical_functions=[
                PhysicalFunction(netdev=netdev, max_vfs=total, current_vfs=current)
            ],
        )

    # Decide the write sequence. Most SR-IOV NICs (mlx5_core included)
    # refuse a direct nonzero -> different-nonzero change, so reset to 0
    # first unless the target itself is 0 or the PF is already at 0.
    # When current is unknown (force=True path), reset defensively first.
    if numvfs == 0:
        values_to_write = [0]
    elif current is None or current != 0:
        values_to_write = [0, numvfs]
    else:
        values_to_write = [numvfs]

    commands: List[CommandResult] = []
    for value in values_to_write:
        step = "reset_to_zero" if value == 0 and value != values_to_write[-1] else "set_numvfs"
        cmd = writer(numvfs_path, "%d\n" % value, dry_run=dry_run)
        commands.append(cmd)
        if not dry_run and not (cmd.executed and cmd.returncode == 0):
            details["failed_at"] = step
            LOG.error(
                "configure_sriov write failed vendor=%s pf=%s step=%s value=%s stderr=%s",
                vendor,
                netdev,
                step,
                value,
                cmd.stderr,
            )
            return OperationResult(
                operation=op,
                vendor=vendor,
                status=STATUS_ERROR,
                message="failed to write sriov_numvfs=%d on %s (%s): %s"
                % (value, netdev, step, cmd.stderr),
                destructive=True,
                executed=True,
                commands=commands,
                details=details,
                physical_functions=[
                    PhysicalFunction(netdev=netdev, max_vfs=total, current_vfs=current)
                ],
            )

    new_current = numvfs if not dry_run else current
    LOG.info(
        "configure_sriov vendor=%s pf=%s %s -> %d dry_run=%s force=%s",
        vendor,
        netdev,
        current,
        numvfs,
        dry_run,
        force,
    )
    return OperationResult(
        operation=op,
        vendor=vendor,
        status=STATUS_OK,
        message="sriov_numvfs %s -> %d on %s dry_run=%s" % (current, numvfs, netdev, dry_run),
        destructive=True,
        executed=(not dry_run),
        commands=commands,
        details=details,
        physical_functions=[
            PhysicalFunction(netdev=netdev, max_vfs=total, current_vfs=new_current)
        ],
    )
