# offload-checker

CLI tooling to inspect whether Open vSwitch and the Linux datapath look
like they are hardware-offloading flows. The concrete tool is
`ovs_offload_checker.py`.

This is an **observation** helper for `tests/offload-validation`,
`docs/test-plan/offload-validation.md`, and
`tools/telemetry-collector`. It does not prove offload by itself: it
collects datapath/driver dumps from several independent sources and
combines them into a heuristic status + confidence score. Read
[`docs/test-plan/offload-validation.md`](../../docs/test-plan/offload-validation.md)
before trusting a single run's verdict — it explains why functional
connectivity is not offload evidence and how counters differ between
software and offloaded paths.

Machine-readable JSON (`offload-validation.json`) is intended for
`tools/report_generator.py` and `labctl.py`'s `validate-offload` stage;
the text/markdown report is for operators running experiments on a DPU
or smart NIC.

## `ovs_offload_checker.py`

Vendor-neutral core. It shells out to:

| Step | Command | When |
|---|---|---|
| OVS bridge/port topology | `ovs-vsctl show` | Always |
| OVS datapath state | `ovs-appctl dpctl/show [DP]` | Always |
| OVS datapath flows | `ovs-appctl dpctl/dump-flows -m [DP]` | Always |
| OpenFlow table flows | `ovs-ofctl dump-flows <bridge>` | Only if `--bridge` is given |
| OpenFlow trace | `ovs-appctl ofproto/trace [bridge] <flow>` | Only if `--flow` is given |
| TC filters, ingress + egress | `tc -s filter show dev <iface> ingress` / `... egress` | For every interface in `-i`/`--interfaces`/`--pf` |
| NIC counters | `ethtool -S <iface>` | Same interface list |
| Netdev RX/TX counters | `ip -s link show <iface>` | Same interface list |
| CPU snapshot | `mpstat -P ALL 1 1`, falling back to `/proc/stat` | Always |

Missing binaries, timeouts, and permission errors are recorded per
command. They never abort the rest of the run — a report with every
tool missing still completes and reports `assessment.status: unknown`.

### Probable-offload markers (core)

The core logic only looks for markers that appear in **upstream** OVS
and kernel TC output. It does not special-case NVIDIA, Intel, AMD, or
Marvell counter names — that is left to `--plugin-dir`.

**OVS datapath flow lines** (`dpctl/dump-flows -m`):

| Marker | Classification |
|---|---|
| `offloaded:yes` | `probable_offloaded` |
| `offloaded:partial` | `probable_partial` |
| `offloaded:no` | `software` |
| `dp:tc` (without `offloaded:no`) | `probable_offloaded` (TC is the usual kernel offload vehicle; not proof the filter is in HW) |
| `dp:ovs` (without `offloaded:yes`) | `software` |
| none of the above | `unknown` |

**TC filter blocks** (checked separately for `ingress` and `egress`):

| Marker | Classification |
|---|---|
| `in_hw` / `in_hw_count N` | `probable_offloaded` |
| `not_in_hw` (and no `in_hw`) | `software` |
| neither | `unknown` |

**ethtool counters**: every counter is captured in JSON. The report
*highlights* names that match a generic regex (`offload`, `in_hw`,
`hw_`, `xdp`, `tc_`, `eswitch`, `representor`, `dropped`, `discard`).
That highlight is a convenience, not a vendor map — use a plugin for
vendor-specific counter names.

`-m` is passed to `dpctl/dump-flows` by default because several OVS
builds only print `offloaded:` / `dp:` in the verbose dump. Use
`--no-more` to omit it.

### Combined assessment (`assessment` section)

`assess_offload()` combines the three classifiable sources above
(`dpctl_dump_flows`, `tc_filters`, `ethtool_stats`) into one status and
a 0.0–1.0 confidence score. Each source contributes a weighted "lean"
only when it actually returned data — a missing tool or empty dump is
recorded under `assessment.warnings` and excluded from the score
denominator, so `unknown` (no data at all) is never conflated with
`software_only` (data says software).

| `assessment.status` | Meaning |
|---|---|
| `probable_offloaded` | Net evidence leans offloaded with confidence ≥ 0.5 |
| `probable_partial` | Evidence is mixed/balanced, or confidence < 0.5 |
| `software_only` | Net evidence leans software with confidence ≥ 0.5 |
| `unknown` | No classifiable source returned data |

