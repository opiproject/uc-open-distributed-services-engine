"""Tests for NVIDIA OVS helpers (mocked ovs-vsctl, no live OVS)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult
from plugins.nvidia_bluefield import ovs as nvovs


class FakeRunner:
    def __init__(self, stdout_by_argv=None, returncode=0, timed_out=False):
        self.calls: List[Tuple[List[str], bool]] = []
        self.stdout_by_argv = stdout_by_argv or {}
        self.returncode = returncode
        self.timed_out = timed_out

    def __call__(self, argv, *, dry_run=True, timeout=None, reason=None, **_k):
        argv_list = list(argv)
        self.calls.append((argv_list, dry_run))
        if dry_run:
            return CommandResult(argv=argv_list, dry_run=True, executed=False)
        key = tuple(argv_list)
        stdout = self.stdout_by_argv.get(key, "")
        if self.timed_out:
            return CommandResult(
                argv=argv_list,
                dry_run=False,
                executed=True,
                returncode=None,
                stdout=None,
                stderr=None,
                timed_out=True,
            )
        return CommandResult(
            argv=argv_list,
            dry_run=False,
            executed=True,
            returncode=self.returncode,
            stdout=stdout,
        )


def _cap_stdout(dps="system netdev", ifaces="system"):
    return {
        ("ovs-vsctl", "get", "Open_vSwitch", ".", "datapath_types"): dps,
        ("ovs-vsctl", "get", "Open_vSwitch", ".", "iface_types"): ifaces,
        ("ovs-vsctl", "get", "Open_vSwitch", ".", "other_config"): '{hw-offload="false"}',
    }


class ParseTests(unittest.TestCase):
    def test_parse_ovs_list(self):
        self.assertEqual(nvovs.parse_ovs_list("[system, netdev]"), ["system", "netdev"])
        self.assertEqual(nvovs.parse_ovs_list("system, doca"), ["system", "doca"])
        self.assertEqual(nvovs.parse_ovs_list(""), [])


class ProbeTests(unittest.TestCase):
    def test_doca_not_assumed(self):
        runner = FakeRunner(_cap_stdout())
        cap = nvovs.probe_ovs_capabilities(
            runner=runner,
            command_exists_fn=lambda n: n == "ovs-vsctl",
            dry_run=False,
        )
        self.assertFalse(cap.details["doca_datapath"])
        self.assertIn("system", cap.details["datapath_types"])

    def test_probe_dry_run_does_not_execute(self):
        runner = FakeRunner(_cap_stdout("system doca"))
        cap = nvovs.probe_ovs_capabilities(
            runner=runner, command_exists_fn=lambda n: True, dry_run=True
        )
        self.assertTrue(all(dry for _argv, dry in runner.calls))
        self.assertFalse(cap.details["doca_datapath"])
        self.assertEqual(cap.details["datapath_types"], [])

    def test_doca_when_listed(self):
        cap = nvovs.probe_ovs_capabilities(
            command_exists_fn=lambda n: True,
            datapath_types=["system", "doca"],
        )
        self.assertTrue(cap.details["doca_datapath"])

    def test_missing_ovs(self):
        cap = nvovs.probe_ovs_capabilities(command_exists_fn=lambda n: False)
        self.assertEqual(cap.status, "skipped")
        self.assertFalse(cap.details["doca_datapath"])


class MutateTests(unittest.TestCase):
    def test_hw_offload_dry_run(self):
        runner = FakeRunner()
        result = nvovs.enable_ovs_hw_offload(
            dry_run=True, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)
        self.assertTrue(runner.calls[0][1])
        self.assertIn("hw-offload=true", " ".join(runner.calls[0][0]))

    def test_doca_init_skipped_without_capability(self):
        runner = FakeRunner(_cap_stdout())
        result = nvovs.enable_doca_init(
            dry_run=True, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.executed)

    def test_doca_init_when_available(self):
        runner = FakeRunner()
        result = nvovs.enable_doca_init(
            dry_run=True,
            runner=runner,
            command_exists_fn=lambda n: True,
            datapath_types=["doca"],
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(any("doca-init" in " ".join(c[0]) for c in runner.calls))

    def test_create_system_bridge_dry_run(self):
        runner = FakeRunner()
        result = nvovs.create_ovs_bridge(
            "br-int", dry_run=True, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.details["datapath_type"], "system")
        self.assertIn("add-br", runner.calls[0][0])

    def test_create_doca_bridge_skipped_without_doca(self):
        runner = FakeRunner(_cap_stdout("system"))
        result = nvovs.create_ovs_doca_bridge(
            "br-int",
            dry_run=True,
            runner=runner,
            command_exists_fn=lambda n: True,
            datapath_types=["system"],
        )
        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.details["requires_doca_packages"])

    def test_create_doca_bridge_when_listed(self):
        runner = FakeRunner()
        result = nvovs.create_ovs_doca_bridge(
            "br-int",
            dry_run=True,
            runner=runner,
            command_exists_fn=lambda n: True,
            datapath_types=["system", "doca"],
        )
        self.assertEqual(result.status, "ok")
        argv_joined = [" ".join(c[0]) for c in runner.calls]
        self.assertTrue(any("datapath_type=doca" in a for a in argv_joined))

    def test_create_doca_bridge_dry_run_plans_without_live_probe(self):
        runner = FakeRunner()
        result = nvovs.create_ovs_doca_bridge(
            "br-int", dry_run=True, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.executed)
        argv_joined = [" ".join(c[0]) for c in runner.calls]
        self.assertTrue(any("add-br" in a for a in argv_joined))
        self.assertTrue(any("datapath_type=doca" in a for a in argv_joined))
        self.assertTrue(all(dry for _argv, dry in runner.calls))

    def test_create_system_bridge_apply_success(self):
        runner = FakeRunner(returncode=0)
        result = nvovs.create_ovs_bridge(
            "br-int", dry_run=False, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)

    def test_create_system_bridge_apply_nonzero_returncode_is_error(self):
        runner = FakeRunner(returncode=1)
        result = nvovs.create_ovs_bridge(
            "br-int", dry_run=False, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "error")

    def test_create_system_bridge_apply_timeout_is_error_not_ok(self):
        # A real SSH round trip that never returns (e.g. a slow-link DPU
        # stall, see docs/operations/dpu-remote-execution.md) has
        # returncode=None -- the same sentinel dry-run uses for "never
        # invoked". Confirm this is NOT mistaken for a successful plan.
        runner = FakeRunner(timed_out=True)
        result = nvovs.create_ovs_bridge(
            "br-int", dry_run=False, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "error")
        self.assertIn("timed out", result.message)

    def test_add_representor_apply_success(self):
        runner = FakeRunner(returncode=0)
        result = nvovs.add_representor_port(
            "br-int", "pf0vf0", dry_run=False, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.executed)

    def test_add_representor_apply_timeout_is_error_not_ok(self):
        runner = FakeRunner(timed_out=True)
        result = nvovs.add_representor_port(
            "br-int", "pf0vf0", dry_run=False, runner=runner, command_exists_fn=lambda n: True
        )
        self.assertEqual(result.status, "error")

    def test_add_representor_requires_names(self):
        result = nvovs.add_representor_port("", "", dry_run=True)
        self.assertEqual(result.status, "error")

    def test_add_representor_dry_run(self):
        runner = FakeRunner()
        result = nvovs.add_representor_port(
            "br-int",
            "pf0vf0",
            dry_run=True,
            runner=runner,
            command_exists_fn=lambda n: True,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.details["representor"], "pf0vf0")
        self.assertFalse(result.executed)

    def test_add_doca_port_skips_without_iface_type(self):
        result = nvovs.add_doca_port(
            "br-int",
            "p0",
            dry_run=True,
            command_exists_fn=lambda n: True,
            datapath_types=["doca"],
            iface_types=["system"],
        )
        self.assertEqual(result.status, "skipped")

    def test_dump_ovs_state(self):
        runner = FakeRunner(
            {
                ("ovs-vsctl", "show"): "Bridge br-int\n",
                ("ovs-appctl", "dpctl/show"): "netdev@ovs-netdev:\n",
                **_cap_stdout(),
            }
        )
        result = nvovs.dump_ovs_state(
            runner=runner,
            command_exists_fn=lambda n: n in ("ovs-vsctl", "ovs-appctl"),
            dry_run=False,
        )
        self.assertIn("br-int", result.details["show"] or "")
        self.assertFalse(result.destructive)


if __name__ == "__main__":
    unittest.main()
