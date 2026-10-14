"""DPU-side offload validation plans DPU OVS probes, not host ovs-vsctl."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.nvidia_bluefield.backend import NvidiaBlueFieldBackend

CHECKER_PATH = ROOT / "tools" / "offload-checker" / "ovs_offload_checker.py"
_SPEC = importlib.util.spec_from_file_location("odse_ovs_offload_checker_dpu", CHECKER_PATH)
checker = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"

DUMP_SAMPLE = (
    "ufid:11111111-1111-1111-1111-111111111111, recirc_id(0),in_port(1),"
    "eth_type(0x0800),ipv4(src=198.51.100.11,dst=198.51.100.12), packets:10, bytes:1000, "
    "offloaded:yes, dp:tc\n"
)


class DpuSideOffloadValidationTests(unittest.TestCase):
    def test_backend_plans_dpu_probes_and_does_not_claim_offload(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        result = NvidiaBlueFieldBackend(lab_config=cfg).validate_offload()
        self.assertEqual(result.details.get("location"), "dpu")
        self.assertIsNone(result.details.get("offload_state"))
        self.assertIsNone(result.details.get("offloaded"))
        self.assertFalse(result.executed)
        joined = " ".join(" ".join(c.argv or []) for c in result.commands)
        self.assertIn("ovs-vsctl", joined)
        self.assertIn("dpctl/dump-flows", joined)
        self.assertTrue(any("ssh" in (c.argv or []) for c in result.commands))
        self.assertIn("No fake", result.message)

    def test_checker_dpu_without_samples_does_not_use_host_ovs(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        report = checker.build_report(
            interfaces=["pf0vf0"],
            pf=None,
            vendor="nvidia",
            flow=None,
            bridge="br-int",
            more=True,
            datapath=None,
            duration=0.0,
            timeout=5,
            plugins=[],
            location="dpu",
            config=cfg,
        )
        self.assertEqual(report["location"], "dpu")
        self.assertIsNotNone(report["dpu_evidence"])
        self.assertIn("Host OVS is not the forwarding owner", report["host_evidence"]["note"])
        self.assertTrue(report["ovs_vsctl_show"].get("planned"))
        argv = report["ovs_vsctl_show"]["command"]["argv"]
        self.assertIn("ssh", argv)
        self.assertEqual(report["dpu_evidence"]["dpctl_dump_flows"].get("flows"), [])
        self.assertEqual(report["assessment"]["status"], "unknown")

    def test_checker_dpu_samples_fill_dpu_evidence_only(self):
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
            samples={
                "ovs_vsctl_show": "Bridge br-int\n    Port pf0vf0\n",
                "dpctl_show": "netdev@ovs-netdev:\n",
                "dpctl_dump_flows": DUMP_SAMPLE,
                "ofctl_dump_flows": " cookie=0x0, table=0, n_packets=1 actions=NORMAL\n",
            },
            forwarding_result={
                "control_entity": "labctl -> nvidia backend -> RemoteCommandRunner -> DPU ovs-vsctl",
                "forwarding_entity": "NVIDIA BlueField DPU OVS/OVS-DOCA",
                "vf_pci_addresses": ["0000:03:00.2"],
                "representors": ["pf0vf0"],
            },
        )
        self.assertEqual(report["location"], "dpu")
        self.assertIn("pf0vf0", "\n".join(report["dpu_evidence"]["ovs_vsctl_show"]["stdout_lines"]))
        self.assertEqual(report["vm_evidence"]["representors"], ["pf0vf0"])
        self.assertIsNone(report["traffic_evidence"])
        self.assertEqual(report["offload_confidence"]["status"], report["assessment"]["status"])
        self.assertFalse(report["dpu_evidence"]["ovs_vsctl_show"].get("planned"))


if __name__ == "__main__":
    unittest.main()
