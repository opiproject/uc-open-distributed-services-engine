"""Intel configure_forwarding() — OPI endpoint gating, discovery-required (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import (
    FORWARDING_ERROR_OPI_ENDPOINT_REQUIRED,
    ForwardingEndpoint,
    ForwardingIntent,
    ForwardingOffloadRequest,
    ForwardingPolicy,
)
from plugins.intel_ipu.backend import IntelIpuBackend


def _intent() -> ForwardingIntent:
    return ForwardingIntent(
        name="odse-vm2vm-intel",
        type="l2-vm-to-vm",
        mode="bridge",
        endpoints=[
            ForwardingEndpoint(name="vm1-endpoint", vm="vm1", vf_index=0, representor=None),
            ForwardingEndpoint(name="vm2-endpoint", vm="vm2", vf_index=1, representor=None),
        ],
        policy=ForwardingPolicy(bidirectional=True),
        offload=ForwardingOffloadRequest(requested=False),
    )


class IntelConfigureForwardingTests(unittest.TestCase):
    def test_no_opi_endpoint_returns_specific_error(self):
        backend = IntelIpuBackend(config={})
        result = backend.configure_forwarding(_intent())
        self.assertFalse(result.success)
        self.assertIn(FORWARDING_ERROR_OPI_ENDPOINT_REQUIRED, result.errors)

    def test_opi_endpoint_present_is_still_discovery_required(self):
        from plugins.intel_ipu.vm_to_vm_capability import (
            INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED,
        )

        backend = IntelIpuBackend(config={"opi_endpoint": "https://opi.example.invalid:443"})
        result = backend.configure_forwarding(_intent())
        self.assertFalse(result.success)
        self.assertIn(INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED, result.errors)
        self.assertTrue(result.details.get("discovery_required"))
        self.assertFalse(result.details.get("ovs_attempted"))
        self.assertEqual(result.details.get("verdict"), "UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
