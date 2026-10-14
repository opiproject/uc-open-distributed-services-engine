"""ovs_offload_checker.py --location dpu: _dpu_probe_section() must honor
remote_execution.dry_run_default (same knob vf_to_representor_map.py's
collect_dpu_samples_via_ssh() honors), not silently stay planned forever
and not silently start real SSH the moment a config/host/user exist."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "tools" / "offload-checker" / "ovs_offload_checker.py"
_SPEC = importlib.util.spec_from_file_location("odse_ovs_offload_checker_dpu_apply", CHECKER_PATH)
checker = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.models import CommandResult


class _FakeRemote:
    """Stands in for plugins.base.remote.RemoteCommandRunner."""

    def __init__(self, *, returncode=0, stdout="ok", timed_out=False):
        self.calls = []
        self._returncode = returncode
        self._stdout = stdout
        self._timed_out = timed_out

    def run_ssh(self, host, user, command, key_path=None, port=22, dry_run=True, timeout=None, reason=None, sudo=False):
        self.calls.append({"argv": list(command), "dry_run": dry_run, "timeout": timeout})
        return CommandResult(
            argv=["ssh", f"{user}@{host}", "--", *command],
            executed=not dry_run,
            dry_run=dry_run,
            returncode=None if (dry_run or self._timed_out) else self._returncode,
            stdout=None if dry_run else self._stdout,
            stderr=None,
            reason=reason,
            timed_out=(not dry_run) and self._timed_out,
        )


class DpuProbeSectionApplyTests(unittest.TestCase):
    def test_default_dry_run_stays_planned_even_with_host_and_user_configured(self):
        config = {"dpu": {"management_ip": "172.22.3.2", "ssh_user": "root"}}
        section = checker._dpu_probe_section(["ovs-vsctl", "show"], config)
        self.assertTrue(section["planned"])
        self.assertIsNone(section["command"]["stdout"])

    def test_dry_run_default_false_actually_executes(self):
        config = {
            "dpu": {"management_ip": "172.22.3.2", "ssh_user": "root", "command_timeout_seconds": 45},
            "remote_execution": {"dry_run_default": False},
        }
        fake = _FakeRemote(returncode=0, stdout="Bridge br-int\n    Port pf0vf0\n")
        with patch("plugins.base.remote.RemoteCommandRunner", return_value=fake):
            section = checker._dpu_probe_section(["ovs-vsctl", "show"], config)
        self.assertFalse(section["planned"])
        self.assertTrue(section["command"]["available"])
        self.assertEqual(section["command"]["returncode"], 0)
        self.assertIn("pf0vf0", section["command"]["stdout"])
        self.assertIsNone(section["command"]["error"])
        self.assertEqual(fake.calls[0]["timeout"], 45)

    def test_dry_run_default_false_timeout_is_reported_as_error(self):
        config = {
            "dpu": {"management_ip": "172.22.3.2", "ssh_user": "root", "command_timeout_seconds": 30},
            "remote_execution": {"dry_run_default": False},
        }
        fake = _FakeRemote(timed_out=True)
        with patch("plugins.base.remote.RemoteCommandRunner", return_value=fake):
            section = checker._dpu_probe_section(["ovs-appctl", "dpctl/show"], config)
        self.assertFalse(section["planned"])
        self.assertFalse(section["command"]["available"])
        self.assertIn("timed out", section["command"]["error"])

    def test_dry_run_default_false_nonzero_returncode_is_error_not_available(self):
        config = {
            "dpu": {"management_ip": "172.22.3.2", "ssh_user": "root"},
            "remote_execution": {"dry_run_default": False},
        }
        fake = _FakeRemote(returncode=1, stdout="")
        with patch("plugins.base.remote.RemoteCommandRunner", return_value=fake):
            section = checker._dpu_probe_section(["ovs-vsctl", "show"], config)
        self.assertFalse(section["command"]["available"])
        self.assertIn("failed", section["command"]["error"])

    def test_dump_flows_parsed_from_real_dpu_stdout(self):
        dump_sample = (
            "ufid:11111111-1111-1111-1111-111111111111, recirc_id(0),in_port(1),"
            "eth_type(0x0800),ipv4(src=198.51.100.11,dst=198.51.100.12), packets:10, "
            "bytes:1000, offloaded:yes, dp:tc\n"
        )
        config = {
            "dpu": {"management_ip": "172.22.3.2", "ssh_user": "root"},
            "remote_execution": {"dry_run_default": False},
        }
        fake = _FakeRemote(returncode=0, stdout=dump_sample)
        with patch("plugins.base.remote.RemoteCommandRunner", return_value=fake):
            report = checker.build_report(
                interfaces=["pf0vf0"],
                pf=None,
                vendor="nvidia",
                flow=None,
                bridge="br-int",
                more=False,
                datapath=None,
                duration=0.0,
                timeout=5,
                plugins=[],
                location="dpu",
                config=config,
            )
        flows = report["dpu_evidence"]["dpctl_dump_flows"]["flows"]
        self.assertEqual(len(flows), 1)
        self.assertFalse(report["dpu_evidence"]["dpctl_dump_flows"]["planned"])

    def test_tc_and_ethtool_shapes_match_what_assess_offload_expects(self):
        # Real bug found via example-output/host-2/19.txt: DPU-mode tc/
        # ethtool sections used to be a flat list of raw _dpu_probe_section
        # dicts with no "interface"/"ingress"/"egress"/"highlighted_counters"
        # keys, so assess_offload() could never see real tc/ethtool content
        # (a pf0vf1 filter marked not_in_hw was silently invisible).
        not_in_hw_filter = (
            "filter protocol ip pref 3 flower chain 0 handle 0x1\n"
            "  not_in_hw\n"
        )
        config = {
            "dpu": {"management_ip": "172.22.3.2", "ssh_user": "root"},
            "remote_execution": {"dry_run_default": False},
        }
        fake = _FakeRemote(returncode=0, stdout=not_in_hw_filter)
        with patch("plugins.base.remote.RemoteCommandRunner", return_value=fake):
            report = checker.build_report(
                interfaces=["pf0vf1"],
                pf=None,
                vendor="nvidia",
                flow=None,
                bridge="br-int",
                more=False,
                datapath=None,
                duration=0.0,
                timeout=5,
                plugins=[],
                location="dpu",
                config=config,
            )
        tc_evidence = report["dpu_evidence"]["tc_filters"]["interfaces"][0]
        self.assertEqual(tc_evidence["interface"], "pf0vf1")
        self.assertIn("ingress", tc_evidence)
        self.assertIn("egress", tc_evidence)
        self.assertEqual(tc_evidence["ingress"]["counts"]["software"], 1)
        eth_evidence = report["dpu_evidence"]["ethtool_stats"]["interfaces"][0]
        self.assertEqual(eth_evidence["interface"], "pf0vf1")
        self.assertIn("highlighted_counters", eth_evidence)
        # Both directions (ingress + egress) must actually be probed over
        # SSH, not just ingress, for parity with the host-mode collector.
        tc_calls = [c for c in fake.calls if c["argv"][:3] == ["tc", "-s", "filter"]]
        self.assertEqual(len(tc_calls), 2)


if __name__ == "__main__":
    unittest.main()
