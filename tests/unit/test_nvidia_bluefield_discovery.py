"""Tests for NVIDIA BlueField discovery parsers (fixture-only, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.nvidia_bluefield.backend import NvidiaBlueFieldBackend
from plugins.nvidia_bluefield.discovery import (
    build_inventory,
    discover,
    looks_like_nvidia,
    parse_devlink_port_show,
    parse_ip_d_link,
    parse_lspci_nn,
    parse_portname,
)

FIXTURES = ROOT / "tests" / "fixtures" / "nvidia_bluefield"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _sample_bundle():
    return {
        "lspci": _load("lspci.txt"),
        "devlink_dev": _load("devlink_dev.txt"),
        "devlink_port": _load("devlink_port.txt"),
        "ip_link": _load("ip_d_link.txt"),
        "ovs": _load("ovs_vsctl.txt"),
        "ethtool": {
            "p0": _load("ethtool_p0.txt"),
            "pf0vf0": _load("ethtool_pf0vf0.txt"),
            "eth0": _load("ethtool_ice.txt"),
        },
    }


class ParseTests(unittest.TestCase):
    def test_hints(self):
        self.assertTrue(looks_like_nvidia("Mellanox Technologies ConnectX-7"))
        self.assertTrue(looks_like_nvidia("NVIDIA BlueField-3"))
        self.assertTrue(looks_like_nvidia("driver: mlx5_core"))
        self.assertFalse(looks_like_nvidia("Intel Corporation Ethernet"))
        self.assertFalse(looks_like_nvidia(""))

    def test_portname_vf_sf_not_guessed_netdev(self):
        vf = parse_portname("pf0vf0")
        self.assertEqual(vf["kind"], "vf")
        self.assertEqual(vf["vf_index"], 0)
        sf = parse_portname("pf0sf0")
        self.assertEqual(sf["kind"], "sf")
        self.assertEqual(sf["sf_index"], 0)
        self.assertIsNone(sf["vf_index"])
        self.assertIsNone(parse_portname("eth0v1")["kind"])
        self.assertIsNone(parse_portname("enp4s0f0v0")["kind"])
        self.assertIsNone(parse_portname("p0")["kind"])

    def test_lspci_ignores_intel(self):
        rows = parse_lspci_nn(_load("lspci.txt"))
        pcis = [r["pci_address"] for r in rows]
        self.assertIn("0000:03:00.0", pcis)
        self.assertTrue(any(r["is_virtual_function"] for r in rows))
    def test_lspci_model_field_not_corrupted_by_pci_address_colon(self):
        # Regression: `_model_from_lspci` used to split on the first
        # bare ":" in the line, but the PCI address itself contains one
        # (`0d:00.0`), so `model` ended up with a garbage fragment like
        # "00.0 Ethernet controller [0200]: Mellanox ..." instead of just
        # the vendor+device description. Covers both domain-prefixed and
        # host-short address forms, plus a trailing "(rev NN)".
        rows = parse_lspci_nn(_load("lspci.txt"))
        row = next(r for r in rows if r["pci_address"] == "0000:03:00.0")
        self.assertEqual(
            row["model"],
            "Mellanox Technologies MT43244 BlueField-3 integrated "
            "ConnectX-7 network controller",
        )

        short_form_rows = parse_lspci_nn(
            "0d:00.0 Ethernet controller [0200]: Mellanox Technologies "
            "MT43244 BlueField-3 integrated ConnectX-7 network controller "
            "[15b3:a2dc] (rev 01)\n"
        )
        self.assertEqual(
            short_form_rows[0]["model"],
            "Mellanox Technologies MT43244 BlueField-3 integrated "
            "ConnectX-7 network controller",
        )

    def test_lspci_short_form_bluefield3_from_host(self):
        raw = (
            "0d:00.0 Ethernet controller [0200]: Mellanox Technologies "
            "MT43244 BlueField-3 integrated ConnectX-7 network controller "
            "[15b3:a2dc] (rev 01)\n"
            "0d:00.1 Ethernet controller [0200]: Mellanox Technologies "
            "MT43244 BlueField-3 integrated ConnectX-7 network controller "
            "[15b3:a2dc] (rev 01)\n"
            "0d:00.2 DMA controller [0801]: Mellanox Technologies MT43244 "
            "BlueField-3 SoC Management Interface [15b3:c2d5] (rev 01)\n"
        )
        rows = parse_lspci_nn(raw)
        pcis = [r["pci_address"] for r in rows]
        self.assertEqual(
            pcis,
            ["0000:0d:00.0", "0000:0d:00.1", "0000:0d:00.2"],
        )
        inv = build_inventory(
            lspci=raw,
            ethtool_by_iface={
                "enp13s0f0np0": "driver: mlx5_core\nbus-info: 0000:0d:00.0\n",
                "enp13s0f1np1": "driver: mlx5_core\nbus-info: 0000:0d:00.1\n",
            },
        )
        pf_pcis = [p.pci_address for p in inv.physical_functions]
        self.assertEqual(pf_pcis, ["0000:0d:00.0", "0000:0d:00.1"])
        self.assertEqual(inv.physical_functions[0].netdev, "enp13s0f0np0")
        self.assertEqual(len(inv.devices), 3)
        self.assertEqual(inv.representors, [])

    def test_ip_link_portname(self):
        ifaces = {i["netdev"]: i for i in parse_ip_d_link(_load("ip_d_link.txt"))}
        self.assertEqual(ifaces["pf0vf0"]["portname"], "pf0vf0")
        self.assertIsNone(ifaces["eth0v1"].get("portname"))

    def test_ip_link_packed_single_line_attrs(self):
        # Regression: real `ip -d link show` packs parentdev/portname/
        # switchid onto the same physical line as `link/ether ...`
        # (confirmed on real host-4 E2100 hardware -- see
        # example-output/host-4/Step1.txt and
        # plugins/intel_ipu/discovery.py), not one attribute per line
        # like the older ip_d_link.txt fixture assumes. Both styles must
        # parse correctly.
        ifaces = {
            i["netdev"]: i for i in parse_ip_d_link(_load("ip_d_link_packed.txt"))
        }
        self.assertEqual(ifaces["p0"]["parentdev"], "0000:03:00.0")
        self.assertEqual(ifaces["p0"]["portname"], "p0")
        self.assertEqual(ifaces["p0"]["phys_switch_id"], "7cfe90abcdef")
        self.assertEqual(ifaces["pf0vf0"]["parentdev"], "0000:03:00.0")
        self.assertEqual(ifaces["pf0vf0"]["portname"], "pf0vf0")
        self.assertIsNone(ifaces["eth0v1"].get("parentdev"))

    def test_devlink_pcivf(self):
        ports = parse_devlink_port_show(_load("devlink_port.txt"))
        vf = [p for p in ports if p["flavour"] == "pcivf"][0]
        self.assertEqual(vf["netdev"], "pf0vf0")
        self.assertEqual(vf["vfnum"], 0)
        self.assertEqual(vf["pci_address"], "0000:03:00.0")


class InventoryTests(unittest.TestCase):
    def test_build_from_fixtures(self):
        s = _sample_bundle()
        result = build_inventory(
            lspci=s["lspci"],
            devlink_dev=s["devlink_dev"],
            devlink_port=s["devlink_port"],
            ip_link=s["ip_link"],
            ethtool_by_iface=s["ethtool"],
            ovs=s["ovs"],
        )
        self.assertGreaterEqual(len(result.devices), 1)
        bf = next(d for d in result.devices if d.pci_address == "0000:03:00.0")
        self.assertEqual(bf.vendor, "nvidia")
        self.assertIn("BlueField", bf.model or "")
        self.assertIsNone(bf.serial_number)
        self.assertIn("32.41.1000", bf.firmware_version or "")
        self.assertIsNone(bf.capabilities.switchdev)
        self.assertIsNone(bf.capabilities.vdpa)
        self.assertIsNone(bf.capabilities.ovs_hw_offload)

        pf0 = next(p for p in result.physical_functions if p.pci_address == "0000:03:00.0")
        self.assertEqual(pf0.netdev, "p0")
        self.assertIsNone(pf0.eswitch_mode)

        self.assertTrue(any(v.pci_address == "0000:04:00.2" for v in result.virtual_functions))
        self.assertTrue(all(v.vf_index is None for v in result.virtual_functions))

        names = {r.netdev: r for r in result.representors}
        self.assertIn("pf0vf0", names)
        self.assertEqual(names["pf0vf0"].vf_index, 0)
        self.assertEqual(names["pf0vf0"].mapping_method, "devlink_pcivf")
        self.assertIn("pf0sf0", names)
        self.assertIsNone(names["pf0sf0"].vf_index)
        self.assertNotIn("eth0v1", names)
        self.assertNotIn("enp4s0f0v0", names)
        self.assertNotIn("p0", names)
        self.assertFalse(any(i == "eth0" for i in result.details["mlx5_interfaces"]))

    def test_ip_only_portname_without_devlink(self):
        result = build_inventory(ip_link=_load("ip_d_link.txt"))
        names = [r.netdev for r in result.representors]
        self.assertIn("pf0vf0", names)
        self.assertIn("pf0sf0", names)
        self.assertNotIn("eth0v1", names)
        self.assertEqual(
            next(r for r in result.representors if r.netdev == "pf0vf0").mapping_method,
            "portname",
        )

    def test_empty_is_skip_not_fake_device(self):
        result = build_inventory(lspci="00:00.0 Host bridge: Intel\n")
        self.assertEqual(result.devices, [])
        self.assertEqual(result.representors, [])

    def test_discover_samples_and_pf_filter(self):
        result = discover(samples=_sample_bundle())
        self.assertTrue(result.devices)
        filtered = discover(samples=_sample_bundle(), pf="p0")
        self.assertTrue(all(p.netdev == "p0" for p in filtered.physical_functions))
        none = discover(samples=_sample_bundle(), pf="does-not-exist")
        self.assertEqual(none.devices, [])
        self.assertEqual(none.representors, [])

    def test_backend_discover_uses_samples_path(self):
        backend = NvidiaBlueFieldBackend()
        self.assertEqual(backend.backend_id, "nvidia")
        # Live discover is allowed; may skip without tools. Must not invent devices.
        live = backend.discover()
        if live.status == "skipped":
            self.assertEqual(live.devices, [])


if __name__ == "__main__":
    unittest.main()
