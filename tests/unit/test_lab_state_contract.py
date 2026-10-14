"""lab_state.py result-directory contract (no hardware)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lab_state  # noqa: E402


class LabStateContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name) / "nvidia-bluefield" / "run-1"
        self.run_dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_atomic_write_and_canonical_filenames(self):
        payload = {"stage": "discover", "ok": True}
        path = lab_state.save_stage(
            self.run_dir,
            "discover",
            payload,
            config_hash_value="abc",
            dry_run=True,
            apply=False,
        )
        self.assertEqual(path.name, "discover.json")
        self.assertTrue(path.exists())
        self.assertFalse(path.with_name("discover.json.tmp").exists())
        loaded = lab_state.load_stage(self.run_dir, "discover")
        self.assertEqual(loaded["ok"], True)
        self.assertEqual(loaded["config_hash"], "abc")
        self.assertTrue(loaded["dry_run"])
        self.assertFalse(loaded["apply"])
        self.assertIn("timestamp", loaded)

    def test_representor_map_and_offload_validation_names(self):
        lab_state.save_stage(self.run_dir, "discover-representors", {"stage": "discover-representors"})
        lab_state.save_stage(self.run_dir, "validate-offload", {"stage": "validate-offload"})
        self.assertTrue((self.run_dir / "representor-map.json").exists())
        self.assertTrue((self.run_dir / "discover_representors.json").exists())
        self.assertTrue((self.run_dir / "offload-validation.json").exists())
        self.assertEqual(
            lab_state.load_stage(self.run_dir, "discover-representors")["stage"],
            "discover-representors",
        )

    def test_run_metadata_and_config_hash(self):
        digest = lab_state.config_hash({"vendor": {"type": "nvidia-bluefield"}})
        self.assertEqual(len(digest), 64)
        lab_state.write_run_metadata(
            self.run_dir,
            run_id="run-1",
            vendor="nvidia-bluefield",
            config_path="config/lab-nvidia-vm2vm.example.yaml",
            config_hash_value=digest,
            dry_run=True,
            apply=False,
        )
        meta = lab_state.load_stage(self.run_dir, "run-metadata")
        self.assertEqual(meta["run_id"], "run-1")
        self.assertEqual(meta["config_hash"], digest)
        self.assertTrue(meta["dry_run"])
        self.assertFalse(meta["apply"])

    def test_documented_graph_missing_prepare_ovs(self):
        lab_state.save_stage(self.run_dir, "discover-representors", {"stage": "discover-representors"})
        missing = lab_state.missing_dependencies(self.run_dir, "configure-forwarding")
        self.assertEqual(missing, ["prepare-ovs"])

    def test_legacy_underscore_still_readable(self):
        (self.run_dir / "validate_config.json").write_text(
            json.dumps({"stage": "validate-config", "valid": True}),
            encoding="utf-8",
        )
        loaded = lab_state.load_stage(self.run_dir, "validate-config")
        self.assertTrue(loaded["valid"])


if __name__ == "__main__":
    unittest.main()
