#!/usr/bin/env python3
"""ovs_offload_checker.py

Inspect Open vSwitch and Linux datapath state to help determine whether
flows are (probably) hardware-offloaded.

This is a vendor-neutral *observation* tool, not a proof of offload. OVS
and the kernel expose a handful of common markers (`offloaded:yes`,
`dp:tc`, TC `in_hw`); when those are present they are highlighted. When
they are absent the tool still dumps the raw state so a human or a later
vendor plugin can interpret it. See README.md and
docs/test-plan/offload-validation.md.

Usage:
    python3 ovs_offload_checker.py
    python3 ovs_offload_checker.py --interfaces eth1v0,p0 --pf p0
    python3 ovs_offload_checker.py --bridge br-int --flow 'in_port=1,tcp,tp_dst=80'
    python3 ovs_offload_checker.py --duration 10 --interfaces eth1v0
    python3 ovs_offload_checker.py --output-dir tests/results/nvidia/r1/validate-offload --json --markdown
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "odse-ovs-offload-checker/v1alpha1"
DEFAULT_TIMEOUT_SECONDS = 15

# Markers that are documented / widely observed in upstream OVS and
# kernel TC, not tied to a particular NIC vendor.
DP_OFFLOADED_YES = re.compile(r"\boffloaded\s*[:=]\s*yes\b", re.IGNORECASE)
DP_OFFLOADED_PARTIAL = re.compile(r"\boffloaded\s*[:=]\s*partial\b", re.IGNORECASE)
DP_OFFLOADED_NO = re.compile(r"\boffloaded\s*[:=]\s*no\b", re.IGNORECASE)
DP_TC = re.compile(r"\bdp\s*[:=]\s*tc\b", re.IGNORECASE)
DP_OVS = re.compile(r"\bdp\s*[:=]\s*ovs\b", re.IGNORECASE)

TC_IN_HW = re.compile(r"\bin_hw\b", re.IGNORECASE)
TC_NOT_IN_HW = re.compile(r"\bnot_in_hw\b", re.IGNORECASE)
TC_IN_HW_COUNT = re.compile(r"\bin_hw_count\s+(\d+)\b", re.IGNORECASE)

# Counter *names* that are generic enough to highlight without naming a
# vendor. The full ethtool -S dump is always kept in JSON.
COUNTER_HIGHLIGHT = re.compile(
    r"(offload|in_hw|hw_|_hw$|xdp|tc_|eswitch|representor|dropped|discard)",
    re.IGNORECASE,
)

ETHTOOL_COUNTER_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_./:-]+)\s*:\s*(?P<value>[-+]?\d+)\s*$"
)

TC_DIRECTIONS = ("ingress", "egress")

# Static, always-true caveats surfaced in every report so a downstream
# consumer (docs, report_generator.py) never has to re-derive them.
OFFLOAD_ASSESSMENT_LIMITATIONS = [
    "This is a heuristic based on documented upstream OVS/TC markers and "
    "generically-named counters, not a vendor-verified proof of hardware "
    "offload.",
    "Functional connectivity (ping/iperf succeeding) is not evidence of "
    "offload -- software-only forwarding produces identical "
    "application-level results.",
    "Absence of markers is common and expected on OVS-DPDK/userspace "
    "datapaths, some vendor OVS builds, and unprivileged runs; it does "
    "not prove software-only forwarding.",
    "ethtool counter names are vendor-specific; the generic highlight "
    "regex will miss some vendor counters and may highlight unrelated "
    "drop/discard counters.",
    "TC 'in_hw' is self-reported by the driver; drivers differ in when "
    "or whether they set it.",
    "Counter deltas collected with --duration measure activity during a "
    "time window, not necessarily the specific test traffic; avoid "
    "background traffic for a clean signal.",
]


# ---------------------------------------------------------------------------
# Safe command execution (same contract as tools/inventory/dpu_inventory.py)
# ---------------------------------------------------------------------------

def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(
    args: Sequence[str], timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """Run a command and never raise; failures become fields on the result."""
    binary = args[0]
    result: Dict[str, Any] = {
        "command": " ".join(args),
        "available": False,
        "returncode": None,
        "stdout": None,
        "stderr": None,
        "error": None,
    }

    if not command_exists(binary):
        result["error"] = f"'{binary}' not found on PATH"
        return result

    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result["available"] = True
        result["returncode"] = completed.returncode
        result["stdout"] = completed.stdout
        result["stderr"] = completed.stderr
        if completed.returncode != 0 and not (completed.stdout or "").strip():
            stderr_text = (completed.stderr or "").strip() or "no output"
            result["error"] = f"command exited {completed.returncode}: {stderr_text}"
    except subprocess.TimeoutExpired:
        result["error"] = f"command timed out after {timeout}s"
    except PermissionError as exc:
        result["error"] = f"permission denied: {exc}"
    except OSError as exc:
        result["error"] = f"OS error running command: {exc}"
    except Exception as exc:  # a collector must never crash the run
        result["error"] = f"unexpected error: {exc}"

    return result


def _stdout_lines(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [line.rstrip("\r") for line in raw.splitlines()]


def _safe_int(text: str) -> Optional[int]:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Flow / filter classification (vendor-neutral markers only)
# ---------------------------------------------------------------------------

def classify_datapath_flow(line: str) -> Tuple[str, List[str]]:
    """Return (classification, markers) for one dpctl dump-flows line.

    Classifications:
      probable_offloaded  -- explicit offloaded:yes, and/or dp:tc
      probable_partial    -- offloaded:partial
      software            -- offloaded:no, and/or dp:ovs with no offload yes
      unknown             -- no recognised marker
    """
    markers: List[str] = []
    if DP_OFFLOADED_YES.search(line):
        markers.append("offloaded:yes")
    if DP_OFFLOADED_PARTIAL.search(line):
        markers.append("offloaded:partial")
    if DP_OFFLOADED_NO.search(line):
        markers.append("offloaded:no")
    if DP_TC.search(line):
        markers.append("dp:tc")
    if DP_OVS.search(line):
        markers.append("dp:ovs")

    if "offloaded:yes" in markers:
        return "probable_offloaded", markers
    if "offloaded:partial" in markers:
        return "probable_partial", markers
    if "offloaded:no" in markers:
        return "software", markers
    if "dp:tc" in markers:
        # TC datapath is the usual kernel HW-offload vehicle, but is not
        # itself proof the filter is in hardware.
        return "probable_offloaded", markers
    if "dp:ovs" in markers:
        return "software", markers
    return "unknown", markers


def parse_dump_flows(raw: Optional[str]) -> List[Dict[str, Any]]:
    flows: List[Dict[str, Any]] = []
    if not raw:
        return flows
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip banners / counts that are not flow records.
        lower = stripped.lower()
        if lower.startswith("flow dump") or lower.startswith("netdev@"):
            continue
        classification, markers = classify_datapath_flow(stripped)
        flows.append(
            {
                "raw": stripped,
                "classification": classification,
                "markers": markers,
            }
        )
    return flows


def classify_tc_block(text: str) -> Tuple[str, List[str]]:
    markers: List[str] = []
    has_not_in_hw = bool(TC_NOT_IN_HW.search(text))
    count_match = TC_IN_HW_COUNT.search(text)
    remainder = TC_NOT_IN_HW.sub(" ", text)
    remainder = TC_IN_HW_COUNT.sub(" ", remainder)
    has_in_hw = bool(re.search(r"\bin_hw\b", remainder, re.IGNORECASE))

    if has_not_in_hw:
        markers.append("not_in_hw")
    if count_match:
        markers.append(f"in_hw_count:{count_match.group(1)}")
    if has_in_hw:
        markers.append("in_hw")

    if has_in_hw or count_match:
        return "probable_offloaded", markers
    if has_not_in_hw:
        return "software", markers
    return "unknown", markers


def parse_tc_filters(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Split `tc filter show` output into filter blocks and classify them."""
    if not raw or not raw.strip():
        return []

    blocks: List[str] = []
    current: List[str] = []
    for line in raw.splitlines():
        if line.startswith("filter ") and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    parsed: List[Dict[str, Any]] = []
    for block in blocks:
        classification, markers = classify_tc_block(block)
        parsed.append(
            {
                "raw": block.strip(),
                "classification": classification,
                "markers": markers,
            }
        )
    return parsed


