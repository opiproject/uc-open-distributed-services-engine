"""Safe command runner for vendor plugins.

Uses subprocess.run. Default dry_run=True so enablement stubs cannot
mutate a host unless a caller opts in. shell=True is off unless
explicitly requested.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Union

from .models import CommandResult

LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0

_SENSITIVE_FRAGMENTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "bearer",
)

_FLAG_TAKES_SECRET = {
    "--password",
    "--passwd",
    "--token",
    "--secret",
    "--api-key",
    "--apikey",
    "--access-token",
    "--auth",
    "-p",
}

_REDACTED = "***REDACTED***"

Argv = Union[str, Sequence[str]]


def planned_command(
    argv: Sequence[str],
    reason: str,
    *,
    destructive: bool = True,
) -> CommandResult:
    """Record a command without executing it (dry_run)."""
    result = run(argv, dry_run=True, reason=reason)
    if destructive:
        LOG.info("planned destructive command (not executed): %s", redact_argv(list(argv)))
    return result


def never_run(argv: Sequence[str]) -> None:
    """Guard for call sites that must not execute yet."""
    raise RuntimeError(
        "refusing to execute %r without an explicit run(..., dry_run=False)"
        % (list(argv),)
    )


def format_argv(argv: Optional[Sequence[str]]) -> List[str]:
    if not argv:
        return []
    return [str(part) for part in argv]


def redact_text(text: Optional[str]) -> str:
    """Mask secret-like tokens in a string for logs."""
    if not text:
        return ""
    out = text
    out = re.sub(
        r"(?i)(bearer\s+)(\S+)",
        r"\1" + _REDACTED,
        out,
    )
    out = re.sub(
        r"(?i)\b("
        + "|".join(re.escape(s) for s in _SENSITIVE_FRAGMENTS)
        + r")(\s*[=:]\s*)([^\s,;]+)",
        r"\1\2" + _REDACTED,
        out,
    )
    return out


def redact_argv(argv: Sequence[str]) -> List[str]:
    redacted: List[str] = []
    hide_next = False
    for part in argv:
        if hide_next:
            redacted.append(_REDACTED)
            hide_next = False
            continue
        key = part.split("=", 1)[0].lower().lstrip("-")
        if part.lower() in _FLAG_TAKES_SECRET or (
            part.startswith("-") and any(s in key.replace("-", "_") for s in _SENSITIVE_FRAGMENTS)
        ):
            if "=" in part:
                prefix, _val = part.split("=", 1)
                redacted.append("%s=%s" % (prefix, _REDACTED))
            else:
                redacted.append(part)
                hide_next = True
            continue
        if "=" in part and any(s in key.replace("-", "_") for s in _SENSITIVE_FRAGMENTS):
            prefix, _val = part.split("=", 1)
            redacted.append("%s=%s" % (prefix, _REDACTED))
            continue
        redacted.append(redact_text(part) if part else part)
    return redacted


def run(
    argv: Argv,
    *,
    dry_run: bool = True,
    timeout: Optional[float] = DEFAULT_TIMEOUT_SECONDS,
    shell: bool = False,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[Mapping[str, str]] = None,
    reason: Optional[str] = None,
    input_text: Optional[str] = None,
) -> CommandResult:
    """Run argv with subprocess.run, or record it when dry_run is True.

    shell defaults to False. Pass shell=True only when a caller explicitly
    needs a shell; prefer an argument list. input_text, when given, is
    piped to the child's stdin (e.g. a script body for `ssh host bash -s`)
    and is never executed or interpreted by this function itself.
    """
    if shell:
        if not isinstance(argv, str):
            raise TypeError("shell=True requires a command string, not a sequence")
        argv_list = [argv]
        popen_args: Union[str, List[str]] = argv
    else:
        if isinstance(argv, str):
            raise TypeError("pass a sequence of arguments, or set shell=True explicitly")
        argv_list = format_argv(argv)
        if not argv_list:
            raise ValueError("empty command")
        popen_args = argv_list

    logged = redact_argv(argv_list)
    LOG.info(
        "command dry_run=%s shell=%s timeout=%s reason=%s argv=%s",
        dry_run,
        shell,
        timeout,
        reason,
        logged,
    )

    if dry_run:
        return CommandResult(
            argv=argv_list,
            executed=False,
            dry_run=True,
            returncode=None,
            stdout=None,
            stderr=None,
            reason=reason,
            timed_out=False,
            shell=shell,
        )

    try:
        completed = subprocess.run(
            popen_args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_captured(exc.stdout)
        stderr = _decode_captured(exc.stderr)
        LOG.warning(
            "command timed out after %s s argv=%s stdout=%s stderr=%s",
            timeout,
            logged,
            redact_text(stdout),
            redact_text(stderr),
        )
        return CommandResult(
            argv=argv_list,
            executed=True,
            dry_run=False,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            reason=reason,
            timed_out=True,
            shell=shell,
        )
    except OSError as exc:
        LOG.error("command failed to start argv=%s error=%s", logged, exc)
        return CommandResult(
            argv=argv_list,
            executed=False,
            dry_run=False,
            returncode=None,
            stdout=None,
            stderr=str(exc),
            reason=reason,
            timed_out=False,
            shell=shell,
        )

    LOG.info(
        "command finished returncode=%s argv=%s stdout=%s stderr=%s",
        completed.returncode,
        logged,
        redact_text(completed.stdout or ""),
        redact_text(completed.stderr or ""),
    )
    return CommandResult(
        argv=argv_list,
        executed=True,
        dry_run=False,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        reason=reason,
        timed_out=False,
        shell=shell,
    )


def command_exists(name: str) -> bool:
    """True if `name` is on PATH (shutil.which)."""
    found = shutil.which(name) is not None
    LOG.debug("command_exists(%r) -> %s", name, found)
    return found


def read_file(path: Union[str, Path]) -> Optional[str]:
    """Read a text file. Missing/unreadable → None (no fake content)."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        LOG.info("read_file %s failed: %s", target, exc)
        return None
    LOG.debug("read_file %s (%s bytes)", target, len(text))
    return text


