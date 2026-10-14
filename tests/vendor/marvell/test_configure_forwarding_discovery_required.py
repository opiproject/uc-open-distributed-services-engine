"""Marvell configure_forwarding() — honest DAO/representor gating (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (
    FORWARDING_ERROR_DAO_NOT_AVAILABLE,
    FORWARDING_ERROR_REPRESENTOR_MAPPING_REQUIRED,
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
)
from plugins.marvell_octeon.backend import MarvellOcteonBackend


def _intent(representors=("dpdk-repr0", "dpdk-repr1")) -> ForwardingIntent:
    return ForwardingIntent(
        name="odse-vm2vm-marvell",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor=representors[0]),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor=representors[1]),
        ],
        policy=ForwardingPolicy(bidirectional=True),
        offload=ForwardingOffloadRequest(requested=True),
        bridge="br-int",
    )


class MarvellConfigureForwardingTests(unittest.TestCase):
    def test_dao_not_available_returns_specific_error(self):
        backend = MarvellOcteonBackend()
        with patch(
            "plugins.marvell_octeon.discovery.scan_dao_environment",
            return_value={"dao_env_present": False},
        ):
            result = backend.configure_forwarding(_intent())
        self.assertFalse(result.success)
        self.assertIn(FORWARDING_ERROR_DAO_NOT_AVAILABLE, result.errors)

    def test_representor_mapping_required_when_dao_available(self):
        backend = MarvellOcteonBackend(configured_dao_app="/opt/dao/dao-ovs")
        with patch(
            "plugins.marvell_octeon.discovery.scan_dao_environment",
            return_value={"dao_env_present": True},
        ), patch("plugins.base.commands.command_exists", return_value=True):
            result = backend.configure_forwarding(_intent(representors=(None, "dpdk-repr1")))
        self.assertFalse(result.success)
        self.assertIn(FORWARDING_ERROR_REPRESENTOR_MAPPING_REQUIRED, result.errors)

    def test_staged_success_when_dao_and_representors_present(self):
        backend = MarvellOcteonBackend(configured_dao_app="/opt/dao/dao-ovs")
        with patch(
            "plugins.marvell_octeon.discovery.scan_dao_environment",
            return_value={"dao_env_present": True},
        ), patch("plugins.base.commands.command_exists", return_value=True):
            result = backend.configure_forwarding(_intent())
        self.assertTrue(result.success)
        self.assertEqual(result.forwarding_entity, "Marvell OCTEON hardware path")
        self.assertIn("dpdk-repr0", result.configured_ports)
        self.assertFalse(result.offload_confirmed)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