`assessment.evidence` lists the per-source contributions (e.g.
`[dpctl_dump_flows] 3/3 datapath flow(s) marked offloaded, none marked
software`); `assessment.warnings` lists sources that could not
contribute (missing tool, no filters, no flows); `assessment.
limitations` is a fixed, always-present list of caveats (heuristic
only, connectivity != offload, driver self-reporting, vendor counter
name variance, etc.) so downstream consumers never have to re-derive
them. See `docs/test-plan/offload-validation.md` for worked examples.

### `--duration`: before/after deltas

Pass `--duration SECONDS` to take a second tc/ethtool/`ip -s link`/CPU
snapshot after sleeping, and report deltas under the top-level
`duration` key (`duration.before`/`duration.after` raw snapshots, plus
`duration.ethtool_deltas`, `duration.ip_link_deltas`,
`duration.tc_deltas`, `duration.cpu_delta`). Run traffic concurrently
with the sleep window. This
does not feed into `assessment.confidence` — it is reported as
contextual evidence for the operator to interpret (see
`docs/test-plan/offload-validation.md` §3–4 for how to read the
deltas, e.g. CPU/`ip -s link` deltas are expected to shrink once a flow
offloads).

### Requirements

- Python 3.7+ (standard library only).
- Meaningful on Linux with some of: `ovs-vsctl`, `ovs-appctl`,
  `ovs-ofctl`, `tc` (`iproute2`), `ethtool`, `ip`, `mpstat`
  (`sysstat`). Run as root (or with `CAP_NET_ADMIN`) for a complete
  dump.
- OVS must be running for the `ovs-vsctl`/`ovs-appctl`/`ovs-ofctl`
  steps.

### Usage

From this directory, or via an absolute path:

```bash
# Text + JSON on stdout (default --format both), no interfaces
sudo python3 ovs_offload_checker.py --bridge br-int

# Datapath dump plus TC/ethtool/ip-link for representor and PF
sudo python3 ovs_offload_checker.py \
  --bridge br-int \
  --interfaces pf0vf0,pf0vf1 \
  --pf pf0hpf \
  --vendor nvidia_mellanox_bluefield

# Before/after deltas across a 15s traffic window
sudo python3 ovs_offload_checker.py \
  --bridge br-int \
  --interfaces pf0vf0,pf0vf1 \
  --duration 15

# Canonical evidence pair for a lab run (JSON + markdown together)
sudo python3 ovs_offload_checker.py \
  --bridge br-int \
  --interfaces pf0vf0,pf0vf1 \
  --duration 15 \
  --output-dir tests/results/run-001/offload

# Focus collection on what configure-forwarding actually configured
# (bridge + interfaces are taken from the file; see
# docs/test-plan/forwarding-validation.md for the intent-to-evidence chain)
sudo python3 ovs_offload_checker.py \
  --forwarding-result tests/results/nvidia-bluefield/run-001/configure_forwarding.json \
  --duration 15 \
  --output-dir tests/results/run-001/offload

# Trace a candidate OpenFlow match
sudo python3 ovs_offload_checker.py \
  --bridge br-int \
  --flow 'in_port=1,tcp,nw_dst=10.0.0.20,tp_dst=80'
```

### Options

