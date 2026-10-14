"""tools/report_generator.py tests.

Covers: defensive stage-file loading, section extraction from realistic
stage JSON shapes, the Forwarding Configuration section (Prompt J item
4), the six-way verdict logic, and an end-to-end CLI run producing
result-summary.md / result-summary.json.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location("odse_report_generator", ROOT / "tools" / "report_generator.py")
report_generator = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = report_generator
_SPEC.loader.exec_module(report_generator)


def _write(dirpath: Path, name: str, data) -> None:
    (dirpath / name).write_text(json.dumps(data), encoding="utf-8")


class LoadStagesTests(unittest.TestCase):
    def test_missing_directory_yields_all_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            stages = report_generator.load_stages(Path(tmp))
        for name in report_generator._STAGE_FILES:
            self.assertIsNone(stages[name], name)
        self.assertIsNone(stages["cleanup_summary"])

    def test_reads_present_files_by_labctl_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write(tmp_path, "discover.json", {"stage": "discover"})
            _write(tmp_path, "validate_config.json", {"stage": "validate-config", "valid": True})
            stages = report_generator.load_stages(tmp_path)
            self.assertEqual(stages["discover"], {"stage": "discover"})
            self.assertTrue(stages["validate_config"]["valid"])
            self.assertIsNone(stages["prepare_vfs"])

    def test_cleanup_summary_preferred_over_cleanup_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "cleanup").mkdir()
            _write(tmp_path / "cleanup", "cleanup-summary.json", {"stages": {"stop_vms": {"success": True}}})
            stages = report_generator.load_stages(tmp_path)
            self.assertEqual(stages["cleanup_summary"]["stages"]["stop_vms"]["success"], True)

    def test_unparsable_json_is_none_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "discover.json").write_text("{not valid json", encoding="utf-8")
            stages = report_generator.load_stages(tmp_path)
            self.assertIsNone(stages["discover"])


class GHelperTests(unittest.TestCase):
    def test_digs_nested_dicts(self):
        data = {"a": {"b": {"c": 42}}}
        self.assertEqual(report_generator.g(data, "a", "b", "c"), 42)

    def test_missing_key_returns_default(self):
        self.assertEqual(report_generator.g({"a": {}}, "a", "b", "c", default="fallback"), "fallback")

    def test_non_dict_intermediate_returns_default(self):
        self.assertIsNone(report_generator.g({"a": [1, 2]}, "a", "b"))


class ForwardingConfigurationSectionTests(unittest.TestCase):
    def test_extracts_all_required_fields(self):
        stages = {
            "configure_forwarding": {
                "intent": {"name": "vm-a-to-vm-b", "type": "l2-vm-to-vm", "mode": "bridge", "offload": {"requested": True}},
                "result": {
                    "vendor": "nvidia-bluefield",
                    "control_entity": "ovs-vsctl",
                    "forwarding_entity": "br-int",
                    "configured_ports": ["pf0vf0", "pf0vf1"],
                    "configured_flows": [],
                    "offload_requested": True,
                    "offload_configured": True,
                    "offload_confirmed": None,
                    "success": True,
                    "message": "ok",
                },
            },
            "validate_offload": {"offload_checker_report": {"assessment": {"status": "probable_offloaded", "confidence": 0.8}}},
        }
        section = report_generator.section_forwarding_configuration(stages)
        self.assertEqual(section["user_intent"]["name"], "vm-a-to-vm-b")
        self.assertEqual(section["backend_selected"], "nvidia-bluefield")
        self.assertEqual(section["control_entity"], "ovs-vsctl")
        self.assertEqual(section["forwarding_entity"], "br-int")
        self.assertEqual(section["configured_ports"], ["pf0vf0", "pf0vf1"])
        self.assertTrue(section["offload_requested"])
        self.assertTrue(section["offload_configured"])
        self.assertIn("probable", section["offload_confirmed_or_probable"])
        self.assertNotIn("confirmed (", section["offload_confirmed_or_probable"])

    def test_offload_requested_falls_back_to_intent_when_result_missing(self):
        stages = {
            "configure_forwarding": {
                "intent": {"offload": {"requested": True}},
                "result": {},
            },
            "validate_offload": None,
        }
        section = report_generator.section_forwarding_configuration(stages)
        self.assertTrue(section["offload_requested"])
        self.assertEqual(section["offload_confirmed_or_probable"], "not available (validate-offload stage not run)")

    def test_confirmed_requires_both_flag_and_probable_offloaded_status(self):
        stages = {
            "configure_forwarding": {"intent": {}, "result": {"offload_confirmed": True}},
            "validate_offload": {"offload_checker_report": {"assessment": {"status": "probable_offloaded", "confidence": 0.9}}},
        }
        section = report_generator.section_forwarding_configuration(stages)
        self.assertTrue(section["offload_confirmed_or_probable"].startswith("confirmed"))

    def test_software_only_is_not_reported_as_probable(self):
        stages = {
            "configure_forwarding": {"intent": {}, "result": {}},
            "validate_offload": {"offload_checker_report": {"assessment": {"status": "software_only", "confidence": 0.7}}},
        }
        section = report_generator.section_forwarding_configuration(stages)
        self.assertIn("not offloaded", section["offload_confirmed_or_probable"])


class SectionBuilderDefensivenessTests(unittest.TestCase):
    def test_all_sections_tolerate_empty_stages(self):
        stages = {name: None for name in report_generator._STAGE_FILES}
        stages["cleanup_summary"] = None
        # Every section_* builder must run without raising on all-missing data.
        self.assertEqual(report_generator.section_server_details(stages), {
            "host_name": None, "management_interface": None, "hostname": None, "platform": None,
        })
        self.assertEqual(report_generator.section_pf_vf_representor_mapping(stages)["mappings"], [])
        self.assertEqual(report_generator.section_traffic_test_results(stages)["commands"], [])
        self.assertFalse(report_generator.section_cleanup_result(stages)["ran"])


class ComputeVerdictTests(unittest.TestCase):
    def _base_stages(self):
        return {name: None for name in report_generator._STAGE_FILES}

    def test_fail_setup_on_invalid_config(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": False, "issues": ["host.name is required"]}
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "FAIL_SETUP")

    def test_fail_setup_on_backend_error(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["prepare_vfs"] = {"backend_result": {"status": "error", "message": "no such PF"}}
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "FAIL_SETUP")

    def test_inconclusive_when_traffic_never_ran(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "INCONCLUSIVE")

    def test_inconclusive_when_traffic_only_dry_run(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["run_traffic"] = {
            "commands": [{"kind": "ping", "command": {"executed": False, "returncode": None, "dry_run": True}}],
        }
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "INCONCLUSIVE")

    def test_fail_connectivity_when_ping_fails(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["run_traffic"] = {
            "commands": [{"kind": "ping", "command": {"executed": True, "returncode": 1, "dry_run": False}}],
        }
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "FAIL_CONNECTIVITY")

    def test_pass_functional_only_when_no_offload_evidence(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["run_traffic"] = {
            "commands": [{"kind": "ping", "command": {"executed": True, "returncode": 0, "dry_run": False}}],
        }
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "PASS_FUNCTIONAL_ONLY")

    def test_pass_offload_probable(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["run_traffic"] = {
            "commands": [{"kind": "ping", "command": {"executed": True, "returncode": 0, "dry_run": False}}],
        }
        stages["validate_offload"] = {"offload_checker_report": {"assessment": {"status": "probable_offloaded", "confidence": 0.9}}}
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "PASS_OFFLOAD_PROBABLE")

    def test_pass_offload_confirmed_requires_backend_flag(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["run_traffic"] = {
            "commands": [{"kind": "ping", "command": {"executed": True, "returncode": 0, "dry_run": False}}],
        }
        stages["validate_offload"] = {"offload_checker_report": {"assessment": {"status": "probable_offloaded", "confidence": 0.95}}}
        stages["configure_forwarding"] = {"result": {"offload_confirmed": True}}
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "PASS_OFFLOAD_CONFIRMED")

    def test_probable_offloaded_without_confirmed_flag_stays_probable(self):
        stages = self._base_stages()
        stages["validate_config"] = {"valid": True, "issues": []}
        stages["run_traffic"] = {
            "commands": [{"kind": "ping", "command": {"executed": True, "returncode": 0, "dry_run": False}}],
        }
        stages["validate_offload"] = {"offload_checker_report": {"assessment": {"status": "probable_offloaded", "confidence": 0.95}}}
        stages["configure_forwarding"] = {"result": {"offload_confirmed": False}}
        verdict = report_generator.compute_verdict(stages)
        self.assertEqual(verdict["verdict"], "PASS_OFFLOAD_PROBABLE")


class EndToEndTests(unittest.TestCase):
    def test_full_run_directory_produces_pass_offload_probable(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            _write(input_dir, "validate_config.json", {"valid": True, "issues": [], "config": {"host_name": "h", "vendor_type": "nvidia-bluefield", "pf_name": "pf0"}})
            _write(input_dir, "discover.json", {"dpu_inventory": {"host": {"hostname": "h"}, "netdevs": {"interfaces": [{"name": "pf0", "driver": "mlx5_core"}]}, "vendor_hints": []}})
            _write(input_dir, "discover_representors.json", {"vf_to_representor_map": {"mappings": [{"vf_index": 0, "vf_pci": "0000:03:00.2", "representor_netdev": "pf0vf0", "mapping_method": "devlink_pcivf"}]}})
            _write(input_dir, "configure_forwarding.json", {
                "intent": {"name": "n", "offload": {"requested": True}},
                "result": {"vendor": "nvidia-bluefield", "configured_ports": ["pf0vf0"], "offload_requested": True, "offload_configured": True, "success": True},
            })
            _write(input_dir, "run_traffic.json", {"commands": [{"kind": "ping", "command": {"executed": True, "returncode": 0, "dry_run": False}}]})
            _write(input_dir, "validate_offload.json", {"offload_checker_report": {"assessment": {"status": "probable_offloaded", "confidence": 0.8}}})

            rc = report_generator.main(["--input-dir", str(input_dir), "--run-id", "r1", "--vendor", "nvidia-bluefield"])
            self.assertEqual(rc, 0)

            md_path = input_dir / "result-summary.md"
            json_path = input_dir / "result-summary.json"
            self.assertTrue(md_path.exists())
            self.assertTrue(json_path.exists())

            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(report["final_verdict"], "PASS_OFFLOAD_PROBABLE")
            self.assertIn("10. Forwarding Configuration", md_path.read_text(encoding="utf-8"))

    def test_missing_input_dir_returns_nonzero(self):
        rc = report_generator.main(["--input-dir", "/does/not/exist/anywhere"])
        self.assertNotEqual(rc, 0)

    def test_partial_run_directory_still_produces_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            _write(input_dir, "discover.json", {"dpu_inventory": {"host": {"hostname": "h"}}})
            rc = report_generator.main(["--input-dir", str(input_dir)])
            self.assertEqual(rc, 0)
            report = json.loads((input_dir / "result-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(report["final_verdict"], "INCONCLUSIVE")
            self.assertIn("validate_config", report["sections"]["test_metadata"]["stages_missing"])


if __name__ == "__main__":
    unittest.main()
