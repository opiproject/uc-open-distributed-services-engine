# Offload validation methodology

Status: Draft — how to **interpret** `tools/offload-checker/ovs_offload_checker.py`
output, not a results database. Pairs with
[`performance-methodology.md`](performance-methodology.md) (attaching
offload evidence to a benchmark record) and
[`vf-to-representor-mapping.md`](../operations/vf-to-representor-mapping.md)
(the topology prerequisite for hardware offload).

---

## 1. Functional connectivity is not proof of offload

A successful `ping` or `iperf3` run between two VMs proves the
**forwarding path works**. It proves nothing about **where** the
packets were processed:

- Kernel OVS datapath (`dp:ovs-system`, `datapath_type=system`)
- Kernel TC flower software fallback
- Hardware-offloaded TC flower / ASIC flow tables
- DPDK userspace datapath (`dp:netdev`)

All four can pass the same connectivity test. The only way to tell
them apart is to inspect datapath-level and driver-level evidence
*while* traffic is flowing, which is what
`ovs_offload_checker.py` automates. Treat `offload_claimed: true` in a
config or a lab run log as an **intent**, never as **evidence**;
evidence is only ever `offload_checker_report.assessment`.

Do not accept "the demo worked" as a substitute for running the
checker. Two common false positives:

- **VF passthrough without OVS in the loop.** If VMs use direct VF
  passthrough, connectivity works via the SR-IOV switch entirely
  outside OVS. There may be no representor ports, no OVS bridge, and
  `ovs_offload_checker.py` correctly reports `unknown` — this is
  expected, not a bug, and is a *different* scenario from OVS
  hardware offload (see `performance-methodology.md` §3 vs §4).
- **Representors exist but flows are not yet offloaded.** The kernel
  installs new flows in software first and only offloads them after a
  few packets establish the flow is stable/hot. A checker run taken
  immediately after the first ping can show `dp:ovs-system` /
  `not_in_hw` even on hardware that will offload the flow a moment
  later. Always warm up traffic (steady flow for several seconds)
  before capturing evidence, and prefer `--duration` (§4 below) over a
  single instantaneous snapshot.

---

## 2. Why OVS flows, TC filters, and hardware counters must all be checked

No single source is authoritative on every vendor and kernel
combination, so the checker deliberately collects three independent
signal families and combines them (see
[`tools/offload-checker/README.md`](../../tools/offload-checker/README.md)
for the exact CLI/output shape):

| Source | Command | What it tells you | Where it can mislead |
|---|---|---|---|
| OVS datapath flows | `ovs-appctl dpctl/dump-flows` | Per-flow `offloaded:yes/no` and `dp:tc`/`dp:ovs-system` markers from the OVS kernel module itself | Only present once a flow exists; absent entirely on DPDK userspace datapaths or non-OVS forwarding (VF passthrough) |
| TC filters | `tc -s filter show dev <if> ingress/egress` | Kernel-level `in_hw` / `not_in_hw` per flower filter, plus per-filter packet/byte counters | TC filters can exist without OVS (manually installed); egress offload support varies by driver/firmware version |
| NIC driver counters | `ethtool -S <if>` | Vendor-specific counters that often distinguish hardware-switched packets from ones that reached the kernel/CPU (e.g. counters with `_rep_`, `sw_`, `hw_`, `switchdev_` style names) | Counter names are **not standardized across vendors**; the checker highlights offload-suggestive names heuristically and never invents a status from ethtool alone |

Because each source can be silent (tool not installed), empty (no
flows yet), or ambiguous (vendor-specific naming), the assessment
heuristic in `assess_offload()` treats **absence of a source as
"no data," never as "software-only."** Only sources that actually
returned data are weighted into the confidence score, and every
report lists exactly which sources contributed
(`assessment.evidence`) and which were unavailable
(`assessment.warnings`). A `probable_offloaded` verdict from a single
source (e.g. only `ethtool` present) is intentionally reported at
lower confidence than the same verdict corroborated by both `dpctl`
and `tc`.

Corroboration matters because dpctl and tc answer different
questions: dpctl says whether *this specific 5-tuple flow* is
offloaded right now, while tc says whether the *installed filter
program* is resident in hardware. A flow can show `dp:ovs-system` in
dpctl while its matching tc filter is `in_hw` if the flow just aged
out and hasn't been reinstalled — this is why the checker never
short-circuits on the first source it finds.

---

## 3. Why some counters may differ between software and offloaded paths

When a flow moves from the kernel/CPU datapath to a hardware-offloaded
path, several observable side effects are expected and are **not**
signs of a broken configuration:

- **`ip -s link show` RX/TX counters may stop incrementing on the
  representor or PF netdev** for offloaded traffic, because the
  packets never cross that netdev's kernel receive/transmit path —
  they are switched directly by the ASIC. Comparing representor
  counters before/after a `--duration` window is a *negative*
  indicator (little to no delta despite known active traffic) as much
  as a positive one.
- **CPU utilization (`mpstat`/`/proc/stat`) should drop or stay flat**
  under load once a flow is offloaded, since the host/DPU CPU is no
  longer doing per-packet forwarding work for that flow. A CPU delta
  that scales linearly with traffic volume during the `--duration`
  window is evidence *against* offload, independent of what dpctl or
  tc report.