| Option | Description |
|---|---|
| `-i IFACE`, `--interface IFACE` | Repeatable. Merged with `--interfaces` and `--pf`. Enables `tc` + `ethtool -S` + `ip -s link` for that netdev. |
| `--interfaces IFACE1,IFACE2` | Comma-separated netdevs, merged with `-i`/`--interface` and `--pf`. |
| `--pf PF_NAME` | PF netdev name; added to the interface list (deduplicated) and recorded under `inputs.pf`. |
| `--vendor VENDOR` | Vendor hint, recorded under `inputs.vendor` and passed to plugin context. Informational only in core. |
| `--forwarding-result PATH` | Path to a `labctl.py vm2vm configure-forwarding` result JSON. Its bridge is used as a fallback for `--bridge`, and its `configured_ports` are merged into the interface list, so collection focuses on what that stage actually configured. Recorded under `report['forwarding_result']`. A missing/invalid file is a warning, not fatal. See `docs/test-plan/forwarding-validation.md`. |
| `--flow FLOW` | OpenFlow match string for `ofproto/trace`. |
| `--bridge BRIDGE` | Bridge argument for `ofproto/trace` (with `--flow`) and for `ovs-ofctl dump-flows`. |
| `--datapath DP` | Optional datapath id/name for `dpctl/show` and `dpctl/dump-flows`. |
| `--no-more` | Do not pass `-m` to `dpctl/dump-flows`. |
| `--duration SECONDS` | If > 0, take a second tc/ethtool/ip-link/CPU snapshot after sleeping this many seconds and report before/after deltas. Default 0 (single snapshot). |
| `--format {text,json,both}` | Legacy single-file format for stdout/`-o`. Default `both`. |
| `-o PATH`, `--output PATH` | Write JSON to `PATH` (legacy single-file option). |
| `--pretty` | Indent JSON (applies to `-o` and `--output-dir`). |
| `--json` | With `--output-dir`, write `offload-validation.json` there. |
| `--markdown` | With `--output-dir`, write `offload-validation-report.md` there. |
| `--output-dir DIR` | Directory for the canonical `offload-validation.json`/`.md` pair (created if missing). If neither `--json` nor `--markdown` is given, both files are written. |
| `--plugin-dir DIR` | Extra plugin directory (repeatable). |
| `--no-bundled-plugins` | Skip the bundled `vendor_plugins/` directory. |
| `--timeout N` | Per-command timeout in seconds (default 15). |

Exit codes: `0` on a completed report (including per-command errors
and a low-confidence/`unknown` assessment — this tool never fails a
build on offload status by itself), `1` for unexpected failures
(report construction raised, or `-o`/`--output-dir` could not be
written), `2` for CLI usage errors (argparse).

### Examples

#### Kernel datapath / TC offload experiment

After `ansible/roles/ovs_setup` with `ovs_enable_hw_offload: true` and
representors on `br-int`:

```bash
sudo python3 ovs_offload_checker.py \
  --bridge br-int \
  --interfaces eth1v0 \
  --format text
```

Look for datapath lines tagged `[OFFLOADED?]` with `offloaded:yes` or
`dp:tc`, and TC filters tagged with `in_hw`. Then generate traffic and
re-run with `--duration`: `ip -s link` and CPU deltas should stay flat
on the offloaded path while `ethtool -S` hardware counters move (see
`docs/test-plan/offload-validation.md` §3).

#### Userspace / DPDK datapath

`tc filter show` is often empty or irrelevant on a `netdev` datapath.
Still dump OVS flows (and optional trace):

```bash
sudo python3 ovs_offload_checker.py \
  --bridge br-phy \
  --flow 'in_port=1,eth_type=0x0800' \
  --format both \
  --pretty \
  -o /tmp/ovs-dpdk-offload.json
```

If the OVS build prints `offloaded:yes` on `netdev`/`dpif-netdev`
flows, those lines are classified the same way as kernel offload. If
it does not, classification stays `unknown` — expected, not a bug.

#### Vendor plugin (placeholder)

Core code stays vendor-neutral. Drop a plugin beside the bundled
`vendor_plugins/` package (or pass `--plugin-dir`):

```python
# vendor_plugins/example_vendor.py  -- placeholder, not shipped as a real backend
from vendor_plugins import VendorPlugin

class ExampleVendorPlugin(VendorPlugin):
    name = "example_vendor"

    def extra_sections(self, context):
        # context = {"interfaces": [...], "report": {...}, "vendor": "..."}
        # Run vendor CLI here; return JSON-serialisable data.
        return {"note": "replace with vendor telemetry; core checker stays generic"}

    def annotate_flow(self, flow):
        return flow
```

```bash
sudo python3 ovs_offload_checker.py --plugin-dir ./vendor_plugins --bridge br-int -i p0
```

See `vendor_plugins/__init__.py` for the `VendorPlugin` contract.
Plugins must not raise; treat missing vendor tools as data.

### JSON shape (abridged)

