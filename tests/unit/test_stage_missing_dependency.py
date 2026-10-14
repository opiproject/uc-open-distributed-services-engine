"""labctl fails clearly when a hard stage dependency is missing."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location("odse_labctl", ROOT / "tools" / "labctl.py")
labctl = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = labctl
_SPEC.loader.exec_module(labctl)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class StageMissingDependencyTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = labctl.RESULTS_ROOT
        labctl.RESULTS_ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        labctl.RESULTS_ROOT = self._orig
        self._tmpdir.cleanup()

    def test_configure_forwarding_without_representor_map(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = labctl.main(
                [
                    "vm2vm",
                    "configure-forwarding",
                    "--config",
                    str(NVIDIA_EXAMPLE),
                    "--run-id",
                    "missing-map",
                    "--json",
                ]
            )
        self.assertEqual(rc, 2)
        envelope = json.loads(out.getvalue())
        result = envelope["result"]
        self.assertEqual(result["error"], "STAGE_MISSING_DEPENDENCY")
        self.assertIn("discover-representors", result["missing_stages"])
        saved = labctl.load_stage_json(
            labctl.run_dir_for("nvidia-bluefield", "missing-map"),
            "discover-representors",
        )
        self.assertIsNone(saved)
        cfg_fwd = labctl.load_stage_json(
            labctl.run_dir_for("nvidia-bluefield", "missing-map"),
            "configure-forwarding",
        )
        self.assertEqual(cfg_fwd["error"], "STAGE_MISSING_DEPENDENCY")
        self.assertIn("config_hash", cfg_fwd)


if __name__ == "__main__":
    unittest.main()