def parse_ethtool_stats(raw: Optional[str]) -> Dict[str, Any]:
    counters: Dict[str, int] = {}
    unparsed: List[str] = []
    if raw:
        for line in raw.splitlines():
            match = ETHTOOL_COUNTER_LINE.match(line)
            if match:
                try:
                    counters[match.group("name")] = int(match.group("value"))
                    continue
                except ValueError:
                    pass
            stripped = line.strip()
            if stripped and not stripped.lower().startswith("nic statistics"):
                unparsed.append(stripped)

    highlighted = {
        name: value
        for name, value in counters.items()
        if COUNTER_HIGHLIGHT.search(name)
    }
    return {
        "counters": counters,
        "highlighted_counters": highlighted,
        "unparsed_lines": unparsed,
    }


def parse_ip_link_stats(raw: Optional[str]) -> Dict[str, Optional[int]]:
    """Extract RX/TX byte/packet/error/dropped counters from
    `ip -s link show <iface>` output. Best-effort: unrecognised layouts
    (older/newer iproute2 formats) leave fields absent rather than
    guessed.
    """
    stats: Dict[str, Optional[int]] = {}
    if not raw:
        return stats
    section: Optional[str] = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("RX:"):
            section = "rx"
            continue
        if stripped.startswith("TX:"):
            section = "tx"
            continue
        if section and re.match(r"^\d", stripped):
            values = stripped.split()
            if len(values) >= 4:
                stats[f"{section}_bytes"] = _safe_int(values[0])
                stats[f"{section}_packets"] = _safe_int(values[1])
                stats[f"{section}_errors"] = _safe_int(values[2])
                stats[f"{section}_dropped"] = _safe_int(values[3])
            section = None
    return stats


def parse_proc_stat_cpu_line(raw: Optional[str]) -> Optional[Dict[str, int]]:
    """Parse the aggregate `cpu ...` line of /proc/stat into named jiffie
    counters. Returns None if the line is missing or malformed.
    """
    if not raw:
        return None
    first_line = raw.splitlines()[0] if raw.splitlines() else ""
    parts = first_line.split()
    if len(parts) < 2 or parts[0] != "cpu":
        return None
    fields = [
        "user", "nice", "system", "idle", "iowait",
        "irq", "softirq", "steal", "guest", "guest_nice",
    ]
    values: Dict[str, int] = {}
    for name, raw_value in zip(fields, parts[1:]):
        parsed = _safe_int(raw_value)
        if parsed is not None:
            values[name] = parsed
    return values or None


# ---------------------------------------------------------------------------
# Collection steps
# ---------------------------------------------------------------------------

def collect_ovs_vsctl_show(timeout: int) -> Dict[str, Any]:
    result = run_command(["ovs-vsctl", "show"], timeout=timeout)
    return {"command": result, "stdout_lines": _stdout_lines(result.get("stdout"))}


def collect_dpctl_show(timeout: int, datapath: Optional[str]) -> Dict[str, Any]:
    args: List[str] = ["ovs-appctl", "dpctl/show"]
    if datapath:
        args.append(datapath)
    result = run_command(args, timeout=timeout)
    return {"command": result, "stdout_lines": _stdout_lines(result.get("stdout"))}


def collect_ofctl_dump_flows(timeout: int, bridge: Optional[str]) -> Dict[str, Any]:
    if not bridge:
        return {
            "command": None,
            "stdout_lines": [],
            "skipped": "no --bridge given (ovs-ofctl dump-flows needs a bridge)",
        }
    result = run_command(["ovs-ofctl", "dump-flows", bridge], timeout=timeout)
    return {"command": result, "stdout_lines": _stdout_lines(result.get("stdout"))}


def collect_dump_flows(
    timeout: int, more: bool, datapath: Optional[str]
) -> Dict[str, Any]:
    args: List[str] = ["ovs-appctl", "dpctl/dump-flows"]
    if more:
        args.append("-m")
    if datapath:
        args.append(datapath)
    result = run_command(args, timeout=timeout)
    flows = parse_dump_flows(result.get("stdout"))
    counts = _classification_counts(flows)
    return {
        "command": result,
        "flows": flows,
        "counts": counts,
    }


