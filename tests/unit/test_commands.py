"""Unit tests for plugins.base.commands (no hardware required)."""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.commands import (  # noqa: E402
    command_exists,
    list_netdevs,
    list_pci_devices,
    planned_command,
    read_file,
    redact_argv,
    redact_text,
    run,
    write_file,
)


class RedactTests(unittest.TestCase):
    def test_redact_password_flag_next_arg(self):
        self.assertEqual(
            redact_argv(["tool", "--password", "s3cret", "ok"]),
            ["tool", "--password", "***REDACTED***", "ok"],
        )

    def test_redact_token_equals(self):
        self.assertEqual(
            redact_argv(["env", "TOKEN=abcd"]),
            ["env", "TOKEN=***REDACTED***"],
        )

    def test_redact_bearer_text(self):
        self.assertIn("***REDACTED***", redact_text("Authorization: Bearer abc.def"))


class RunTests(unittest.TestCase):
    def test_dry_run_does_not_call_subprocess(self):
        with patch("plugins.base.commands.subprocess.run") as mock_run:
            result = run(["echo", "hi"], dry_run=True, timeout=5)
        mock_run.assert_not_called()
        self.assertFalse(result.executed)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.returncode)
        self.assertEqual(result.argv, ["echo", "hi"])
        self.assertFalse(result.shell)

    def test_planned_command_is_dry_run(self):
        result = planned_command(["ovs-vsctl", "add-br", "br-int"], "destructive")
        self.assertTrue(result.dry_run)
        self.assertFalse(result.executed)

    def test_string_argv_requires_explicit_shell(self):
        with self.assertRaises(TypeError):
            run("echo hi", dry_run=True, shell=False)

    def test_shell_true_requires_string(self):
        with self.assertRaises(TypeError):
            run(["echo", "hi"], dry_run=True, shell=True)

    def test_captures_stdout_stderr_returncode(self):
        completed = MagicMock()
        completed.returncode = 7
        completed.stdout = "out"
        completed.stderr = "err"
        with patch("plugins.base.commands.subprocess.run", return_value=completed) as mock_run:
            result = run(["/bin/false"], dry_run=False, timeout=12, shell=False)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 12)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.stderr, "err")
        self.assertTrue(result.executed)
        self.assertFalse(result.dry_run)

    def test_timeout_sets_timed_out(self):
        exc = subprocess.TimeoutExpired(cmd=["sleep", "9"], timeout=0.1, output=b"x", stderr=b"y")
        with patch("plugins.base.commands.subprocess.run", side_effect=exc):
            result = run(["sleep", "9"], dry_run=False, timeout=0.1)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.executed)
        self.assertIsNone(result.returncode)
        self.assertEqual(result.stdout, "x")
        self.assertEqual(result.stderr, "y")

    def test_logs_redacted_argv(self):
        log = logging.getLogger("plugins.base.commands")
        with self.assertLogs(log, level="INFO") as cm:
            run(["tool", "--token", "hunter2"], dry_run=True)
        joined = " ".join(cm.output)
        self.assertNotIn("hunter2", joined)
        self.assertIn("***REDACTED***", joined)

    def test_input_text_forwarded_to_subprocess(self):
        completed = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("plugins.base.commands.subprocess.run", return_value=completed) as mock_run:
            result = run(["bash", "-s"], dry_run=False, timeout=5, input_text="echo hi\n")
        self.assertEqual(mock_run.call_args.kwargs["input"], "echo hi\n")
        self.assertTrue(result.executed)

    def test_input_text_defaults_to_none(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("plugins.base.commands.subprocess.run", return_value=completed) as mock_run:
            run(["true"], dry_run=False, timeout=5)
        self.assertIsNone(mock_run.call_args.kwargs["input"])

    def test_shell_true_passed_to_subprocess(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("plugins.base.commands.subprocess.run", return_value=completed) as mock_run:
            result = run("true", dry_run=False, shell=True, timeout=3)
        self.assertTrue(result.shell)
        self.assertTrue(mock_run.call_args.kwargs["shell"])
        self.assertEqual(mock_run.call_args.args[0], "true")


class HelperTests(unittest.TestCase):
    def test_command_exists_true_false(self):
        with patch("plugins.base.commands.shutil.which", return_value="/usr/bin/lspci"):
            self.assertTrue(command_exists("lspci"))
        with patch("plugins.base.commands.shutil.which", return_value=None):
            self.assertFalse(command_exists("definitely-not-a-binary-odse"))

    def test_read_file_ok_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello", encoding="utf-8")
            self.assertEqual(read_file(path), "hello")
            self.assertIsNone(read_file(path / "missing"))

    def test_write_file_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.txt"
            result = write_file(path, "nope", dry_run=True)
            self.assertTrue(result.dry_run)
            self.assertFalse(path.exists())

    def test_write_file_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.txt"
            result = write_file(path, "yes", dry_run=False)
            self.assertTrue(result.executed)
            self.assertEqual(path.read_text(encoding="utf-8"), "yes")

    def test_list_netdevs_from_fake_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "eth0").mkdir()
            (root / "lo").mkdir()
            self.assertEqual(list_netdevs(sysfs_net=root), ["eth0", "lo"])

    def test_list_netdevs_missing_sysfs(self):
        self.assertEqual(list_netdevs(sysfs_net=Path("/nonexistent/odse-net")), [])

    def test_list_pci_devices_from_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "0000-01-00.0").mkdir()
            self.assertEqual(list_pci_devices(sysfs_pci=root, use_lspci=False), ["0000-01-00.0"])

    def test_list_pci_devices_empty_without_sources(self):
        with patch("plugins.base.commands.command_exists", return_value=False):
            self.assertEqual(
                list_pci_devices(sysfs_pci=Path("/nonexistent/odse-pci"), use_lspci=True),
                [],
            )


if __name__ == "__main__":
    unittest.main()
