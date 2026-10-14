"""Lab YAML loader and vendorctl --config (no hardware)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.lab_config import (  # noqa: E402
    config_dry_run,
    intel_plugin_config,
    load_lab_config,
    select_device,
)
from plugins.base.models import OperationResult  # noqa: E402

EXAMPLE = ROOT / "config" / "lab.example.yaml"

_SPEC = importlib.util.spec_from_file_location(
    "odse_vendorctl",
    ROOT / "tools" / "vendorctl.py",
)
vendorctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = vendorctl
_SPEC.loader.exec_module(vendorctl)


class LabExampleTests(unittest.TestCase):
    def test_example_parses(self):
        lab = load_lab_config(EXAMPLE)
        self.assertEqual(lab["schema"], "odse-lab-config/v1alpha1")
        self.assertTrue(config_dry_run(lab))
        nvidia = select_device(lab, "nvidia-bluefield")
        self.assertIsNotNone(nvidia)
        self.assertEqual(nvidia["management_ip"], "192.0.2.10")
        self.assertEqual(nvidia["pfs"][0]["name"], "<PF_NETDEV>")
        self.assertIsNone(nvidia["pfs"][0]["expected_vfs"])
        self.assertEqual(nvidia["bridges"][0]["datapath_type"], "system")
        self.assertFalse(nvidia["vdpa"]["enabled"])
        intel = select_device(lab, "intel-ipu", host_id="compute-01")
        cfg = intel_plugin_config(intel)
        self.assertEqual(cfg["redfish_base"], "https://192.0.2.20")
        self.assertEqual(cfg["opi_endpoint"], "https://opi.example.invalid:443")
        amd = select_device(lab, "amd")
        self.assertEqual(amd["id"], "amd-dsc-0")
        marvell = select_device(lab, "marvell-octeon")
        self.assertEqual(marvell["bridges"][0]["datapath_type"], "netdev")


class VendorctlConfigTests(unittest.TestCase):
    def test_example_blocks_apply(self):
        fake = MagicMock()
        fake.discover.return_value = OperationResult(
            operation="discover",
            vendor="nvidia",
            status="ok",
            message="fixture",
        )
        with patch.object(vendorctl, "load_backend", return_value=fake):
            rc = vendorctl.main(
                [
                    "discover",
                    "--vendor",
                    "nvidia-bluefield",
                    "--config",
                    str(EXAMPLE),
                    "--apply",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        fake.discover.assert_called_once()

    def test_placeholder_pf_not_used(self):
        args = vendorctl.build_parser().parse_args(
            [
                "configure-sriov",
                "--vendor",
                "nvidia-bluefield",
                "--config",
                str(EXAMPLE),
            ]
        )
        lab = load_lab_config(EXAMPLE)
        device = select_device(lab, "nvidia-bluefield")
        vendorctl._apply_lab_defaults(args, device)
        self.assertIsNone(args.pf)
        self.assertIsNone(args.num_vfs)
        self.assertEqual(args.bridge, "br-int")
        self.assertEqual(args.datapath_type, "system")
