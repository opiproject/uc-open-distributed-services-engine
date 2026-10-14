"""Intel P4Runtime/IPDK client from tests/fixtures/intel_ipu (no gRPC)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.intel_ipu.p4runtime import (
    MODE_CLI,
    MODE_GRPC,
    P4RuntimeClient,
    probe_pipeline_capability,
)
from tests.vendor.harness import FIXTURES


def _load(name: str) -> dict:
    return json.loads((FIXTURES / "intel_ipu" / name).read_text(encoding="utf-8"))


class IntelP4RuntimeFixtureTests(unittest.TestCase):
    def test_mock_from_config_fixture_reports_pipeline_configured(self):
        client = P4RuntimeClient.from_config(_load("p4_config.json"))
        result = client.get_forwarding_pipeline_config()
        self.assertTrue(result.details["pipeline_configured"])
        self.assertTrue(result.capabilities.p4_native)

    def test_mock_no_pipeline_configured_is_skipped_not_false(self):
        client = P4RuntimeClient(mode="mock")
        result = client.get_forwarding_pipeline_config()
        self.assertFalse(result.details["pipeline_configured"])
        self.assertIsNone(result.capabilities.p4_native)

    def test_write_and_delete_table_entry_mock(self):
        client = P4RuntimeClient(mode="mock")
        written = client.write_table_entry({"id": "e1", "action": "forward"})
        self.assertTrue(written.executed)
        deleted = client.delete_table_entry("e1")
        self.assertTrue(deleted.executed)
        missing = client.delete_table_entry("e1")
        self.assertEqual(missing.status, "skipped")

    def test_read_counters_no_fake_zeros(self):
        client = P4RuntimeClient.from_config(_load("p4_config.json"))
        result = client.read_counters("vm1-vm2-fwd")
        self.assertEqual(len(result.telemetry), 1)
        self.assertEqual(result.telemetry[0].value, 128.0)

        empty = client.read_counters("unknown-entry")
        self.assertEqual(empty.telemetry, [])

    def test_grpc_mode_is_placeholder_does_not_send(self):
        client = P4RuntimeClient(mode=MODE_GRPC, endpoint="infrap4d.example.test:9339")
        result = client.get_forwarding_pipeline_config()
        self.assertFalse(result.executed)
        self.assertFalse(result.details["grpc_attempted"])

    def test_probe_pipeline_capability_true_only_for_mock_with_config(self):
        self.assertTrue(probe_pipeline_capability(_load("p4_config.json")))
        self.assertFalse(probe_pipeline_capability({}))
        self.assertFalse(
            probe_pipeline_capability({"p4_mode": "grpc", "p4_endpoint": "x:9339"})
        )


class IntelP4RuntimeCliModeTests(unittest.TestCase):
    """cli mode shells the real p4rt-ctl over SSH (ipdk-io/ipu-playbook's
    mechanism) -- these tests stay dry_run=True (planned only), no SSH."""

    def _client(self, **acc_ssh_overrides):
        acc_ssh = {"host": "acc.example.invalid", "user": "root"}
        acc_ssh.update(acc_ssh_overrides)
        return P4RuntimeClient(mode=MODE_CLI, device_id="br0", acc_ssh=acc_ssh, dry_run=True)

    def test_get_pipe_planned_only_by_default(self):
        client = self._client()
        result = client.get_forwarding_pipeline_config()
        self.assertFalse(result.executed)
        self.assertEqual(result.commands[0].argv[-3:], ["p4rt-ctl", "get-pipe", "br0"])
        self.assertTrue(result.commands[0].dry_run)

    def test_get_pipe_without_acc_ssh_configured_is_skipped_not_false(self):
        client = P4RuntimeClient(mode=MODE_CLI, acc_ssh={}, dry_run=False)
        result = client.get_forwarding_pipeline_config()
        self.assertFalse(result.executed)
        self.assertIsNone(result.details["pipeline_configured"])

    def test_add_entry_requires_table_and_flow(self):
        client = self._client()
        result = client.write_table_entry({"id": "e1"})
        self.assertEqual(result.status, "error")
        self.assertFalse(result.executed)

    def test_add_entry_builds_p4rt_ctl_argv(self):
        client = self._client()
        result = client.write_table_entry(
            {"id": "e1", "table": "ingress.ipv4_host", "flow": "hdr.ipv4.dst_addr=1.1.1.1,action=ingress.send(0)"}
        )
        self.assertEqual(
            result.commands[0].argv[-5:],
            [
                "p4rt-ctl",
                "add-entry",
                "br0",
                "ingress.ipv4_host",
                "hdr.ipv4.dst_addr=1.1.1.1,action=ingress.send(0)",
            ],
        )

    def test_del_entry_is_not_implemented_not_guessed(self):
        client = self._client()
        result = client.delete_table_entry("e1")
        self.assertEqual(result.status, "not_implemented")
        self.assertFalse(result.executed)

    def test_dump_entries_builds_p4rt_ctl_argv(self):
        client = self._client()
        result = client.read_counters("e1")
        self.assertEqual(result.commands[0].argv[-3:], ["p4rt-ctl", "dump-entries", "br0"])
        self.assertEqual(result.telemetry, [])  # unparsed output, no fabricated counters

    def test_from_lab_config_reads_dpu_block_for_acc_ssh_and_remote_execution_gate(self):
        cfg = {
            "p4": {"mode": "cli", "device_id": "br0"},
            "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root", "ssh_port": 22},
            "remote_execution": {"dry_run_default": False},
        }
        client = P4RuntimeClient.from_lab_config(cfg)
        self.assertEqual(client.mode, MODE_CLI)
        self.assertFalse(client.dry_run)
        self.assertEqual(client.acc_ssh["host"], "192.0.2.20")
        self.assertEqual(client.acc_ssh["user"], "root")

    def test_from_lab_config_defaults_dry_run_true_when_remote_execution_missing(self):
        cfg = {"p4": {"mode": "cli"}, "dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"}}
        client = P4RuntimeClient.from_lab_config(cfg)
        self.assertTrue(client.dry_run)


if __name__ == "__main__":
    unittest.main()
