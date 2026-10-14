"""AMD configure_forwarding is blocked until a control path is proven."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando.backend import AmdPensandoBackend
from plugins.amd_pensando.capability_report import AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH
from plugins.amd_pensando.discovery_workflow import forwarding_allowed
from plugins.base.models import (
    FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED,
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
)
from tests.vendor.harness import amd_samples


def _intent() -> ForwardingIntent:
    return ForwardingIntent(
        name="odse-vm2vm-amd",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor=None),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor=None),
        ],
        policy=ForwardingPolicy(bidirectional=True),
        offload=ForwardingOffloadRequest(requested=False),
    )


class AmdForwardingBlockedTests(unittest.TestCase):
    def test_backend_blocked_without_control_path(self):
        backend = AmdPensandoBackend(lab_config={"amd_pensando": {}})
        result = backend.configure_forwarding(_intent())
        self.assertFalse(result.success)
        self.assertIn(FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED, result.errors)
        self.assertIn(AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH, result.errors)
        self.assertFalse(result.details.get("ovs_attempted"))

    def test_forwarding_allowed_false_on_ionic_only_samples(self):
        self.assertFalse(forwarding_allowed({}, samples=amd_samples()))


if __name__ == "__main__":
    unittest.main()
