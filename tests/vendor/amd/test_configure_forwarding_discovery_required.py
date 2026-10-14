"""AMD configure_forwarding() — discovery-first, no assumed control path (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (
    FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED,
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
)
from plugins.amd_pensando.backend import AmdPensandoBackend


def _intent(representors=(None, None)) -> ForwardingIntent:
    return ForwardingIntent(
        name="odse-vm2vm-amd",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor=representors[0]),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor=representors[1]),
        ],
        policy=ForwardingPolicy(bidirectional=True),
        offload=ForwardingOffloadRequest(requested=False),
        bridge="br-int",
    )


class AmdConfigureForwardingTests(unittest.TestCase):
    def setUp(self):
        self.backend = AmdPensandoBackend()

    def test_no_representors_returns_specific_error_and_gaps(self):
        result = self.backend.configure_forwarding(_intent())
        self.assertFalse(result.success)
        self.assertIn(FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED, result.errors)
        self.assertIn("gap_report", result.details)
        self.assertNotEqual(result.details["gap_report"].get("final_status"), "AMD_READY_FOR_VM_TO_VM_DRY_RUN")

    def test_representors_on_intent_are_not_control_path_proof(self):
        result = self.backend.configure_forwarding(_intent(representors=("eth0v0", "eth0v1")))
        self.assertFalse(result.success)
        self.assertIn(FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED, result.errors)
        self.assertFalse(result.details.get("ovs_attempted"))


if __name__ == "__main__":
    unittest.main()