def collect_ofproto_trace(
    timeout: int, flow: Optional[str], bridge: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not flow:
        return None
    args: List[str] = ["ovs-appctl", "ofproto/trace"]
    if bridge:
        args.append(bridge)
    args.append(flow)
    result = run_command(args, timeout=timeout)
    return {
        "bridge": bridge,
        "flow": flow,
        "command": result,
        "stdout_lines": _stdout_lines(result.get("stdout")),
    }


def collect_tc_filters(timeout: int, interfaces: List[str]) -> Dict[str, Any]:
    """`tc -s filter show dev <iface> ingress` and `... egress` per interface.

    `-s` (statistics) is always passed so `in_hw_count` and per-filter
    packet/byte counters are present when the kernel/driver report them.
    """
    per_iface: List[Dict[str, Any]] = []
    for name in interfaces:
        entry: Dict[str, Any] = {"interface": name}
        for direction in TC_DIRECTIONS:
            result = run_command(
                ["tc", "-s", "filter", "show", "dev", name, direction],
                timeout=timeout,
            )
            filters = parse_tc_filters(result.get("stdout"))
            entry[direction] = {
                "command": result,
                "filters": filters,
                "counts": _classification_counts(filters),
            }
        per_iface.append(entry)
    return {"interfaces": per_iface}


def collect_ethtool_stats(timeout: int, interfaces: List[str]) -> Dict[str, Any]:
    per_iface: List[Dict[str, Any]] = []
    for name in interfaces:
        result = run_command(["ethtool", "-S", name], timeout=timeout)
        parsed = parse_ethtool_stats(result.get("stdout"))
        per_iface.append(
            {
                "interface": name,
                "command": result,
                **parsed,
            }
        )
    return {"interfaces": per_iface}


def collect_ip_link_stats(timeout: int, interfaces: List[str]) -> Dict[str, Any]:
    per_iface: List[Dict[str, Any]] = []
    for name in interfaces:
        result = run_command(["ip", "-s", "link", "show", name], timeout=timeout)
        parsed = parse_ip_link_stats(result.get("stdout"))
        per_iface.append({"interface": name, "command": result, "stats": parsed})
    return {"interfaces": per_iface}


def collect_cpu_snapshot(timeout: int) -> Dict[str, Any]:
    """Best-effort CPU snapshot: `mpstat` if installed, else /proc/stat.

    This is context, not offload evidence by itself -- see
    docs/test-plan/offload-validation.md for how a low/flat CPU curve
    alongside a driver in_hw marker is used as *corroborating* evidence,
    never as a standalone signal.
    """
    mpstat_result = run_command(["mpstat"], timeout=timeout)
    proc_stat_raw: Optional[str] = None
    proc_stat_error: Optional[str] = None
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            proc_stat_raw = handle.read()
    except OSError as exc:
        proc_stat_error = str(exc)
    proc_stat = parse_proc_stat_cpu_line(proc_stat_raw)
    source = "mpstat" if (mpstat_result.get("available") and not mpstat_result.get("error")) else (
        "proc_stat" if proc_stat else None
    )
    return {
        "source": source,
        "mpstat": mpstat_result,
        "proc_stat": proc_stat,
        "proc_stat_error": proc_stat_error,
    }


def _classification_counts(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {
        "probable_offloaded": 0,
        "probable_partial": 0,
        "software": 0,
        "unknown": 0,
        "total": 0,
    }
    for item in items:
        counts["total"] += 1
        key = item.get("classification", "unknown")
        if key not in counts:
            counts[key] = 0
        counts[key] += 1
    return counts


# ---------------------------------------------------------------------------
# --duration before/after deltas
# ---------------------------------------------------------------------------

def _diff_int_maps(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, int]:
    deltas: Dict[str, int] = {}
    for key, after_value in after.items():
        before_value = before.get(key)
        if isinstance(after_value, int) and isinstance(before_value, int):
            deltas[key] = after_value - before_value
    return deltas


def _diff_ethtool_section(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    after_by_iface = {i["interface"]: i for i in after.get("interfaces") or []}
    results: List[Dict[str, Any]] = []
    for b in before.get("interfaces") or []:
        a = after_by_iface.get(b["interface"])
        if a is None:
            continue
        deltas = _diff_int_maps(b.get("counters") or {}, a.get("counters") or {})
        highlighted_deltas = {
            name: value for name, value in deltas.items() if COUNTER_HIGHLIGHT.search(name)
        }
        results.append(
            {
                "interface": b["interface"],
                "deltas": deltas,
                "highlighted_deltas": highlighted_deltas,
            }
        )
    return results


def _diff_ip_link_section(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    after_by_iface = {i["interface"]: i for i in after.get("interfaces") or []}
    results: List[Dict[str, Any]] = []
    for b in before.get("interfaces") or []:
        a = after_by_iface.get(b["interface"])
        if a is None:
            continue
        deltas = _diff_int_maps(b.get("stats") or {}, a.get("stats") or {})
        results.append({"interface": b["interface"], "deltas": deltas})
    return results


def _diff_tc_section(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    after_by_iface = {i["interface"]: i for i in after.get("interfaces") or []}
    results: List[Dict[str, Any]] = []
    for b in before.get("interfaces") or []:
        a = after_by_iface.get(b["interface"])
        if a is None:
            continue
        entry: Dict[str, Any] = {"interface": b["interface"]}
        for direction in TC_DIRECTIONS:
            b_counts = (b.get(direction) or {}).get("counts") or {}
            a_counts = (a.get(direction) or {}).get("counts") or {}
            entry[f"{direction}_count_deltas"] = _diff_int_maps(b_counts, a_counts)
        results.append(entry)
    return results


def _diff_cpu(before: Dict[str, Any], after: Dict[str, Any]) -> Optional[Dict[str, int]]:
    b = before.get("proc_stat")
    a = after.get("proc_stat")
    if not b or not a:
        return None
    return _diff_int_maps(b, a)


def collect_duration_snapshot(
    timeout: int, interfaces: List[str]
) -> Dict[str, Any]:
    if interfaces:
        tc = collect_tc_filters(timeout=timeout, interfaces=interfaces)
        eth = collect_ethtool_stats(timeout=timeout, interfaces=interfaces)
        ip_link = collect_ip_link_stats(timeout=timeout, interfaces=interfaces)
    else:
        skipped = "no interfaces given (--interface/--interfaces/--pf)"
        tc = {"interfaces": [], "skipped": skipped}
        eth = {"interfaces": [], "skipped": skipped}
        ip_link = {"interfaces": [], "skipped": skipped}
    cpu = collect_cpu_snapshot(timeout)
    return {"tc_filters": tc, "ethtool_stats": eth, "ip_link_stats": ip_link, "cpu_snapshot": cpu}


def build_duration_section(
    *, timeout: int, interfaces: List[str], duration: float
) -> Dict[str, Any]:
    before = collect_duration_snapshot(timeout=timeout, interfaces=interfaces)
    time.sleep(duration)
    after = collect_duration_snapshot(timeout=timeout, interfaces=interfaces)
    return {
        "duration_seconds": duration,
        "before": before,
        "after": after,
        "ethtool_deltas": _diff_ethtool_section(
            before["ethtool_stats"], after["ethtool_stats"]
        ),
        "ip_link_deltas": _diff_ip_link_section(
            before["ip_link_stats"], after["ip_link_stats"]
        ),
        "tc_deltas": _diff_tc_section(before["tc_filters"], after["tc_filters"]),
        "cpu_delta": _diff_cpu(before["cpu_snapshot"], after["cpu_snapshot"]),
    }


# ---------------------------------------------------------------------------
# Probable-offload assessment (heuristic; see docs/test-plan/offload-validation.md)
# ---------------------------------------------------------------------------

def assess_offload(report: Dict[str, Any]) -> Dict[str, Any]:
    """Combine dpctl flow classification, TC filter classification
    (ingress+egress, all requested interfaces), and ethtool
    highlighted-counter presence into one probable status and a
    0.0-1.0 confidence score.

    This is intentionally simple and fully documented: each source
    contributes a signed "lean" (offloaded / software / partial) and a
    fixed weight only when that source actually returned classifiable
    data; sources with no data are excluded from the denominator
    (an environment without `ovs-appctl` should read `unknown`, not be
    penalized as `software_only`). See docs/test-plan/offload-validation.md
    for the worked examples this scoring is meant to match.
    """
    signals: List[Dict[str, Any]] = []
    warnings: List[str] = []

    dump = report.get("dpctl_dump_flows") or {}
    dump_cmd = dump.get("command") or {}
    dump_counts = dump.get("counts") or {}
    if dump_cmd.get("error"):
        warnings.append(f"ovs-appctl dpctl/dump-flows: {dump_cmd['error']}")
    elif not dump_counts.get("total"):
        warnings.append("ovs-appctl dpctl/dump-flows returned no flow entries")
    else:
        off = dump_counts.get("probable_offloaded", 0)
        part = dump_counts.get("probable_partial", 0)
        sw = dump_counts.get("software", 0)
        total = dump_counts.get("total", 0)
        if off and not sw and not part:
            signals.append(
                {
                    "source": "dpctl_dump_flows",
                    "weight": 2.0,
                    "lean": "offloaded",
                    "detail": f"{off}/{total} datapath flow(s) marked offloaded, none marked software",
                }
            )
        elif sw and not off and not part:
            signals.append(
                {
                    "source": "dpctl_dump_flows",
                    "weight": 2.0,
                    "lean": "software",
                    "detail": f"{sw}/{total} datapath flow(s) marked software, none marked offloaded",
                }
            )
        elif off or part:
            signals.append(
                {
                    "source": "dpctl_dump_flows",
                    "weight": 1.0,
                    "lean": "partial",
                    "detail": f"mixed dpctl markers: {off} offloaded, {part} partial, {sw} software (of {total})",
                }
            )
        else:
            warnings.append(
                "ovs-appctl dpctl/dump-flows: flows present but no recognised offload marker"
            )

    tc_section = report.get("tc_filters") or {}
    if tc_section.get("skipped"):
        warnings.append(f"tc filter collection skipped ({tc_section['skipped']})")
    else:
        tc_total = tc_off = tc_sw = 0
        for iface in tc_section.get("interfaces") or []:
            for direction in TC_DIRECTIONS:
                counts = (iface.get(direction) or {}).get("counts") or {}
                tc_total += counts.get("total", 0)
                tc_off += counts.get("probable_offloaded", 0)
                tc_sw += counts.get("software", 0)
        if not tc_total:
            warnings.append("tc filter show returned no filters on any requested interface")
        elif tc_off and not tc_sw:
            signals.append(
                {
                    "source": "tc_filters",
                    "weight": 2.0,
                    "lean": "offloaded",
                    "detail": f"{tc_off}/{tc_total} TC filter(s) reported in_hw across ingress+egress",
                }
            )
        elif tc_sw and not tc_off:
            signals.append(
                {
                    "source": "tc_filters",
                    "weight": 2.0,
                    "lean": "software",
                    "detail": f"{tc_sw}/{tc_total} TC filter(s) reported not_in_hw, none in_hw",
                }
            )
        elif tc_off:
            signals.append(
                {
                    "source": "tc_filters",
                    "weight": 1.0,
                    "lean": "partial",
                    "detail": f"mixed TC markers: {tc_off} in_hw, {tc_sw} not_in_hw (of {tc_total})",
                }
            )

    eth_section = report.get("ethtool_stats") or {}
    if eth_section.get("skipped"):
        warnings.append(f"ethtool collection skipped ({eth_section['skipped']})")
    else:
        nonzero_highlighted = 0
        for iface in eth_section.get("interfaces") or []:
            for _name, value in (iface.get("highlighted_counters") or {}).items():
                if value:
                    nonzero_highlighted += 1
        if nonzero_highlighted:
            signals.append(
                {
                    "source": "ethtool_stats",
                    "weight": 1.0,
                    "lean": "offloaded",
                    "detail": (
                        f"{nonzero_highlighted} generically-named offload/hw counter(s) "
                        "are nonzero (see JSON for names; not vendor-mapped)"
                    ),
                }
            )
        elif eth_section.get("interfaces"):
            warnings.append(
                "ethtool -S: no generically-named offload/hw counters were nonzero "
                "(does not rule out offload -- vendor counter names vary)"
            )

    if not signals:
        status, confidence = "unknown", 0.0
    else:
        # A "partial" lean (mixed evidence *within* one source, e.g. some
        # flows offloaded and some not) contributes half its weight to
        # each side rather than being scored as its own direction — it
        # is uncertainty, not a vote for either outcome.
        offload_leaning = sum(
            s["weight"] if s["lean"] == "offloaded" else s["weight"] * 0.5 if s["lean"] == "partial" else 0.0
            for s in signals
        )
        software_leaning = sum(
            s["weight"] if s["lean"] == "software" else s["weight"] * 0.5 if s["lean"] == "partial" else 0.0
            for s in signals
        )
        considered_weight = offload_leaning + software_leaning
        net = offload_leaning - software_leaning
        if considered_weight == 0:
            status, confidence = "unknown", 0.0
        else:
            confidence = round(abs(net) / considered_weight, 2)
            if net > 0:
                status = "probable_offloaded" if confidence >= 0.5 else "probable_partial"
            elif net < 0:
                status = "software_only" if confidence >= 0.5 else "probable_partial"
            else:
                status = "probable_partial" if any(s["lean"] == "partial" for s in signals) else "unknown"

    return {
        "status": status,
        "confidence": confidence,
        "evidence": [f"[{s['source']}] {s['detail']}" for s in signals],
        "warnings": warnings,
        "limitations": OFFLOAD_ASSESSMENT_LIMITATIONS,
    }


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

def run_plugins(
    plugins: List[Any],
    interfaces: List[str],
    report: Dict[str, Any],
    vendor: Optional[str],
) -> Dict[str, Any]:
    plugin_section: Dict[str, Any] = {
        "loaded": [getattr(plugin, "name", plugin.__class__.__name__) for plugin in plugins],
        "results": {},
    }
    context = {"interfaces": interfaces, "report": report, "vendor": vendor}
    for plugin in plugins:
        name = getattr(plugin, "name", plugin.__class__.__name__)
        try:
            extra = plugin.extra_sections(context) or {}
        except Exception as exc:
            extra = {"error": f"plugin raised: {exc}"}
        plugin_section["results"][name] = extra

        dump = report.get("dpctl_dump_flows") or {}
        for flow in dump.get("flows") or []:
            try:
                plugin.annotate_flow(flow)
            except Exception:
                continue
    return plugin_section


def _planned_command(argv: List[str]) -> Dict[str, Any]:
    return {
        "command": {
            "command": " ".join(argv),
            "available": False,
            "returncode": None,
            "stdout": None,
            "stderr": None,
            "error": "DPU probe planned (not executed; dry-run / no samples)",
            "argv": argv,
        },
        "stdout_lines": [],
        "planned": True,
    }


def _dpu_probe_section(argv: List[str], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Plan or run a DPU-side probe over SSH. Never treats local host
    ovs-vsctl as DPU proof.

    Every argv passed in here is a read-only inspection command
    (ovs-vsctl show, dpctl/show, dump-flows, tc -s filter show,
    ethtool -S) -- this tool has no --apply/--dry-run flag of its own,
    so there is no destructive-operation gate to honor. Whether it is
    actually sent over SSH is instead controlled by the same
    remote_execution.dry_run_default config knob that
    vf_to_representor_map.py's collect_dpu_samples_via_ssh() honors
    (defaults to True/planned, so pointing --config at a real lab
    without setting dry_run_default: false does not silently start
    SSHing to whatever management_ip happens to be in the file).
    Falls back to _planned_command() when dpu.management_ip/ssh_user
    are not configured (nothing to SSH to).
    """
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from plugins.base.remote import RemoteCommandRunner, ssh_params
    except Exception:
        return _planned_command(argv)
    params = ssh_params(config or {})
    if not params.get("host") or not params.get("user"):
        return _planned_command(argv)
    remote = (config or {}).get("remote_execution") or {}
    dry_run = bool(remote.get("dry_run_default", True))
    command_timeout = params["command_timeout_seconds"]
    result = RemoteCommandRunner().run_ssh(
        params["host"],
        params["user"],
        list(argv),
        key_path=params.get("key_path"),
        port=int(params.get("port") or 22),
        dry_run=dry_run,
        timeout=command_timeout,
        reason="dpu offload probe",
        sudo=bool(params.get("sudo")),
    )
    if dry_run:
        # Same shape as _planned_command(), but with the real ssh-wrapped
        # argv (via RemoteCommandRunner) instead of the bare inner command,
        # so operators can see exactly what would run.
        return {
            "command": {
                "command": " ".join(result.argv),
                "available": False,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": "DPU probe planned (not executed; dry-run / no samples)",
                "argv": list(result.argv),
            },
            "stdout_lines": [],
            "planned": True,
        }
    error: Optional[str] = None
    if result.timed_out:
        error = (
            "DPU probe timed out after %ss (increase dpu.command_timeout_seconds "
            "if this is a slow link; see docs/operations/dpu-remote-execution.md)"
            % command_timeout
        )
    elif result.returncode not in (0, None):
        error = "DPU probe failed (returncode=%s): %s" % (
            result.returncode,
            (result.stderr or "").strip(),
        )
    elif result.stdout is None:
        error = "DPU probe returned no output"
    return {
        "command": {
            "command": " ".join(result.argv),
            "available": error is None,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": error,
            "argv": list(result.argv),
        },
        "stdout_lines": _stdout_lines(result.stdout),
        "planned": False,
    }


def _section_from_sample(stdout: Optional[str], argv: List[str]) -> Dict[str, Any]:
    return {
        "command": {
            "command": " ".join(argv),
            "available": stdout is not None,
            "returncode": 0 if stdout is not None else None,
            "stdout": stdout,
            "stderr": None,
            "error": None if stdout is not None else "no sample",
            "argv": argv,
        },
        "stdout_lines": _stdout_lines(stdout),
        "planned": False,
    }


def location_from_forwarding(forwarding_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not forwarding_result:
        return None
    if str(forwarding_result.get("ovs_location") or "").lower() == "dpu":
        return "dpu"
    control = str(forwarding_result.get("control_entity") or "")
    entity = str(forwarding_result.get("forwarding_entity") or "")
    blob = (control + " " + entity).upper()
    if "DPU" in blob or "REMOTECOMMANDRUNNER" in blob.replace(" ", ""):
        return "dpu"
    return None


def attach_evidence_sections(
    report: Dict[str, Any],
    *,
    location: str,
    vsctl_show: Dict[str, Any],
    dpctl_show: Dict[str, Any],
    dump: Dict[str, Any],
    ofctl_dump: Dict[str, Any],
    tc_section: Dict[str, Any],
    ethtool_section: Dict[str, Any],
    cpu_snapshot: Any,
    duration_section: Optional[Dict[str, Any]],
    forwarding_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    dpu_block = {
        "ovs_vsctl_show": vsctl_show,
        "ofctl_dump_flows": ofctl_dump,
        "dpctl_show": dpctl_show,
        "dpctl_dump_flows": dump,
        "tc_filters": tc_section,
        "ethtool_stats": ethtool_section,
    }
    host_block = {
        "cpu_snapshot": cpu_snapshot,
        "note": None,
    }
    if location == "dpu":
        host_block["note"] = (
            "Host OVS is not the forwarding owner in DPU mode; host ovs-vsctl "
            "is not DPU offload proof."
        )
        report["dpu_evidence"] = dpu_block
        report["host_evidence"] = host_block
    else:
        host_block.update(dpu_block)
        report["host_evidence"] = host_block
        report["dpu_evidence"] = None
    report["vm_evidence"] = {
        "vf_pci_addresses": list((forwarding_result or {}).get("vf_pci_addresses") or []),
        "representors": list((forwarding_result or {}).get("representors") or []),
        "note": "Guest/VF counters are not invented; collect via VM SSH when available.",
    }
    report["traffic_evidence"] = duration_section
    report["offload_confidence"] = report.get("assessment")
    report["location"] = location
    if str(report.get("inputs", {}).get("vendor") or "").lower() in ("marvell", "marvell-octeon"):
        report["marvell_dao_evidence"] = {
            "dao_available": None,
            "dao_running": None,
            "dpdk_ports": [],
            "representors": [],
            "flow_offload_indicators": None,
            "confidence": "unknown",
            "warnings": [
                "Host ovs-vsctl is not Marvell DAO offload proof. Use plugins.marvell_octeon.dao_telemetry."
            ],
        }
    return report

def build_report(
    *,
    interfaces: List[str],
    pf: Optional[str],
    vendor: Optional[str],
    flow: Optional[str],
    bridge: Optional[str],
    more: bool,
    datapath: Optional[str],
    duration: float,
    timeout: int,
    plugins: List[Any],
    forwarding_result: Optional[Dict[str, Any]] = None,
    forwarding_result_path: Optional[str] = None,
    location: Optional[str] = None,
    samples: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_location = location or location_from_forwarding(forwarding_result) or "host"
    if samples:
        vsctl_show = _section_from_sample(samples.get("ovs_vsctl_show"), ["ovs-vsctl", "show"])
        dpctl_show = _section_from_sample(samples.get("dpctl_show"), ["ovs-appctl", "dpctl/show"])
        dump_stdout = samples.get("dpctl_dump_flows")
        dump = {
            **_section_from_sample(dump_stdout, ["ovs-appctl", "dpctl/dump-flows"]),
            "flows": parse_dump_flows(dump_stdout),
            "counts": _classification_counts(parse_dump_flows(dump_stdout)),
        }
        ofctl_dump = _section_from_sample(
            samples.get("ofctl_dump_flows"),
            ["ovs-ofctl", "dump-flows", bridge or ""],
        )
        tc_section = {"interfaces": samples.get("tc_filters") or [], "source": "samples"}
        ethtool_section = {"interfaces": samples.get("ethtool_stats") or [], "source": "samples"}
        ip_link_section = {"interfaces": [], "skipped": "samples mode"}
        cpu_snapshot = samples.get("cpu_snapshot")
        duration_section = samples.get("duration")
        trace = None
    elif resolved_location == "dpu":
        vsctl_show = _dpu_probe_section(["ovs-vsctl", "show"], config)
        dpctl_show = _dpu_probe_section(
            ["ovs-appctl", "dpctl/show"] + ([datapath] if datapath else []), config
        )
        dump_cmd = ["ovs-appctl", "dpctl/dump-flows"]
        if more:
            dump_cmd.append("-m")
        dump_section = _dpu_probe_section(dump_cmd, config)
        dump_flows = parse_dump_flows(dump_section["command"].get("stdout"))
        dump = {
            **dump_section,
            "flows": dump_flows,
            "counts": _classification_counts(dump_flows),
        }
        ofctl_dump = (
            _dpu_probe_section(["ovs-ofctl", "dump-flows", bridge], config)
            if bridge
            else {"command": None, "stdout_lines": [], "skipped": "no --bridge given"}
        )
        # assess_offload() reads tc_filters as [{"interface": name,
        # "ingress": {"filters":.., "counts":..}, "egress": {...}}, ...]
        # (see collect_tc_filters() below, the host-mode equivalent) and
        # ethtool_stats as [{"interface": name, "highlighted_counters":..},
        # ...] -- both directions are probed, like the host path, so a
        # filter that is only offloaded/only software on one direction
        # isn't silently dropped from the count.
        tc_per_iface = []
        ethtool_per_iface = []
        for name in interfaces:
            entry: Dict[str, Any] = {"interface": name}
            for direction in TC_DIRECTIONS:
                probe = _dpu_probe_section(
                    ["tc", "-s", "filter", "show", "dev", name, direction], config
                )
                filters = parse_tc_filters(probe["command"].get("stdout"))
                entry[direction] = {
                    **probe,
                    "filters": filters,
                    "counts": _classification_counts(filters),
                }
            tc_per_iface.append(entry)
            eth_probe = _dpu_probe_section(["ethtool", "-S", name], config)
            parsed = parse_ethtool_stats(eth_probe["command"].get("stdout"))
            ethtool_per_iface.append({"interface": name, **eth_probe, **parsed})
        # Each entry's own "planned" flag (from _dpu_probe_section) reflects
        # whether it was actually executed (False) or fell back to a plan
        # because dpu.management_ip/ssh_user is not configured (True).
        tc_section = {"interfaces": tc_per_iface}
        ethtool_section = {"interfaces": ethtool_per_iface}
        ip_link_section = {"interfaces": [], "skipped": "DPU location: host ip -s link is not DPU evidence"}
        cpu_snapshot = {"skipped": "host /proc/stat is not DPU offload evidence"}
        duration_section = None
        trace = None
    else:
        vsctl_show = collect_ovs_vsctl_show(timeout)
        dpctl_show = collect_dpctl_show(timeout=timeout, datapath=datapath)
        dump = collect_dump_flows(timeout=timeout, more=more, datapath=datapath)
        ofctl_dump = collect_ofctl_dump_flows(timeout=timeout, bridge=bridge)
        trace = collect_ofproto_trace(timeout=timeout, flow=flow, bridge=bridge)

        if interfaces:
            tc_section = collect_tc_filters(timeout=timeout, interfaces=interfaces)
            ethtool_section = collect_ethtool_stats(timeout=timeout, interfaces=interfaces)
            ip_link_section = collect_ip_link_stats(timeout=timeout, interfaces=interfaces)
        else:
            skipped = "no interfaces given (--interface/--interfaces/--pf)"
            tc_section = {"interfaces": [], "skipped": skipped}
            ethtool_section = {"interfaces": [], "skipped": skipped}
            ip_link_section = {"interfaces": [], "skipped": skipped}
        cpu_snapshot = collect_cpu_snapshot(timeout)

        duration_section = None
        if duration and duration > 0:
            duration_section = build_duration_section(
                timeout=timeout, interfaces=interfaces, duration=duration
            )

    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": os.uname().nodename if hasattr(os, "uname") else None,
        "inputs": {
            "interfaces": interfaces,
            "pf": pf,
            "vendor": vendor,
            "flow": flow,
            "bridge": bridge,
            "dump_flows_more": more,
            "datapath": datapath,
            "duration_seconds": duration,
            "forwarding_result_path": forwarding_result_path,
            "location": resolved_location,
        },
        "forwarding_result": forwarding_result,
        "ovs_vsctl_show": vsctl_show,
        "dpctl_show": dpctl_show,
        "dpctl_dump_flows": dump,
        "ofctl_dump_flows": ofctl_dump,
        "ofproto_trace": trace,
        "tc_filters": tc_section,
        "ethtool_stats": ethtool_section,
        "ip_link_stats": ip_link_section,
        "cpu_snapshot": cpu_snapshot,
        "duration": duration_section,
    }
    report["assessment"] = assess_offload(report)
    report["summary"] = {
        "datapath_flows": dump.get("counts"),
        "note": (
            "Classifications are heuristic. Markers such as offloaded:yes, "
            "dp:tc, and TC in_hw indicate *probable* hardware offload; "
            "their absence does not prove software-only forwarding. See "
            "report['assessment'] for the combined status/confidence and "
            "docs/test-plan/offload-validation.md for how to read it."
        ),
    }
    report["plugins"] = run_plugins(plugins, interfaces, report, vendor)
    attach_evidence_sections(
        report,
        location=resolved_location,
        vsctl_show=vsctl_show,
        dpctl_show=dpctl_show,
        dump=dump,
        ofctl_dump=ofctl_dump,
        tc_section=tc_section,
        ethtool_section=ethtool_section,
        cpu_snapshot=cpu_snapshot,
        duration_section=duration_section,
        forwarding_result=forwarding_result,
    )
    return report


# ---------------------------------------------------------------------------
# Human-readable rendering
# ---------------------------------------------------------------------------

def _label(classification: str) -> str:
    return {
        "probable_offloaded": "OFFLOADED?",
        "probable_partial": "PARTIAL?",
        "software": "SOFTWARE",
        "unknown": "UNKNOWN",
    }.get(classification, classification.upper())


def render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("ODSE OVS offload checker")
    lines.append(f"schema: {report.get('schema_version')}")
    lines.append(f"timestamp: {report.get('timestamp')}")
    lines.append(f"host: {report.get('host')}")
    lines.append("")

    assessment = report.get("assessment") or {}
    lines.append("== assessment ==")
    lines.append(f"status: {assessment.get('status')}  confidence: {assessment.get('confidence')}")
    for item in assessment.get("evidence") or []:
        lines.append(f"  + {item}")
    for item in assessment.get("warnings") or []:
        lines.append(f"  ! {item}")
    lines.append("")

    forwarding_result = report.get("forwarding_result")
    if forwarding_result is not None:
        lines.append("== forwarding result (--forwarding-result) ==")
        if forwarding_result.get("error"):
            lines.append(f"error: {forwarding_result['error']}")
        else:
            lines.append(f"name: {forwarding_result.get('name')}")
            lines.append(f"bridge: {forwarding_result.get('bridge')}")
            lines.append(f"configured_ports: {', '.join(forwarding_result.get('configured_ports') or []) or '(none)'}")
            lines.append(f"configured_flows: {', '.join(forwarding_result.get('configured_flows') or []) or '(none)'}")
            lines.append(f"vf_pci_addresses: {', '.join(forwarding_result.get('vf_pci_addresses') or []) or '(none)'}")
            lines.append(f"representors: {', '.join(forwarding_result.get('representors') or []) or '(none)'}")
            lines.append(f"offload_requested: {forwarding_result.get('offload_requested')}")
            lines.append(f"offload_configured: {forwarding_result.get('offload_configured')}")
        lines.append("")

    vsctl = report.get("ovs_vsctl_show") or {}
    lines.append("== ovs-vsctl show ==")
    vcmd = vsctl.get("command") or {}
    if vcmd.get("error"):
        lines.append(f"error: {vcmd.get('error')}")
    else:
        for row in vsctl.get("stdout_lines") or []:
            lines.append(f"  {row}")
    lines.append("")

    dshow = report.get("dpctl_show") or {}
    lines.append("== ovs-appctl dpctl/show ==")
    dcmd = dshow.get("command") or {}
    if dcmd.get("error"):
        lines.append(f"error: {dcmd.get('error')}")
    else:
        for row in dshow.get("stdout_lines") or []:
            lines.append(f"  {row}")
    lines.append("")

    dump = report.get("dpctl_dump_flows") or {}
    cmd = dump.get("command") or {}
    counts = dump.get("counts") or {}
    lines.append("== ovs-appctl dpctl/dump-flows ==")
    lines.append(f"command: {cmd.get('command')}")
    if cmd.get("error"):
        lines.append(f"error: {cmd.get('error')}")
    lines.append(
        "counts: total={total} probable_offloaded={probable_offloaded} "
        "probable_partial={probable_partial} software={software} unknown={unknown}".format(
            **{k: counts.get(k, 0) for k in (
                "total",
                "probable_offloaded",
                "probable_partial",
                "software",
                "unknown",
            )}
        )
    )
    for flow in dump.get("flows") or []:
        marker_txt = ",".join(flow.get("markers") or []) or "-"
        lines.append(
            f"  [{_label(flow.get('classification', 'unknown'))}] "
            f"markers={marker_txt}"
        )
        lines.append(f"    {flow.get('raw')}")
    if not dump.get("flows") and not cmd.get("error"):
        lines.append("  (no datapath flows reported)")
    lines.append("")

    ofctl = report.get("ofctl_dump_flows") or {}
    lines.append("== ovs-ofctl dump-flows <bridge> ==")
    if ofctl.get("skipped"):
        lines.append(f"skipped ({ofctl['skipped']})")
    else:
        ocmd = ofctl.get("command") or {}
        if ocmd.get("error"):
            lines.append(f"error: {ocmd.get('error')}")
        for row in ofctl.get("stdout_lines") or []:
            lines.append(f"  {row}")
    lines.append("")

    trace = report.get("ofproto_trace")
    lines.append("== ovs-appctl ofproto/trace ==")
    if not trace:
        lines.append("skipped (no --flow provided)")
    else:
        tcmd = trace.get("command") or {}
        lines.append(f"command: {tcmd.get('command')}")
        if tcmd.get("error"):
            lines.append(f"error: {tcmd.get('error')}")
        for row in trace.get("stdout_lines") or []:
            lines.append(f"  {row}")
        if not trace.get("stdout_lines") and not tcmd.get("error"):
            lines.append("  (empty trace output)")
    lines.append("")

    tc_section = report.get("tc_filters") or {}
    lines.append("== tc -s filter show ... ingress|egress ==")
    if tc_section.get("skipped"):
        lines.append(f"skipped ({tc_section.get('skipped')})")
    for iface in tc_section.get("interfaces") or []:
        lines.append(f"interface: {iface.get('interface')}")
        for direction in TC_DIRECTIONS:
            dir_section = iface.get(direction) or {}
            icmd = dir_section.get("command") or {}
            icounts = dir_section.get("counts") or {}
            lines.append(f"  [{direction}]")
            if icmd.get("error"):
                lines.append(f"    error: {icmd.get('error')}")
            lines.append(
                "    counts: total={total} probable_offloaded={probable_offloaded} "
                "software={software} unknown={unknown}".format(
                    total=icounts.get("total", 0),
                    probable_offloaded=icounts.get("probable_offloaded", 0),
                    software=icounts.get("software", 0),
                    unknown=icounts.get("unknown", 0),
                )
            )
            for filt in dir_section.get("filters") or []:
                marker_txt = ",".join(filt.get("markers") or []) or "-"
                lines.append(
                    f"    [{_label(filt.get('classification', 'unknown'))}] "
                    f"markers={marker_txt}"
                )
    lines.append("")

    eth_section = report.get("ethtool_stats") or {}
    lines.append("== ethtool -S ==")
    if eth_section.get("skipped"):
        lines.append(f"skipped ({eth_section.get('skipped')})")
    for iface in eth_section.get("interfaces") or []:
        lines.append(f"interface: {iface.get('interface')}")
        ecmd = iface.get("command") or {}
        if ecmd.get("error"):
            lines.append(f"  error: {ecmd.get('error')}")
        highlighted = iface.get("highlighted_counters") or {}
        if highlighted:
            lines.append("  highlighted counters (generic name match):")
            for name, value in sorted(highlighted.items()):
                lines.append(f"    {name}: {value}")
        else:
            lines.append("  (no generic offload-like counter names matched)")
        n_counters = len(iface.get("counters") or {})
        lines.append(f"  total counters captured: {n_counters} (full set in JSON)")
    lines.append("")

    ip_section = report.get("ip_link_stats") or {}
    lines.append("== ip -s link show ==")
    if ip_section.get("skipped"):
        lines.append(f"skipped ({ip_section.get('skipped')})")
    for iface in ip_section.get("interfaces") or []:
        lines.append(f"interface: {iface.get('interface')}")
        icmd = iface.get("command") or {}
        if icmd.get("error"):
            lines.append(f"  error: {icmd.get('error')}")
        stats = iface.get("stats") or {}
        for name, value in sorted(stats.items()):
            lines.append(f"  {name}: {value}")
    lines.append("")

    cpu = report.get("cpu_snapshot") or {}
    lines.append("== CPU snapshot ==")
    lines.append(f"source: {cpu.get('source')}")
    if cpu.get("proc_stat"):
        lines.append(f"  /proc/stat cpu: {cpu['proc_stat']}")
    lines.append("")

    duration_section = report.get("duration")
    lines.append("== --duration before/after deltas ==")
    if not duration_section:
        lines.append("skipped (no --duration > 0 given)")
    else:
        lines.append(f"duration_seconds: {duration_section.get('duration_seconds')}")
        for entry in duration_section.get("ethtool_deltas") or []:
            highlighted = entry.get("highlighted_deltas") or {}
            if highlighted:
                lines.append(f"  ethtool[{entry['interface']}] highlighted deltas: {highlighted}")
        for entry in duration_section.get("tc_deltas") or []:
            lines.append(f"  tc[{entry['interface']}]: {entry}")
        if duration_section.get("cpu_delta"):
            lines.append(f"  cpu_delta: {duration_section['cpu_delta']}")
    lines.append("")

    plugins = report.get("plugins") or {}
    lines.append("== vendor plugins ==")
    loaded = plugins.get("loaded") or []
    if not loaded:
        lines.append("none loaded (core report is vendor-neutral)")
    else:
        lines.append("loaded: " + ", ".join(loaded))
        for name, extra in (plugins.get("results") or {}).items():
            lines.append(f"  [{name}] {json.dumps(extra, default=str)}")
    lines.append("")

    for item in assessment.get("limitations") or []:
        lines.append("limitation: " + item)
    lines.append("")
    return "\n".join(lines)


def render_markdown(report: Dict[str, Any]) -> str:
    inputs = report.get("inputs") or {}
    assessment = report.get("assessment") or {}
    lines: List[str] = []

    lines.append("# OVS Offload Validation Report")
    lines.append("")
    lines.append(f"- **Schema:** `{report.get('schema_version')}`")
    lines.append(f"- **Timestamp:** {report.get('timestamp')}")
    lines.append(f"- **Host:** {report.get('host')}")
    lines.append(f"- **Interfaces:** {', '.join(inputs.get('interfaces') or []) or '(none)'}")
    if inputs.get("pf"):
        lines.append(f"- **PF:** {inputs['pf']}")
    if inputs.get("bridge"):
        lines.append(f"- **Bridge:** {inputs['bridge']}")
    if inputs.get("vendor"):
        lines.append(f"- **Vendor hint:** {inputs['vendor']}")
    lines.append("")

    lines.append("## Assessment")
    lines.append("")
    lines.append(f"**Status:** `{assessment.get('status')}` &nbsp;&nbsp; **Confidence:** {assessment.get('confidence')}")
    lines.append("")
    lines.append(
        "> Heuristic only, not proof of offload. See "
        "`docs/test-plan/offload-validation.md`."
    )
    lines.append("")
    lines.append("### Evidence")
    lines.append("")
    if assessment.get("evidence"):
        for item in assessment["evidence"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (no offload or software-only evidence collected)")
    lines.append("")
    lines.append("### Warnings")
    lines.append("")
    if assessment.get("warnings"):
        for item in assessment["warnings"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("### Limitations")
    lines.append("")
    for item in assessment.get("limitations") or []:
        lines.append(f"- {item}")
    lines.append("")

    forwarding_result = report.get("forwarding_result")
    if forwarding_result is not None:
        lines.append("## Forwarding Result (`--forwarding-result`)")
        lines.append("")
        if forwarding_result.get("error"):
            lines.append(f"> {forwarding_result['error']}")
        else:
            lines.append(f"- **Name:** {forwarding_result.get('name')}")
            lines.append(f"- **Bridge:** {forwarding_result.get('bridge')}")
            lines.append(f"- **Configured ports:** {', '.join(forwarding_result.get('configured_ports') or []) or '(none)'}")
            lines.append(f"- **Configured flows:** {', '.join(forwarding_result.get('configured_flows') or []) or '(none)'}")
            lines.append(f"- **VF PCI addresses:** {', '.join(forwarding_result.get('vf_pci_addresses') or []) or '(none)'}")
            lines.append(f"- **Representors:** {', '.join(forwarding_result.get('representors') or []) or '(none)'}")
            lines.append(f"- **Offload requested:** {forwarding_result.get('offload_requested')}")
            lines.append(f"- **Offload configured:** {forwarding_result.get('offload_configured')}")
        lines.append("")

    dump = report.get("dpctl_dump_flows") or {}
    counts = dump.get("counts") or {}
    lines.append("## `ovs-appctl dpctl/dump-flows`")
    lines.append("")
    lines.append("| Classification | Count |")
    lines.append("|---|---|")
    for key in ("probable_offloaded", "probable_partial", "software", "unknown", "total"):
        lines.append(f"| {key} | {counts.get(key, 0)} |")
    lines.append("")

    tc_section = report.get("tc_filters") or {}
    lines.append("## `tc -s filter show ... ingress|egress`")
    lines.append("")
    if tc_section.get("skipped"):
        lines.append(f"Skipped: {tc_section['skipped']}")
    else:
        lines.append("| Interface | Direction | Offloaded | Software | Unknown | Total |")
        lines.append("|---|---|---|---|---|---|")
        for iface in tc_section.get("interfaces") or []:
            for direction in TC_DIRECTIONS:
                c = (iface.get(direction) or {}).get("counts") or {}
                lines.append(
                    f"| {iface.get('interface')} | {direction} | "
                    f"{c.get('probable_offloaded', 0)} | {c.get('software', 0)} | "
                    f"{c.get('unknown', 0)} | {c.get('total', 0)} |"
                )
    lines.append("")

    eth_section = report.get("ethtool_stats") or {}
    lines.append("## `ethtool -S` (highlighted counters)")
    lines.append("")
    if eth_section.get("skipped"):
        lines.append(f"Skipped: {eth_section['skipped']}")
    else:
        any_highlighted = False
        for iface in eth_section.get("interfaces") or []:
            highlighted = iface.get("highlighted_counters") or {}
            if not highlighted:
                continue
            any_highlighted = True
            lines.append(f"**{iface.get('interface')}**")
            lines.append("")
            lines.append("| Counter | Value |")
            lines.append("|---|---|")
            for name, value in sorted(highlighted.items()):
                lines.append(f"| `{name}` | {value} |")
            lines.append("")
        if not any_highlighted:
            lines.append("No generically-named offload/hw counters were nonzero.")
            lines.append("")

    duration_section = report.get("duration")
    lines.append("## `--duration` before/after deltas")
    lines.append("")
    if not duration_section:
        lines.append("Skipped (no `--duration > 0` given).")
    else:
        lines.append(f"Window: {duration_section.get('duration_seconds')}s")
        lines.append("")
        for entry in duration_section.get("ethtool_deltas") or []:
            if entry.get("highlighted_deltas"):
                lines.append(f"- `{entry['interface']}` highlighted counter deltas: `{entry['highlighted_deltas']}`")
        for entry in duration_section.get("tc_deltas") or []:
            lines.append(f"- `{entry['interface']}` TC in_hw-count deltas: `{entry}`")
        if duration_section.get("cpu_delta"):
            lines.append(f"- CPU jiffie deltas: `{duration_section['cpu_delta']}`")
    lines.append("")

    plugins = report.get("plugins") or {}
    lines.append("## Vendor plugins")
    lines.append("")
    loaded = plugins.get("loaded") or []
    if not loaded:
        lines.append("None loaded (core report is vendor-neutral).")
    else:
        lines.append("Loaded: " + ", ".join(f"`{name}`" for name in loaded))
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect OVS and Linux datapath state and highlight probable "
            "hardware-offload markers. Vendor-neutral core; optional plugins."
        )
    )
    parser.add_argument(
        "-i",
        "--interface",
        action="append",
        dest="interface_list",
        default=[],
        metavar="IFACE",
        help="Netdev to query with `tc`/`ethtool -S`/`ip -s link`. Repeatable. "
        "Merged with --interfaces and --pf.",
    )
    parser.add_argument(
        "--interfaces",
        dest="interfaces_csv",
        default=None,
        metavar="IFACE1,IFACE2",
        help="Comma-separated netdevs, merged with -i/--interface and --pf.",
    )
    parser.add_argument(
        "--pf",
        dest="pf",
        default=None,
        metavar="PF_NAME",
        help="PF netdev name. Recorded under inputs.pf and added to the "
        "interface list (deduplicated) so its tc/ethtool/ip-link state is "
        "collected too.",
    )
    parser.add_argument(
        "--vendor",
        dest="vendor",
        default=None,
        metavar="VENDOR",
        help="Vendor hint, recorded under inputs.vendor and passed to plugin "
        "context. Informational only in core (no vendor branching here).",
    )
    parser.add_argument(
        "--forwarding-result",
        dest="forwarding_result",
        default=None,
        metavar="PATH",
        help="Path to a labctl configure-forwarding.json. Its bridge and "
        "configured_ports are merged into --bridge/--interfaces (as a "
        "fallback for --bridge, merged for interfaces) so collection "
        "focuses on the ports this run actually configured. Recorded "
        "under report['forwarding_result'] for evidence linking; a "
        "missing/invalid file is a warning, not a fatal error.",
    )
    parser.add_argument(
        "--flow",
        metavar="FLOW",
        help="OpenFlow match for `ovs-appctl ofproto/trace` (requires OVS).",
    )
    parser.add_argument(
        "--bridge",
        metavar="BRIDGE",
        help="Bridge name for `ofproto/trace` and `ovs-ofctl dump-flows`.",
    )
    parser.add_argument(
        "--datapath",
        metavar="DP",
        help="Optional datapath argument for `dpctl/dump-flows` and `dpctl/show`.",
    )
    parser.add_argument(
        "--no-more",
        action="store_true",
        help="Do not pass -m to dpctl/dump-flows (less verbose; may hide markers).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="If > 0, take a second tc/ethtool/ip-link/CPU snapshot after "
        "sleeping this many seconds and report before/after deltas. "
        "Default 0 (single snapshot, no sleep).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "both"),
        default="both",
        help="Legacy single-file output format. Default: both (text on "
        "stdout, JSON if -o given or also stdout). See --output-dir for "
        "the offload-validation.json / .md pair.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Write JSON report to PATH (legacy single-file option).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --output-dir, write offload-validation.json there.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="With --output-dir, write offload-validation-report.md there.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        metavar="DIR",
        help="Directory to write offload-validation.json and/or "
        "offload-validation-report.md into (created if missing). If "
        "neither --json nor --markdown is given, both files are written.",
    )
    parser.add_argument(
        "--plugin-dir",
        action="append",
        dest="plugin_dirs",
        default=[],
        metavar="DIR",
        help="Additional directory of vendor plugins. Repeatable.",
    )
    parser.add_argument(
        "--no-bundled-plugins",
        action="store_true",
        help="Do not load plugins from the bundled vendor_plugins directory.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-command timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--location",
        choices=("host", "dpu"),
        default=None,
        help="Where OVS runs. dpu: do not treat host ovs-vsctl as offload proof.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="VM2VM lab YAML (used with --location dpu for RemoteCommandRunner planning).",
    )
    parser.add_argument(
        "--samples-json",
        metavar="PATH",
        help="Inject collected DPU/host command stdout (tests / offline). Skips live probes.",
    )
    return parser.parse_args(argv)


def load_forwarding_result(path: str) -> Dict[str, Any]:
    """Load a labctl configure-forwarding.json and extract the fields
    needed to focus offload-checker collection on the ports/bridge that
    run actually configured (Prompt J intent-to-evidence linking).

    Never raises: a missing file, unreadable JSON, or unexpected shape
    is reported as `error` and every other field stays at its default,
    so a bad --forwarding-result degrades to "no forwarding result"
    rather than aborting the whole checker run.
    """
    info: Dict[str, Any] = {
        "path": path,
        "error": None,
        "name": None,
        "bridge": None,
        "configured_ports": [],
        "configured_flows": [],
        "offload_requested": None,
        "offload_configured": None,
        "vf_pci_addresses": [],
        "representors": [],
        "control_entity": None,
        "forwarding_entity": None,
    }
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        info["error"] = f"could not read/parse --forwarding-result '{path}': {exc}"
        return info

    if not isinstance(data, dict):
        info["error"] = f"--forwarding-result '{path}' did not contain a JSON object"
        return info

    intent = data.get("intent") or {}
    result = data.get("result") or {}
    if not isinstance(intent, dict):
        intent = {}
    if not isinstance(result, dict):
        result = {}

    info["name"] = intent.get("name")
    info["bridge"] = intent.get("bridge") or (result.get("details") or {}).get("bridge")
    info["configured_ports"] = [p for p in (result.get("configured_ports") or []) if p]
    info["configured_flows"] = list(result.get("configured_flows") or [])
    info["offload_requested"] = result.get("offload_requested")
    info["offload_configured"] = result.get("offload_configured")
    info["control_entity"] = result.get("control_entity")
    info["forwarding_entity"] = result.get("forwarding_entity")
    for endpoint in intent.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        if endpoint.get("vf_pci"):
            info["vf_pci_addresses"].append(endpoint["vf_pci"])
        if endpoint.get("representor"):
            info["representors"].append(endpoint["representor"])
    return info


def resolve_interfaces(
    args: argparse.Namespace, forwarding_result: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Merge -i/--interface (repeatable), --interfaces (comma list),
    --pf, and (if given and valid) --forwarding-result's configured_ports
    into one de-duplicated, order-preserving list.
    """
    ordered: List[str] = []
    seen = set()

    def _add(name: Optional[str]) -> None:
        if not name:
            return
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)

    for name in args.interface_list or []:
        _add(name)
    if args.interfaces_csv:
        for name in args.interfaces_csv.split(","):
            _add(name)
    _add(args.pf)
    if forwarding_result and not forwarding_result.get("error"):
        for name in forwarding_result.get("configured_ports") or []:
            _add(name)
    return ordered


def _load_plugins(args: argparse.Namespace) -> List[Any]:
    # Package is named vendor_plugins, not "plugins": a bare "plugins"
    # package here collides with the repo-root plugins/ package under
    # pytest's default collection (see
    # tools/topology-discovery/README.md "Known limitations").
    try:
        from vendor_plugins import load_plugins, plugin_dir_default
    except ImportError:
        # Allow running as `python3 /path/to/ovs_offload_checker.py` from any cwd
        # by putting this file's directory on sys.path.
        here = Path(__file__).resolve().parent
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        from vendor_plugins import load_plugins, plugin_dir_default

    dirs: List[Path] = []
    if not args.no_bundled_plugins:
        dirs.append(plugin_dir_default())
    for extra in args.plugin_dirs:
        dirs.append(Path(extra))
    return load_plugins(dirs)


def write_output_dir_reports(
    output_dir: str, report: Dict[str, Any], write_json: bool, write_markdown: bool, pretty: bool
) -> List[str]:
    """Write the canonical offload-validation.json / .md pair. Returns the
    list of paths actually written.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    if write_json:
        json_path = out_path / "offload-validation.json"
        json_path.write_text(
            json.dumps(report, indent=2 if pretty else None, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        written.append(str(json_path))
    if write_markdown:
        md_path = out_path / "offload-validation-report.md"
        md_path.write_text(render_markdown(report), encoding="utf-8")
        written.append(str(md_path))
    return written


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    forwarding_result: Optional[Dict[str, Any]] = None
    if args.forwarding_result:
        forwarding_result = load_forwarding_result(args.forwarding_result)
        if forwarding_result.get("error"):
            print(f"warning: {forwarding_result['error']}", file=sys.stderr)

    # Unlike the original tool, --bridge alone (no --flow) is a valid,
    # common invocation now: it drives `ovs-ofctl dump-flows <bridge>`.
    interfaces = resolve_interfaces(args, forwarding_result)
    bridge = args.bridge
    if not bridge and forwarding_result and not forwarding_result.get("error"):
        bridge = forwarding_result.get("bridge")

    try:
        plugins = _load_plugins(args)
    except Exception as exc:
        print(f"warning: plugin load failed ({exc}); continuing without plugins", file=sys.stderr)
        plugins = []

    config: Optional[Dict[str, Any]] = None
    if args.config:
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from plugins.base.vm2vm_config import load_vm2vm_config

        try:
            config = load_vm2vm_config(args.config)
        except (OSError, ValueError) as exc:
            print(f"warning: could not load --config {args.config}: {exc}", file=sys.stderr)

    samples: Optional[Dict[str, Any]] = None
    if args.samples_json:
        try:
            with open(args.samples_json, encoding="utf-8") as handle:
                loaded = json.load(handle)
            samples = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError) as exc:
            print(f"warning: could not load --samples-json {args.samples_json}: {exc}", file=sys.stderr)

    location = args.location
    if not location and config:
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            from plugins.base.remote import is_dpu_mode

            if is_dpu_mode(config):
                location = "dpu"
        except Exception:
            pass

    try:
        report = build_report(
            interfaces=interfaces,
            pf=args.pf,
            vendor=args.vendor,
            flow=args.flow,
            bridge=bridge,
            more=not args.no_more,
            datapath=args.datapath,
            duration=args.duration,
            timeout=args.timeout,
            plugins=plugins,
            forwarding_result=forwarding_result,
            forwarding_result_path=args.forwarding_result,
            location=location,
            samples=samples,
            config=config,
        )
    except Exception as exc:
        print(f"error: offload check failed unexpectedly: {exc}", file=sys.stderr)
        return 1

    indent = 2 if args.pretty else None
    json_text = json.dumps(report, indent=indent, sort_keys=False)
    text_report = render_text(report)

    if args.format in ("text", "both"):
        sys.stdout.write(text_report)
        if not text_report.endswith("\n"):
            sys.stdout.write("\n")

    if args.format == "json" and not args.output:
        sys.stdout.write(json_text)
        sys.stdout.write("\n")
    elif args.format == "both" and not args.output:
        sys.stdout.write("\n----- JSON -----\n")
        sys.stdout.write(json_text)
        sys.stdout.write("\n")

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(json_text)
                handle.write("\n")
        except OSError as exc:
            print(f"error: could not write output file '{args.output}': {exc}", file=sys.stderr)
            return 1

    if args.output_dir:
        write_json = args.json or not args.markdown
        write_markdown = args.markdown or not args.json
        try:
            written = write_output_dir_reports(
                args.output_dir, report, write_json, write_markdown, args.pretty
            )
        except OSError as exc:
            print(f"error: could not write to --output-dir '{args.output_dir}': {exc}", file=sys.stderr)
            return 1
        for path in written:
            print(f"wrote {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
