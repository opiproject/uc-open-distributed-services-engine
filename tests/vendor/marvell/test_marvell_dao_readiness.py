"""Marvell DAO readiness statuses (fixtures only, no DAO/DPDK execution)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.marvell_octeon.readiness import (
    MARVELL_READY_FOR_DRY_RUN,
    MARVELL_REPRESENTOR_DISCOVERY_REQUIRED,
    evaluate_marvell_readiness,
)

FIXTURES = ROOT / "tests" / "fixtures" / "marvell_octeon"


def _cfg(**extra):
    base = {
        "host": {"management_interface": "eno1"},
        "dpu": {"pf_name": "eth0"},
        "vendor": {"profile": "marvell-octeon-dao-ovs-offload"},
        "marvell": {"require_dao": True, "require_dpdk": True, "datapath": "dao"},
        "dpdk": {"hugepages_required": True},
        "dao": {},
    }
    base.update(extra)
    return base


def _ready_samples(**overrides):
    samples = {
        "lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
        "dpdk_available": True,
        "dpdk_devbind_tool": True,
        "dpdk_devbind": (FIXTURES / "dpdk_devbind_status.txt").read_text(encoding="utf-8"),
        "meminfo": "HugePages_Total: 1024\nHugePages_Free: 512\n",
        "dao_available": True,
        "dao_port_list": (FIXTURES / "dao_port_list.txt").read_text(encoding="utf-8"),
    }
    samples.update(overrides)
    return samples


class MarvellDaoReadinessTests(unittest.TestCase):
    def test_example_profile_is_dao_ovs_offload(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-marvell-vm2vm.example.yaml")
        self.assertEqual(cfg["vendor"]["profile"], "marvell-octeon-dao-ovs-offload")
        self.assertEqual(cfg["marvell"]["mode"], "dao-ovs-offload")
        self.assertFalse(cfg["marvell"]["allow_host_ovs_for_forwarding"])

    def test_ready_for_dry_run_with_injected_samples(self):
        result = evaluate_marvell_readiness(_cfg(), samples=_ready_samples())
        self.assertEqual(result.details["readiness_status"], MARVELL_READY_FOR_DRY_RUN)

    def test_representor_discovery_required_without_ports(self):
        result = evaluate_marvell_readiness(
            _cfg(),
            samples=_ready_samples(dao_port_list="", dpdk_devbind=""),
        )
        self.assertEqual(
            result.details["readiness_status"], MARVELL_REPRESENTOR_DISCOVERY_REQUIRED
        )


if __name__ == "__main__":
    unittest.main()