```json
{
  "schema_version": "odse-ovs-offload-checker/v1alpha1",
  "timestamp": "2026-08-15T12:00:00+00:00",
  "host": "dpu-host-01",
  "inputs": {
    "interfaces": ["pf0vf0"], "pf": "pf0hpf", "vendor": "nvidia_mellanox_bluefield",
    "flow": null, "bridge": "br-int", "dump_flows_more": true,
    "datapath": null, "duration_seconds": 15.0,
    "forwarding_result_path": "tests/results/nvidia-bluefield/run-001/configure_forwarding.json"
  },
  "forwarding_result": {
    "path": "tests/results/nvidia-bluefield/run-001/configure_forwarding.json",
    "error": null, "name": "odse-vm2vm-nvidia", "bridge": "br-int",
    "configured_ports": ["pf0vf0", "pf0vf1"], "configured_flows": [],
    "vf_pci_addresses": ["0000:03:00.2", "0000:03:00.3"],
    "representors": ["pf0vf0", "pf0vf1"],
    "offload_requested": true, "offload_configured": true
  },
  "ovs_vsctl_show": {"command": {"available": true}, "stdout": "..."},
  "dpctl_show": {"command": {"available": true}, "stdout": "..."},
  "dpctl_dump_flows": {
    "command": {"command": "ovs-appctl dpctl/dump-flows -m", "available": true},
    "flows": [{"raw": "..., offloaded:yes, dp:tc, actions:2", "classification": "probable_offloaded", "markers": ["offloaded:yes", "dp:tc"]}],
    "counts": {"probable_offloaded": 1, "probable_partial": 0, "software": 0, "unknown": 0, "total": 1}
  },
  "ofctl_dump_flows": {"command": {"available": true}, "stdout": "..."},
  "ofproto_trace": null,
  "tc_filters": {"interfaces": [{"interface": "pf0vf0", "ingress": {"filters": [], "counts": {}}, "egress": {"filters": [], "counts": {}}}]},
  "ethtool_stats": {"interfaces": [{"interface": "pf0vf0", "counters": {}, "highlighted_counters": {}}]},
  "ip_link_stats": {"interfaces": [{"interface": "pf0vf0", "rx": {}, "tx": {}}]},
  "cpu_snapshot": {"source": "proc_stat", "mpstat": {"available": false}, "proc_stat": {}},
  "duration": {
    "duration_seconds": 15.0,
    "before": {"tc_filters": {}, "ethtool_stats": {}, "ip_link_stats": {}, "cpu_snapshot": {}},
    "after": {"tc_filters": {}, "ethtool_stats": {}, "ip_link_stats": {}, "cpu_snapshot": {}},
    "ethtool_deltas": {}, "ip_link_deltas": {}, "tc_deltas": {}, "cpu_delta": {}
  },
  "assessment": {
    "status": "probable_offloaded",
    "confidence": 0.8,
    "evidence": ["[dpctl_dump_flows] 1/1 datapath flow(s) marked offloaded, none marked software"],
    "warnings": [],
    "limitations": ["This is a heuristic based on documented upstream OVS/TC markers...", "..."]
  },
  "summary": {"datapath_flows": {}, "note": "..."},
  "plugins": {"loaded": [], "results": {}}
}
```

### Known limitations

See `assessment.limitations` in every report (also listed in
`docs/test-plan/offload-validation.md`):

- **Not a pass/fail offload test.** Absence of markers is common on
  OVS-DPDK, some vendor OVS builds, and unprivileged runs — it is
  reported as `unknown`, not `software_only`.
- **Functional connectivity is not offload evidence.** Ping/iperf
  succeeding says nothing about which datapath handled the packets.
- **`ofproto/trace` is a simulator**, not a statement that the live
  datapath offloaded the packet.
- **TC `in_hw` is driver-reported.** Drivers differ in when they set
  it, and tc byte/packet counters can lag hardware even when
  `in_hw: yes`.
- **ethtool highlight regex is generic** and will both miss vendor
  counters with unusual names and highlight unrelated `dropped`
  stats. Use JSON + a vendor plugin for those.
- **`--duration` deltas measure activity during a time window**, not
  necessarily the test traffic specifically — avoid background
  traffic for a clean signal, and remember representor/PF `ip -s
  link` counters are expected to stay flat once a flow is truly
  offloaded (see `docs/test-plan/offload-validation.md` §3).
