"""DPU-side NVIDIA VF-to-representor mapping (fixtures, no SSH)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config, representor_mapping_settings
from plugins.nvidia_bluefield.discovery import map_dpu_vf_representors

FIXTURES = ROOT / "tests" / "fixtures" / "nvidia_bluefield"
IP_LINK = (FIXTURES / "dpu_ip_d_link_show.txt").read_text(encoding="utf-8")
DEVLINK = (FIXTURES / "dpu_devlink_port_show.txt").read_text(encoding="utf-8")

_SPEC = importlib.util.spec_from_file_location(
    "odse_vf_to_representor_map",
    ROOT / "tools" / "topology-discovery" / "vf_to_representor_map.py",
)
mapper = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = mapper
_SPEC.loader.exec_module(mapper)


class DpuSideRepresentorMappingTests(unittest.TestCase):
    def test_profile_defaults_to_dpu_nvidia_portname(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        settings = representor_mapping_settings(cfg)
        self.assertEqual(settings["location"], "dpu")
        self.assertEqual(settings["strategy"], "nvidia-portname")
        self.assertTrue(settings["require_high_confidence"])

    def test_maps_pf0vf0_and_pf0vf1_high_confidence(self):
        doc = map_dpu_vf_representors(
            ip_link=IP_LINK,
            devlink_port=DEVLINK,
            vf_indexes=(0, 1),
        )
        by_idx = {row["vf_index"]: row for row in doc["mappings"]}
        self.assertEqual(by_idx[0]["dpu_representor_name"], "pf0vf0")
        self.assertEqual(by_idx[0]["dpu_representor_portname"], "pf0vf0")
        self.assertEqual(by_idx[0]["switchid"], "7cfe90abcdef")
        self.assertEqual(by_idx[0]["confidence"], "high")
        self.assertEqual(by_idx[0]["mapping_source"], "devlink_pcivf+nvidia-portname")
        self.assertEqual(by_idx[1]["dpu_representor_name"], "pf0vf1")
        self.assertEqual(by_idx[1]["confidence"], "high")
        self.assertEqual(doc["location"], "dpu")

    def test_eth0v1_is_not_a_representor(self):
        doc = map_dpu_vf_representors(
            ip_link=IP_LINK,
            devlink_port=DEVLINK,
            vf_indexes=(0, 1),
        )
        names = [row.get("representor_netdev") for row in doc["mappings"]]
        self.assertNotIn("eth0v1", names)
        self.assertNotIn("enp4s0f0v0", names)
        self.assertNotIn("pf0sf0", names)

    def test_pf1vf0_is_not_used_for_pf0_vf_index_0(self):
        doc = map_dpu_vf_representors(
            ip_link=IP_LINK,
            devlink_port=DEVLINK,
            vf_indexes=(0,),
        )
        self.assertEqual(doc["mappings"][0]["representor_netdev"], "pf0vf0")
        self.assertNotEqual(doc["mappings"][0]["representor_netdev"], "pf1vf0")

    def test_maps_pf0vf0_from_packed_single_line_ip_link_output(self):
        # Regression: real `ip -d link show` packs parentdev/portname/
        # switchid onto the same physical line as `link/ether ...`
        # (confirmed on real host-4 E2100 hardware -- see
        # example-output/host-4/Step1.txt and
        # plugins/intel_ipu/discovery.py), unlike the one-attribute-
        # per-line style the other fixtures here use. Both must map
        # identically.
        packed_ip_link = (FIXTURES / "dpu_ip_d_link_show_packed.txt").read_text(
            encoding="utf-8"
        )
        doc = map_dpu_vf_representors(
            ip_link=packed_ip_link,
            devlink_port=DEVLINK,
            vf_indexes=(0,),
        )
        self.assertEqual(doc["mappings"][0]["dpu_representor_name"], "pf0vf0")
        self.assertEqual(doc["mappings"][0]["switchid"], "7cfe90abcdef")
        self.assertEqual(doc["mappings"][0]["confidence"], "high")

    def test_parse_ip_link_phys_reads_packed_single_line_attrs(self):
        packed_ip_link = (FIXTURES / "dpu_ip_d_link_show_packed.txt").read_text(
            encoding="utf-8"
        )
        by_name = mapper.parse_ip_link_phys(packed_ip_link)
        self.assertEqual(by_name["p0"]["phys_switch_id"], "7cfe90abcdef")
        self.assertEqual(by_name["p0"]["portname"], "p0")
        self.assertEqual(by_name["pf0vf0"]["phys_port_name"], "pf0vf0")

    def test_dry_run_without_samples_does_not_guess(self):
        doc = map_dpu_vf_representors(vf_indexes=(0, 1))
        for row in doc["mappings"]:
            self.assertIsNone(row["representor_netdev"])
            self.assertIsNone(row["confidence"])
            self.assertIn("dry-run", row["no_representor_reason"])

    def test_cli_location_dpu_with_fixtures(self):
        out, err = io.StringIO(), io.StringIO()
        argv = [
            "--config",
            str(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"),
            "--location",
            "dpu",
            "--json",
            "--ip-link-file",
            str(FIXTURES / "dpu_ip_d_link_show.txt"),
            "--devlink-file",
            str(FIXTURES / "dpu_devlink_port_show.txt"),
        ]
        with redirect_stdout(out), redirect_stderr(err):
            rc = mapper.main(argv)
        self.assertEqual(rc, 0, err.getvalue())
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["location"], "dpu")
        by_idx = {row["vf_index"]: row for row in doc["mappings"]}
        self.assertEqual(by_idx[0]["representor_netdev"], "pf0vf0")
        self.assertEqual(by_idx[1]["representor_netdev"], "pf0vf1")
        self.assertEqual(by_idx[0]["confidence"], "high")


if __name__ == "__main__":
    unittest.main()
