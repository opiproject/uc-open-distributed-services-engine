"""OPI lifecycle/inventory gateway (no hardware, no grpcio)."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.commands import CommandResult  # noqa: E402
from plugins.base.models import STATUS_OK, OperationResult, PhysicalFunction  # noqa: E402
from plugins.base.opi_lifecycle import (  # noqa: E402
    INIT_GAP,
    PING_REACHED,
    PING_UNCONFIGURED,
    SETNUMVFS_LIMITATION,
    get_devices,
    get_inventory,
    init_xpu,
    ping,
    set_num_vfs,
)

_SERVER_SPEC = importlib.util.spec_from_file_location(
    "odse_opi_server", ROOT / "tools" / "opi_server.py"
)
opi_server = importlib.util.module_from_spec(_SERVER_SPEC)
assert _SERVER_SPEC.loader is not None
_SERVER_SPEC.loader.exec_module(opi_server)

LSPCI = (
    "0000:0a:00.0 Ethernet controller [0200]: Intel Corporation "
    "Infrastructure Processing Unit [8086:1452] (rev 01)\n"
    "not a pci line\n"
)


def _reader(path: str):
    if path.endswith("sriov_numvfs"):
        return "0"
    if path.endswith("sriov_totalvfs"):
        return "8"
    return None


class OpiLifecycleTests(unittest.TestCase):
    def test_get_devices_uses_netdev_and_skips_placeholders(self):
        call = get_devices(
            {
                "host": {"name": "<HOST_NAME>"},
                "dpu": {"pf_name": "<IPU_PF_NETDEV>", "pf_pci": "0000:0a:00.0"},
            },
            physical_functions=[
                PhysicalFunction(netdev="enp10s0f0", pci_address="0000:0a:00.0"),
            ],
        )
        self.assertEqual(call.http_status, 200)
        self.assertEqual(set(call.body["devices"]), {"enp10s0f0", "0000:0a:00.0"})
        self.assertNotIn("<IPU_PF_NETDEV>", call.body["devices"])
        self.assertNotIn("health", call.body["devices"]["enp10s0f0"])
        self.assertNotIn("topology", call.body["devices"]["enp10s0f0"])
        self.assertEqual(list(call.not_opi), ["representor", "offload"])

    def test_get_inventory_parses_lspci_pci_slice_only(self):
        call = get_inventory(lspci_text=LSPCI)
        self.assertEqual(call.body["pci"][0]["address"], "0000:0a:00.0")
        self.assertEqual(call.body["pci"][0]["vendor"], "8086")
        self.assertEqual(call.body["pci"][0]["product"], "1452")
        self.assertNotIn("bios", call.body)

    def test_set_num_vfs_dry_run_does_not_claim_a_write(self):
        writes = []

        def writer(path, content, dry_run=True):
            writes.append((path, content, dry_run))
            return CommandResult(argv=["write_file", path], executed=False, dry_run=True)

        call = set_num_vfs(
            {"vendor": {"type": "nvidia-bluefield"}, "dpu": {"pf_name": "ens1f0np0"}},
            2,
            dry_run=True,
            read_file_fn=_reader,
            write_file_fn=writer,
        )
        self.assertEqual(call.http_status, 200)
        self.assertEqual(call.body, {"vfCnt": 2})
        self.assertNotIn("limitation", call.body)
        self.assertEqual(call.limitation, SETNUMVFS_LIMITATION)
        self.assertFalse(call.executed)
        self.assertTrue(writes)
        self.assertTrue(all(item[2] is True for item in writes))

    def test_set_num_vfs_refuses_amd(self):
        call = set_num_vfs(
            {"vendor": {"type": "amd-pensando"}, "dpu": {"pf_name": "ionic0"}},
            2,
            dry_run=False,
        )
        self.assertEqual(call.http_status, 501)
        self.assertEqual(call.body["code"], "UNIMPLEMENTED")
        self.assertFalse(call.executed)

    def test_set_num_vfs_requires_pf_name(self):
        call = set_num_vfs({"vendor": {"type": "intel-ipu"}, "dpu": {"pf_name": "<IPU_PF_NETDEV>"}}, 2)
        self.assertEqual(call.http_status, 400)
        self.assertIn("device id", call.body["message"])

    def test_ping_requires_a_live_management_check(self):
        missing = ping({})
        self.assertFalse(missing.body["healthy"])
        self.assertEqual(missing.limitation, PING_UNCONFIGURED)
        reached = ping(
            config={"dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"}},
            probe=lambda config: (True, PING_REACHED),
        )
        self.assertTrue(reached.body["healthy"])
        self.assertEqual(reached.limitation, PING_REACHED)
        down = ping(
            config={"dpu": {"management_ip": "192.0.2.20", "ssh_user": "root"}},
            probe=lambda config: (False, "SSH to 192.0.2.20 failed: timed out"),
        )
        self.assertFalse(down.body["healthy"])
        self.assertIn("timed out", down.limitation or "")

    def test_init_is_unimplemented(self):
        recorded = init_xpu(
            {"dpu": {"management_ip": "192.0.2.20", "ssh_port": 22}},
            {"dpuMode": True},
        )
        self.assertEqual(recorded.http_status, 501)
        self.assertEqual(recorded.body["code"], "UNIMPLEMENTED")
        self.assertEqual(recorded.limitation, INIT_GAP)
        self.assertNotIn("ip", recorded.body)
        self.assertFalse(recorded.executed)


class OpiGatewayTests(unittest.TestCase):
    def test_http_get_devices_and_ping(self):
        def discover(config):
            return [PhysicalFunction(netdev="ens1f0np0", pci_address="0000:0f:00.0")], None

        gateway = opi_server.OpiGateway(
            {
                "vendor": {"type": "nvidia-bluefield"},
                "dpu": {"pf_name": "odse-no-such-pf"},
                "host": {"name": "host2"},
            },
            dry_run=True,
            discover_fn=discover,
            ping_probe=lambda config: (True, PING_REACHED),
        )
        server = opi_server.ThreadingHTTPServer(
            ("127.0.0.1", 0), opi_server.make_handler(gateway)
        )
        try:
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with urllib.request.urlopen("http://127.0.0.1:%s/v1/lifecycle/devices" % port) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertIn("ens1f0np0", body["devices"])
                self.assertNotIn("odse-no-such-pf", body["devices"])
                self.assertTrue(response.headers["X-Odse-Opi-Service"].endswith("GetDevices"))
            with urllib.request.urlopen("http://127.0.0.1:%s/v1/lifecycle/heartbeat/ping" % port) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertTrue(body["healthy"])
                self.assertEqual(response.headers["X-Odse-Opi-Limitation"], PING_REACHED)
            request = urllib.request.Request(
                "http://127.0.0.1:%s/v1/lifecycle/devices/vfs" % port,
                data=json.dumps({"vfCnt": 2}).encode("utf-8"),
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("SetNumVfsRequest", caught.exception.headers["X-Odse-Opi-Limitation"])
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_prepare_vfs_records_opi_body_without_a_second_write(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("odse_labctl_opi", ROOT / "tools" / "labctl.py")
        labctl = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(labctl)

        class Backend:
            def configure_sriov(self, pf=None, numvfs=None, dry_run=True):
                return OperationResult(
                    operation="configure_sriov",
                    vendor="nvidia",
                    status=STATUS_OK,
                    message="planned",
                    executed=False,
                )

        class Args:
            dry_run = True
            apply = False

        ctx = labctl.StageContext(
            Args(),
            {"dpu": {"pf_name": "ens1f0np0", "num_vfs": 2}},
            "nvidia-bluefield",
            Backend(),
            Path("."),
            "run-1",
        )
        ctx.dry_run = True
        ctx.apply_allowed = False
        payload = labctl.stage_prepare_vfs(ctx)
        self.assertEqual(payload["opi_set_num_vfs"]["body"], {"vfCnt": 2})
        self.assertEqual(payload["opi_set_num_vfs"]["limitation"], SETNUMVFS_LIMITATION)
        self.assertNotIn("representor", payload["opi_set_num_vfs"]["body"])


if __name__ == "__main__":
    unittest.main()
