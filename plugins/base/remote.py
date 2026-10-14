"""Remote command execution for DPU-side OVS/representors (dry-run default).

Used when NVIDIA BlueField runs in DPU mode: labctl must run ovs-vsctl
on the Arm OS, not on the x86 host. Local commands still go through
plugins.base.commands.run (no shell=True unless a caller opts in).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence, Union

from .commands import command_exists, redact_argv, run
from .models import CommandResult

LOG = logging.getLogger(__name__)

Argv = Union[str, Sequence[str]]


class RemoteCommandRunner:
    """Plan or execute local/SSH commands. Default dry_run=True."""

    def __init__(self, output_dir: Optional[Union[str, Path]] = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else None
        self.history: List[CommandResult] = []

    def _record(self, result: CommandResult, label: str) -> CommandResult:
        self.history.append(result)
        if self.output_dir and (result.stdout or result.stderr):
            self.output_dir.mkdir(parents=True, exist_ok=True)
            stem = label.replace("/", "_").replace(" ", "_")[:80]
            (self.output_dir / ("%s.stdout.txt" % stem)).write_text(
                result.stdout or "", encoding="utf-8"
            )
        LOG.info("remote %s argv=%s executed=%s", label, redact_argv(result.argv), result.executed)
        return result

    def run_local(
        self,
        command: Argv,
        dry_run: bool = True,
        *,
        timeout: Optional[float] = 30.0,
        reason: Optional[str] = None,
        shell: bool = False,
    ) -> CommandResult:
        result = run(
            command,
            dry_run=dry_run,
            timeout=timeout,
            reason=reason or "remote.run_local",
            shell=shell,
        )
        return self._record(result, "local")

    def _ssh_argv(
        self,
        host: str,
        user: str,
        command: Sequence[str],
        *,
        key_path: Optional[str] = None,
        port: int = 22,
    ) -> List[str]:
        argv = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(port),
        ]
        if key_path:
            argv.extend(["-i", key_path])
        argv.append("%s@%s" % (user, host))
        argv.append("--")
        argv.extend([str(p) for p in command])
        return argv

    def run_ssh(
        self,
        host: str,
        user: str,
        command: Sequence[str],
        key_path: Optional[str] = None,
        port: int = 22,
        dry_run: bool = True,
        *,
        timeout: Optional[float] = 30.0,
        reason: Optional[str] = None,
        sudo: bool = False,
    ) -> CommandResult:
        remote_cmd = list(command)
        if sudo:
            remote_cmd = ["sudo", "-n"] + remote_cmd
        argv = self._ssh_argv(host, user, remote_cmd, key_path=key_path, port=port)
        result = run(
            argv,
            dry_run=dry_run,
            timeout=timeout,
            reason=reason or "remote.run_ssh",
            shell=False,
        )
        return self._record(result, "ssh:%s" % host)

    def file_exists_remote(
        self,
        path: str,
        *,
        host: Optional[str] = None,
        user: Optional[str] = None,
        key_path: Optional[str] = None,
        port: int = 22,
        dry_run: bool = True,
    ) -> CommandResult:
        if not host or not user:
            local = Path(path)
            exists = local.exists()
            return CommandResult(
                argv=["test", "-e", path],
                executed=not dry_run,
                dry_run=dry_run,
                returncode=0 if exists else 1,
                reason="file_exists local",
            )
        return self.run_ssh(
            host,
            user,
            ["test", "-e", path],
            key_path=key_path,
            port=port,
            dry_run=dry_run,
            reason="file_exists_remote",
        )

    def command_exists_remote(
        self,
        command: str,
        *,
        host: Optional[str] = None,
        user: Optional[str] = None,
        key_path: Optional[str] = None,
        port: int = 22,
        dry_run: bool = True,
    ) -> CommandResult:
        if not host or not user:
            present = command_exists(command)
            return CommandResult(
                argv=["command", "-v", command],
                executed=False,
                dry_run=True,
                returncode=0 if present else 1,
                reason="command_exists local",
            )
        return self.run_ssh(
            host,
            user,
            ["command", "-v", command],
            key_path=key_path,
            port=port,
            dry_run=dry_run,
            reason="command_exists_remote",
        )

    def copy_to_remote(
        self,
        local_path: str,
        remote_path: str,
        *,
        host: str,
        user: str,
        key_path: Optional[str] = None,
        port: int = 22,
        dry_run: bool = True,
    ) -> CommandResult:
        argv = ["scp", "-P", str(port)]
        if key_path:
            argv.extend(["-i", key_path])
        argv.extend([local_path, "%s@%s:%s" % (user, host, remote_path)])
        result = run(argv, dry_run=dry_run, reason="copy_to_remote", shell=False)
        return self._record(result, "scp-to")

    def collect_remote_file(
        self,
        remote_path: str,
        local_path: str,
        *,
        host: str,
        user: str,
        key_path: Optional[str] = None,
        port: int = 22,
        dry_run: bool = True,
    ) -> CommandResult:
        argv = ["scp", "-P", str(port)]
        if key_path:
            argv.extend(["-i", key_path])
        argv.extend(["%s@%s:%s" % (user, host, remote_path), local_path])
        result = run(argv, dry_run=dry_run, reason="collect_remote_file", shell=False)
        return self._record(result, "scp-from")


def runner_from_lab_config(
    config: Optional[dict],
    *,
    output_dir: Optional[Union[str, Path]] = None,
) -> RemoteCommandRunner:
    remote = (config or {}).get("remote_execution") or {}
    dpu = (config or {}).get("dpu") or {}
    dest = output_dir
    if dest is None and remote.get("enabled"):
        dest = Path("tests") / "results" / "remote"
    return RemoteCommandRunner(output_dir=dest)


def ssh_params(config: Optional[dict]) -> dict:
    dpu = (config or {}).get("dpu") or {}
    return {
        "host": dpu.get("management_ip"),
        "user": dpu.get("ssh_user"),
        "key_path": dpu.get("ssh_key_path"),
        "port": int(dpu.get("ssh_port") or 22),
        "sudo": bool(dpu.get("sudo", True)),
        # Per-command execution timeout for run_ssh (docs/operations/
        # dpu-remote-execution.md documents this key; callers that make
        # several *sequential* SSH round trips -- e.g.
        # collect_dpu_samples_via_ssh() in vf_to_representor_map.py --
        # should size their own enclosing subprocess timeout as at least
        # (number of probes * command_timeout_seconds) plus headroom,
        # not just reuse this single-command value directly.
        "command_timeout_seconds": float(dpu.get("command_timeout_seconds") or 30),
    }


def is_dpu_mode(config: Optional[dict]) -> bool:
    nvidia = (config or {}).get("nvidia") or {}
    return nvidia.get("mode") == "dpu" and nvidia.get("ovs_location") == "dpu"
