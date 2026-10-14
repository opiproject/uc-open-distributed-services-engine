"""Shared helpers for tests/vendor/* (fixtures only, no hardware)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"

_SPEC = importlib.util.spec_from_file_location(
    "odse_capability_report",
    ROOT / "tools" / "capability_report.py",
)
_cap_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules.setdefault(_SPEC.name, _cap_mod)
_SPEC.loader.exec_module(_cap_mod)

row_from_discovery = _cap_mod.row_from_discovery


def read_fixture(vendor_dir: str, name: str) -> str:
    return (FIXTURES / vendor_dir / name).read_text(encoding="utf-8")


def nvidia_samples() -> Dict[str, Any]:
    d = "nvidia_bluefield"
    return {
        "lspci": read_fixture(d, "lspci.txt"),
        "devlink_dev": read_fixture(d, "devlink_dev.txt"),
        "devlink_port": read_fixture(d, "devlink_port.txt"),
        "ip_link": read_fixture(d, "ip_d_link.txt"),
        "ovs": read_fixture(d, "ovs_vsctl.txt"),
        "ethtool": {
            "p0": read_fixture(d, "ethtool_p0.txt"),
            "pf0vf0": read_fixture(d, "ethtool_pf0vf0.txt"),
            "eth0": read_fixture(d, "ethtool_ice.txt"),
        },
    }


def amd_samples() -> Dict[str, Any]:
    d = "amd_pensando"
    return {
        "lspci": read_fixture(d, "lspci.txt"),
        "ethtool_i": {"eth0": read_fixture(d, "ethtool_i.txt")},
        "ethtool_s": {"eth0": read_fixture(d, "ethtool_s.txt")},
        "rdma_link": read_fixture(d, "rdma_link.txt"),
        "lsmod": read_fixture(d, "lsmod.txt"),
    }


def marvell_samples() -> Dict[str, Any]:
    d = "marvell_octeon"
    return {
        "lspci": read_fixture(d, "lspci.txt"),
        "dpdk_devbind": read_fixture(d, "dpdk_devbind.txt"),
        "ovs": "Bridge br-int\n",
        "environ": {"DAO_PATH": "/opt/dao"},
    }
