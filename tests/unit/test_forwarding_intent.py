"""ForwardingIntent build/validate + tools/forwarding_intent.py CLI (no hardware)."""

from __future__ import annotations

import copy
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import (  # noqa: E402
    build_forwarding_intent,
    load_vm2vm_config,
    validate_forwarding_section,
)

_SPEC = importlib.util.spec_from_file_location(
    "odse_forwarding_intent",
    ROOT / "tools" / "forwarding_intent.py",
)
forwarding_intent_cli = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = forwarding_intent_cli
_SPEC.loader.exec_module(forwarding_intent_cli)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class BuildForwardingIntentTests(unittest.TestCase):
    def setUp(self):
        self.config = load_vm2vm_config(NVIDIA_EXAMPLE)

    def test_two_endpoint_intent_is_valid(self):
        issues = validate_forwarding_section(self.config)
        self.assertEqual(issues, [])

    def test_nvidia_dpu_mode_intent_uses_doca_datapath(self):
        intent = build_forwarding_intent(self.config)
        self.assertEqual(intent.name, "odse-vm2vm-nvidia")
        self.assertEqual(intent.type, "l2-vm-to-vm")
        self.assertEqual(intent.mode, "bridge")
        self.assertEqual(intent.bridge, "br-int")
        self.assertEqual(intent.details.get("datapath_type"), "doca")
        self.assertEqual(len(intent.endpoints), 2)
        self.assertTrue(intent.policy.bidirectional)
        self.assertTrue(intent.offload.requested)

    def test_generic_system_datapath_when_preferred_datapath_is_system(self):
        cfg = copy.deepcopy(self.config)
        cfg["dpu"]["preferred_datapath"] = "system"
        cfg["ovs"]["datapath_type"] = "system"
        intent = build_forwarding_intent(cfg)
        self.assertEqual(intent.details.get("datapath_type"), "system")
        self.assertEqual(intent.bridge, "br-int")

    def test_missing_endpoint_fails_validation(self):
        cfg = copy.deepcopy(self.config)
        cfg["forwarding"]["endpoints"] = cfg["forwarding"]["endpoints"][:1]
        issues = validate_forwarding_section(cfg)
        self.assertTrue(any("at least 2 entries" in issue for issue in issues))

    def test_three_endpoint_l2_segment_is_structurally_valid(self):
        cfg = copy.deepcopy(self.config)
        cfg["forwarding"]["type"] = "l2-segment"
        third = copy.deepcopy(cfg["forwarding"]["endpoints"][1])
        third["name"] = "vm3-endpoint"
        third["vm"] = "odse-vm2vm-vm1"  # reuse an existing vm name; vf_index matches vm1
        third["vf_index"] = 0
        cfg["forwarding"]["endpoints"].append(third)
        issues = validate_forwarding_section(cfg)
        self.assertEqual(issues, [])
        intent = build_forwarding_intent(cfg)
        self.assertEqual(intent.type, "l2-segment")
        self.assertEqual(len(intent.endpoints), 3)

    def test_endpoint_vf_index_mismatch_is_rejected(self):
        cfg = copy.deepcopy(self.config)
        cfg["forwarding"]["endpoints"][0]["vf_index"] = 1
        issues = validate_forwarding_section(cfg)
        self.assertTrue(any("does not match" in issue for issue in issues))

    def test_endpoint_unknown_vm_is_rejected(self):
        cfg = copy.deepcopy(self.config)
        cfg["forwarding"]["endpoints"][0]["vm"] = "does-not-exist"
        issues = validate_forwarding_section(cfg)
        self.assertTrue(any("does not match any vms" in issue for issue in issues))

    def test_missing_forwarding_section_is_rejected(self):
        cfg = copy.deepcopy(self.config)
        del cfg["forwarding"]
        issues = validate_forwarding_section(cfg)
        self.assertEqual(issues, ["forwarding section is required and must be a mapping"])


class ForwardingIntentCliTests(unittest.TestCase):
    def test_cli_prints_valid_json_intent(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = forwarding_intent_cli.main(["--config", str(NVIDIA_EXAMPLE), "--pretty"])
        self.assertEqual(rc, 0)
        self.assertIn('"valid": true', buf.getvalue())
        self.assertIn("odse-vm2vm-nvidia", buf.getvalue())

    def test_cli_reports_issues_and_nonzero_exit(self):
        import json
        import tempfile

        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        del config["forwarding"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(config, handle)
            temp_path = handle.name
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = forwarding_intent_cli.main(["--config", temp_path, "--json"])
            self.assertEqual(rc, 1)
            self.assertIn("forwarding section is required", buf.getvalue())
        finally:
            Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