- **`ethtool -S` hardware/vendor counters increment instead** of the
  generic netdev counters — this is the expected signature of
  offload and is why the checker highlights offload-suggestive
  ethtool counter names as positive evidence rather than relying on
  `ip -s link` alone.
- **TC filter byte/packet counters can lag or read zero even when
  `in_hw: yes`**, because some drivers do not sync hardware counters
  back to the kernel's tc statistics in real time (sync interval is
  vendor/driver-specific). Do not treat a zero or stale tc counter as
  proof a filter isn't offloaded if `in_hw: yes` is present — treat
  it as inconclusive and rely on the ethtool/CPU evidence instead.

These are exactly the counter families `assess_offload()` inspects,
and exactly why the tool reports a **confidence score** instead of a
boolean: any one counter behaving unexpectedly (stale tc byte count,
missing vendor ethtool counters) is common and expected, and should
lower confidence rather than flip the verdict.

---

## 4. How to compare software-only vs offload-enabled runs

Use the `--duration <seconds>` flag to capture a **before/after**
snapshot pair (tc filters, ethtool counters, `ip -s link`, and CPU)
bracketing a window of active traffic, rather than relying on a single
instantaneous snapshot:

```bash
python3 tools/offload-checker/ovs_offload_checker.py \
  --bridge br-int \
  --pf pf0hpf \
  --interfaces pf0vf0,pf0vf1 \
  --duration 15 \
  --output-dir tests/results/run-001/offload
```

Run traffic (e.g. `iperf3` via `labctl.py run-traffic`) concurrently
with the checker's sleep window so the deltas reflect real activity.

Recommended comparison procedure for a single physical pair:

1. **Software-only baseline run.** Disable hardware offload
   (`ovs-vsctl set Open_vSwitch . other_config:hw-offload=false`, or
   use `scenario_id: baseline_no_offload` from
   `performance-methodology.md` §2). Run the checker with
   `--duration` during steady traffic. Save the report as
   `tests/results/<run>/offload/offload-validation.json` with a
   `scenario_id` of `baseline_no_offload`.
2. **Offload-enabled run.** Re-enable hardware offload
   (`other_config:hw-offload=true`), confirm representors are mapped
   (`vf_to_representor_map.py --strict`), warm up the flow, then run
   the checker with the same `--duration`, same interfaces, and same
   traffic profile. Save as a separate report with `scenario_id:
   ovs_hw_offload_kernel`.
3. **Diff the two reports**, focusing on:
   - `assessment.status` and `assessment.confidence` (expect
     `probable_offloaded` with confidence ≥ 0.5 only in run 2).
   - The `duration.cpu` delta (expect run 2's CPU delta to be lower
     for the same traffic volume, per §3).
   - The `duration.ip_link` delta on the PF/representor (expect run
     2's counters to grow much more slowly than run 1's for the same
     traffic, per §3).
   - `dpctl` flow classifications (expect `dp:tc`/`offloaded:yes` to
     appear only in run 2).
4. **Never compare runs with different topologies, traffic profiles,
   or durations.** A confidence or CPU-delta difference is only
   attributable to offload if everything else (physical pair, SKU,
   firmware, frame size, flow count, `--duration`) is held constant,
   per `performance-methodology.md` §1.
5. Record both reports' paths in the run's result report (see
   [`tests/results/README.md`](../../tests/results/README.md)) so the
   comparison is reproducible and auditable later.

A run that only has the offload-enabled report, with no
`baseline_no_offload` counterpart, can still show a high-confidence
`probable_offloaded` verdict — that verdict stands on its own
evidence — but it **cannot** be used to make a performance/efficiency
claim relative to software forwarding without the baseline for
comparison.

---

## 5. Host vs DPU vs VM vs traffic vs confidence

When `forwarding_result.control_entity` (or `--location dpu`) says OVS
runs on the DPU, the checker **must not** treat host `ovs-vsctl` as
offload proof. NVIDIA BlueField DPU mode (`nvidia-bluefield-dpu-mode`)
collects:

| Report key | What it is | What it is not |
|---|---|---|
| `host_evidence` | Host CPU / notes that host OVS is not the forwarding owner | DPU datapath offload |
| `dpu_evidence` | DPU `ovs-vsctl show`, `ovs-ofctl dump-flows`, `dpctl/show`, `dpctl/dump-flows`, optional DPU `tc` / `ethtool` | Host OVS, guest counters |
| `vm_evidence` | VF PCI addresses and representor names from forwarding intent | Invented guest/VF counters |
| `traffic_evidence` | `--duration` before/after window when requested | A ping success |
| `offload_confidence` | Same object as `assessment` (status + 0.0–1.0 + warnings) | A boolean `offloaded:yes` |

Without injected `--samples-json` (or a live DPU SSH `--apply` path),
DPU probes stay **planned**. Planned stdout is empty; `assessment.status`
stays `unknown`. That is valid. Do not backfill `offloaded:yes`.

`labctl.py vm2vm validate-offload` in NVIDIA DPU mode adds
`--location dpu --config <file>` so the combined
`offload-validation.json` keeps these sections in one file.

Host-switchdev (advanced profile) keeps OVS evidence under
`host_evidence` and sets `dpu_evidence` to `null`.