def write_file(
    path: Union[str, Path],
    content: str,
    dry_run: bool = True,
) -> CommandResult:
    """Write text to path. Default dry_run=True does not touch the filesystem."""
    target = Path(path)
    argv = ["write_file", str(target)]
    LOG.info(
        "write_file dry_run=%s path=%s bytes=%s",
        dry_run,
        target,
        len(content),
    )
    if dry_run:
        return CommandResult(
            argv=argv,
            executed=False,
            dry_run=True,
            reason="write_file dry_run",
        )
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        LOG.error("write_file failed path=%s error=%s", target, exc)
        return CommandResult(
            argv=argv,
            executed=False,
            dry_run=False,
            returncode=1,
            stderr=str(exc),
            reason="write_file",
        )
    return CommandResult(
        argv=argv,
        executed=True,
        dry_run=False,
        returncode=0,
        reason="write_file",
    )


def list_netdevs(*, sysfs_net: Optional[Union[str, Path]] = None) -> List[str]:
    """Netdev names from sysfs. Empty if the directory is missing (e.g. non-Linux)."""
    root = Path(sysfs_net) if sysfs_net is not None else Path("/sys/class/net")
    if not root.is_dir():
        LOG.info("list_netdevs: %s not a directory; returning []", root)
        return []
    names = sorted(p.name for p in root.iterdir() if p.name and p.name not in (".", ".."))
    LOG.debug("list_netdevs -> %s", names)
    return names


def list_pci_devices(
    *,
    sysfs_pci: Optional[Union[str, Path]] = None,
    use_lspci: bool = True,
    dry_run: bool = False,
) -> List[str]:
    """PCI addresses or lspci lines. Empty if neither sysfs nor lspci is available.

    Does not invent devices. dry_run applies only to the optional lspci probe.
    """
    pci_root = Path(sysfs_pci) if sysfs_pci is not None else Path("/sys/bus/pci/devices")
    if pci_root.is_dir():
        addrs = sorted(p.name for p in pci_root.iterdir() if p.name)
        LOG.debug("list_pci_devices sysfs -> %s", addrs)
        return addrs
    if use_lspci and command_exists("lspci"):
        result = run(["lspci"], dry_run=dry_run, timeout=DEFAULT_TIMEOUT_SECONDS, shell=False)
        if result.dry_run or not result.executed or result.returncode not in (0, None):
            if result.dry_run:
                LOG.info("list_pci_devices: lspci dry_run, no devices claimed")
            return []
        lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
        return lines
    LOG.info("list_pci_devices: no sysfs PCI and no lspci; returning []")
    return []


def _decode_captured(data: Optional[Union[str, bytes]]) -> Optional[str]:
    if data is None:
        return None
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data
