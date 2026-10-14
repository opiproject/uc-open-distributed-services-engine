"""Generic OVS helpers (plugins/base/ovs.py), vendor-parameterized.

NVIDIA's plugins/nvidia_bluefield/ovs.py and Intel's
plugins/intel_ipu/ovs_p4.py both delegate here -- see
tests/unit/test_nvidia_bluefield_ovs.py and
tests/vendor/intel/test_intel_ovs_p4.py for the vendor-specific wrapper
behavior (including NVIDIA's DOCA-specific gating, which stays out of
this module).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base import ovs as base_ovs
from plugins.base.models import CommandResult


class FakeRunner:
    def __init__(self, stdout_by_argv=None, returncode=0):
        self.calls = []
        self.stdout_by_argv = stdout_by_argv or {}
        self.returncode = returncode

    def __call__(self, argv, *, dry_run=True, timeout=None, reason=None, **_k):
        argv_list = list(argv)
        self.calls.append((argv_list, dry_run))
        if dry_run:
            return CommandResult(argv=argv_list, dry_run=True, executed=False)
        return CommandResult(
            argv=argv_list,
            dry_run=False,
            executed=True,
            returncode=self.returncode,
            stdout=self.stdout_by_argv.get(tuple(argv_list), ""),
        )


class ParseTests(unittest.TestCase):
    def test_parse_ovs_list(self):
        self.assertEqual(base_ovs.parse_ovs_list("[system, netdev]"), ["system", "netdev"])
        self.assertEqual(base_ovs.parse_ovs_list(""), [])

    def test_parse_other_config(self):
        self.assertEqual(base_ovs.parse_other_config('{hw-offload="true"}'), {"hw-offload": "true"})


class VendorParameterizedTests(unittest.TestCase):
    def test_vendor_is_stamped_on_result(self):
        runner = FakeRunner()
        result = base_ovs.create_ovs_bridge(
            "br-int", vendor="intel", dry_run=True, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.vendor, "intel")

    def test_create_bridge_requires_name(self):
        result = base_ovs.create_ovs_bridge("", vendor="intel", dry_run=True)
        self.assertEqual(result.status, "error")

    def test_add_representor_port_dry_run_not_executed(self):
        runner = FakeRunner()
        result = base_ovs.add_representor_port(
            "br-int", "acc-pr0", vendor="intel", dry_run=True, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)

    def test_add_representor_port_apply_error_on_nonzero_returncode(self):
        runner = FakeRunner(returncode=1)
        result = base_ovs.add_representor_port(
            "br-int", "acc-pr0", vendor="intel", dry_run=False, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "error")

    def test_add_representor_port_apply_not_executed_is_error_not_ok(self):
        """A runner that plans (executed=False) instead of running, even
        when the caller asked for dry_run=False (e.g. ACC SSH not
        configured), must never be reported as a successful write."""

        def _never_executes(argv, *, dry_run=True, timeout=None, reason=None, **_k):
            return CommandResult(argv=list(argv), dry_run=True, executed=False, returncode=None)

        result = base_ovs.add_representor_port(
            "br-int", "acc-pr0", vendor="intel", dry_run=False, runner=_never_executes, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "error")
        self.assertFalse(result.executed)

    def test_add_flow_requires_bridge_and_flow(self):
        result = base_ovs.add_flow("", "", vendor="intel", dry_run=True)
        self.assertEqual(result.status, "error")

    def test_probe_capabilities_missing_tool_is_skipped(self):
        result = base_ovs.probe_ovs_capabilities(vendor="intel", command_exists_fn=lambda n: False)
        self.assertEqual(result.status, "skipped")

    def test_probe_capabilities_injected_datapath_types(self):
        result = base_ovs.probe_ovs_capabilities(
            vendor="intel", command_exists_fn=lambda n: True, datapath_types=["system"]
        )
        self.assertEqual(result.details["datapath_types"], ["system"])
        self.assertNotIn("doca_datapath", result.details)  # NVIDIA-only, stays in nvidia_bluefield.ovs


if __name__ == "__main__":
    unittest.main()
