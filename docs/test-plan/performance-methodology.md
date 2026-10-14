# Performance benchmarking methodology (ODSE)

Status: Draft — how to **measure**, not a results database.

This methodology compares datapaths used in Open Distributed Services
Engine (host software OVS, kernel TC offload, VFIO, vDPA, DPU-resident
OVN, vendor OVS). It does **not** certify vendor SKUs and does **not**
treat Kind/`netdevsim` numbers as hardware performance.

Scripts (when added) live under `tests/performance/`. Hardware reruns:
`ci/hardware-regression`. Offload *correctness* (markers, ACLs) is
`tests/offload-validation` and
`tools/offload-checker`; this document only defines how to **attach**
those observations to a benchmark record.

JSON documents must use `schema_version` below. Missing measurements
are JSON `null`, never invented zeros.

---

## 1. Test goals

1. **Compare** the scenarios in §2–7 on the **same** physical pair
   (same servers, same `<SKU>`, same firmware, same frame size, same
   flow count) so differences are attributable to the datapath, not
   the lab.
2. Report **host CPU**, **DPU/ACC CPU** (if a DPU OS exists), **guest
   CPU**, throughput, PPS, latency/jitter, and an **offload hit
   ratio** that is honest about uncertainty.
3. Support **regression**: a later run with the same `topology_id` and
   `scenario_id` can be diffed.
4. **Non-goals:** claiming NSX/vDSE parity; ranking vendors from a
   single iperf; using simulation as a performance gate.

Each run records `scenario_id`, `offload_claimed` (boolean **intent**),
and `offload_evidence` (checker/vendor counters). `offload_claimed:
true` with `offload_evidence.classification` in `unknown` / `null` is
allowed but **must not** be summarized as “hardware offload won.”

---

## 2. Baseline no-offload scenario

`scenario_id`: `baseline_no_offload`

**Intent:** software forwarding the rest of the lab can beat.

Typical bind: virtio-net + `vhost-net` or veth; OVS `datapath_type=
system` (or kernel bridge); `other_config:hw-offload=false`. No VF,
no DPDK PMD on the measurement path.

**Must:** same L2/L3 policy shape as later scenarios (same allow
rules), even if enforcement is iptables/OVS software. Otherwise ACL
cost is confounded with offload.

Kind clusters may use this scenario only to **debug the harness**,
not to publish vendor comparisons.

---

## 3. OVS hardware offload scenario

`scenario_id`: `ovs_hw_offload_kernel`

**Intent:** kernel OVS + TC flower / `hw-offload=true` + representors
(`ansible/roles/ovs_setup`, `switchdev_setup`).

**Gate:** representors exist (`vf_to_representor_map.py` or
`devlink flavour pcivf`). If not, **skip** and set
`status: skipped_not_applicable` — do not fall back to §2 and call it
this scenario.

Workload: VMs/pods with VFs; representors on `br-int`. Evidence:
`ovs_offload_checker.py` after warmup. NVIDIA kernel path is the
usual first hardware lab; AMD must not run this unless §7 of
`amd-pensando.md` passed.

---

## 4. VF passthrough scenario

`scenario_id`: `vfio_passthrough`

**Intent:** guest sees vendor NIC (`qemu/vfio/vf-passthrough.md`).
Host CPU should exclude a software vSwitch on the guest’s packets
**on the host**; DPU/eswitch may still switch.

This is **not** “no switch.” Report DPU CPU and offload evidence on
the **representor owner**. Do not equate VFIO with “wire speed” without
§8–13.

---

## 5. vDPA scenario

`scenario_id`: `vhost_vdpa`

**Intent:** guest virtio-net, `vhost-vdpa`
(`qemu/vdpa/vhost-vdpa.md`). Compare to §2 and §4 on the same card.

**If `vdpa mgmtdev show` is empty:** skip (`skipped_not_applicable`).
Do not substitute vhost-user and keep this `scenario_id`.

Guest `ethtool` TSO flags are **not** eswitch offload (vhost-vdpa.md
§9).

---

## 6. DPU-side OVS/OVN scenario

`scenario_id`: `dpu_ovn_chassis`

**Intent:** `ovn-controller` + OVS on the **DPU OS** (or IPU ACC);
host only consumes VF. Matches overview §10–11.

**If there is no second OS / no ovn-controller on the card:** skip.
On-path SmartNIC (OVN on hypervisor) is a **different**
`scenario_id`: `host_ovn_offpath_false` — do not mix.

Measure CPU on **both** host and DPU. Policy (ACL) must be OVN-installed
on the DPU chassis, not only host iptables.

---

## 7. Vendor optimized backend scenario

`scenario_id`: `vendor_backend` plus `vendor_backend_name`:
`ovs_doca` | `dao_ovs` | `other` (`<VENDOR_DATAPATH>`).

**Intent:** OVS-DOCA, DAO OVS, or similar (`datapath_type` not
`system` TC). Generic OVS vs vendor split: `marvell-octeon.md`.

**Gate:** packages/docs for the pinned `<DOCA_VERSION>` /
`<DAO_VERSION>`. Mixing distro OVS with leftover vendor datapath
invalidates the run (`status: invalid`).

Offload evidence may be `rte_flow` / DAO counters, not `dp:tc`.
Record `offload_evidence.method` accordingly.

---

## 8. Metrics to collect

| Metric | Unit | Required |
|---|---|---|
| `throughput_bps` | bits/s (goodput) | yes |
| `pps` | packets/s | yes if frame size fixed; else `null` |
| `latency_us` | {p50,p90,p99,mean} | yes for latency jobs; `null` if not run |
| `jitter_us` | stddev or RFC jitter; name the method | same |
| `cpu_host_pct` | 0–100 of **eligible** cores, see §9 | yes |
| `cpu_dpu_pct` | same on DPU OS | `null` if no DPU OS |
| `cpu_guest_pct` | sum or max of sender/receiver guests | yes |
| `offload_hit_ratio` | 0–1 or `null` | yes field; value may be `null` |
| `loss_ratio` | 0–1 | yes |
| `duration_s` | seconds of measurement window | yes |
| `frame_size_bytes` | L2 or L3; `frame_size_layer` | yes |
| `flow_count` | concurrent 5-tuples | yes |

Also metadata: UTC `started_at`/`ended_at`, `sku`, `firmware`,
`kernel`, `ovs_version`, `scenario_id`, `tool` (`iperf3`|`trex`|…).

---

## 9. CPU measurement method

**Window:** same interval as throughput (after warmup). Do not include
VM boot.

**Host / DPU:**

- Sample `/proc/stat` **or** `mpstat -P <CPU_LIST> 1` on the **pinned
  datapath cores only** (`cpu_core_set` in JSON). Reporting 5% of 128
  idle cores as “low CPU” is invalid.
- `cpu_*_pct` = average `(user+nice+system+irq+softirq+steal) /
  (that + idle + iowait)` over the window on `cpu_core_set`.
- Record `cpu_core_set` (list of ids). Isolated CPUs for DPDK/DAO
  must be the set measured for that scenario.

**Guest:** `mpstat` inside sender and receiver; report both in
`cpu_guest_sender_pct` / `cpu_guest_receiver_pct` and optional
`cpu_guest_pct` = max of the two (document which in `cpu_guest_agg`).

**Do not** use `top` snapshots. **Do not** compare host % across
scenarios that pin different core counts without recording the set.

If DPU OS is unreachable, `cpu_dpu_pct: null` and
`notes` must say so — not `0`.

---

## 10. Throughput measurement method

- Default tool: `iperf3` TCP **and** a UDP run (separate result
  objects, `traffic.protocol`).
- Duration: ≥ `30` s measurement after ≥ `10` s warmup (repeatability
  §15). Discard warmup.
- Report **receiver** goodput (`bits_per_second` from iperf JSON
  `end.sum_received` for TCP).
- Parallel streams: record `stream_count`. Default `1` for latency
  correlation; throughput jobs may use `4` or `8` but then PPS/latency
  are a different test (`job_id`).
- MTU/TSO/GRO: record `ethtool -k` offload flags on the **guest**
  NIC; changing them between scenarios without recording invalidates
  comparison.
- North-south vs east-west: `path: vm_to_vm | vm_to_wire` (required).

TRex/`testpmd` allowed if `tool` and `command` (argv, no secrets) are
in JSON. Same duration/warmup rules.

---

## 11. PPS measurement method

PPS is **not** inferred from TCP iperf unless `frame_size_bytes` is
fixed and segmentation is off.

**Preferred:** UDP iperf3 `-l <PAYLOAD>` chosen so **on-wire** frame
size is `frame_size_bytes` (document L2 vs UDP payload in
`frame_size_layer`: `l2`|`udp_payload`). `pps` = packets received /
`duration_s` from the receiver, or iperf UDP `packets` / seconds.

**Alternative:** TRex/stateless with constant packet size; use RX
packet counters on the **destination** (guest or wire sniffer), not
TX-only.

If TSO/GSO is on, set `pps: null` and `pps_omitted_reason:
"segmentation_enabled"` unless using a packet blaster with fixed
size.

---

## 12. Latency and jitter method

Do **not** use ping as the primary metric (ICMP may bypass the path
under test).

**Preferred:** `iperf3 --udp` with `--json` latency fields **or**
`sockperf`/`qperf`/`trex` latency mode. Record tool.

Report microseconds: `latency_us.p50`, `.p90`, `.p99`, `.mean`.
Jitter: `jitter_us` with `jitter_method`: `stddev_rtt` |
`rfc3550` | `tool_native` (and name the tool field).

Load: **separate** jobs — `latency_unloaded` (low PPS) vs
`latency_at_load` (while §10 traffic runs). Do not mix into one
number.

Clock sync: for two-host RTT tools, NTP/chrony; for one-host VM-VM,
note `clock_domain: same_host`.

---

## 13. Offload hit ratio method

`offload_hit_ratio` ∈ [0,1] or `null`.

**Never** set `1.0` only because iperf was fast.

**Kernel/TC (`ovs_hw_offload_kernel`):**

1. After warmup, `ovs_offload_checker.py` on representor + PF.
2. If datapath flows exist: `ratio ≈ n_probable_offloaded /
   n_total` for flows matching the test 5-tuple **or**
   `null` if the dump cannot be filtered.
3. Optional: TC `in_hw` filter count vs total filters on that
   representor.
4. If all classifications are `unknown`: **`null`**, not `0`.

**Vendor backend:** hits from `rte_flow` / DAO / DOCA counters:
`hw_hits / (hw_hits + sw_hits)` if both exist; else `null` and
`offload_evidence.raw` excerpt (no huge dumps — store path to
artifact).

**VFIO/vDPA:** guest speed ≠ hit ratio. Ratio is still from
**eswitch/OVS/vendor** on the representor owner. If packets never
hit kernel OVS, kernel ratio is `null` (Marvell/DAO case).

`offload_evidence` object: `method`, `classification_counts`,
`checker_json_path`, `notes`.

---

## 14. Test topology

Minimum **east-west** (VM-to-VM):

```text
[Generator VM/pod] --VF/vDPA/virtio--> [Host A]
                         PCIe
                    [DPU/eswitch A] -- representor -- OVS/OVN
                         |
                      fabric / hairpin
                         |
                    [DPU/eswitch B] ... [Host B] -- [Sink VM/pod]
```

Same-host hairpin (two VFs, one card) is allowed; set
`topology: single_host_hairpin` vs `two_host`. Hairpin often
overstates cache effects — label it.

**North-south:** sink is a known tester on the wire (`vm_to_wire`).
Do not use the management SSH interface as the traffic generator.

IPs, VLANs, MTU, and OVN logical switch id go in `topology` JSON.
Placeholders in runbooks: `<GEN_IP>`, `<SINK_IP>`, `<BRIDGE>`,
`<REPR_A>`, `<REPR_B>`.

---

## 15. Repeatability rules

1. Pin `firmware`, `doca_or_dao_version`, `kernel`, `ovs_version`,
   `qemu_version` in every file. “Latest” is invalid.
2. Warmup ≥ 10 s; measure ≥ 30 s; **N ≥ 3** independent runs;
   report **median** of `throughput_bps` / `pps` / `p99` as
   `summary.*` and keep `runs[]`.
3. No other jobs on `cpu_core_set`. Record `governor` if known.
4. Change **one** variable per comparison (scenario, or frame size,
   or flow_count).
5. Reboot policy: if a scenario requires DPU reset, the next baseline
   must be re-run (stale flows).
6. Kind/netdevsim: `environment: simulation`; cannot enter
   `ci/hardware-regression` pass/fail for performance.
7. Reject outliers only with a stated rule (e.g. IQR) in `notes`;
   default keep all runs.
8. Store offload-checker JSON next to the result (`artifacts[]`).

---

## 16. Reporting format

One JSON file per **job** (scenario × path × protocol × frame size).
Filename: `{date}_{scenario_id}_{path}_{protocol}_{frame}B.json`.

Human summary (README or CI log) must include: scenario, median
throughput, median host CPU, `offload_hit_ratio` or “null
(unknown)”, SKU, firmware. Do not headline a vendor win if ratio is
`null`.

Schema: `odse-perf-result/v1alpha1` (below). Validate with a JSON
Schema checker when one is wired in `ci/lint`.

---

## JSON results schema

### JSON Schema (Draft 07)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://odse.example/schemas/perf-result-v1alpha1.json",
  "title": "ODSE performance result",
  "type": "object",
  "required": [
    "schema_version",
    "scenario_id",
    "status",
    "started_at",
    "environment",
    "identity",
    "topology",
    "traffic",
    "summary",
    "runs",
    "offload_evidence"
  ],
  "properties": {
    "schema_version": { "const": "odse-perf-result/v1alpha1" },
    "scenario_id": {
      "type": "string",
      "enum": [
        "baseline_no_offload",
        "ovs_hw_offload_kernel",
        "vfio_passthrough",
        "vhost_vdpa",
        "dpu_ovn_chassis",
        "host_ovn_offpath_false",
        "vendor_backend"
      ]
    },
    "vendor_backend_name": {
      "type": ["string", "null"],
      "enum": ["ovs_doca", "dao_ovs", "other", null]
    },
    "status": {
      "enum": ["ok", "failed", "skipped_not_applicable", "invalid"]
    },
    "started_at": { "type": "string", "format": "date-time" },
    "ended_at": { "type": ["string", "null"], "format": "date-time" },
    "environment": { "enum": ["hardware", "simulation"] },
    "identity": {
      "type": "object",
      "required": ["sku", "firmware", "kernel", "ovs_version"],
      "properties": {
        "sku": { "type": ["string", "null"] },
        "firmware": { "type": ["string", "null"] },
        "kernel": { "type": ["string", "null"] },
        "ovs_version": { "type": ["string", "null"] },
        "qemu_version": { "type": ["string", "null"] },
        "doca_or_dao_version": { "type": ["string", "null"] },
        "host_hostname": { "type": ["string", "null"] },
        "dpu_hostname": { "type": ["string", "null"] }
      }
    },
    "topology": {
      "type": "object",
      "required": ["shape", "path"],
      "properties": {
        "shape": { "enum": ["single_host_hairpin", "two_host"] },
        "path": { "enum": ["vm_to_vm", "vm_to_wire", "pod_to_pod"] },
        "bridge": { "type": ["string", "null"] },
        "notes": { "type": "string" }
      }
    },
    "traffic": {
      "type": "object",
      "required": ["tool", "protocol", "duration_s", "warmup_s", "frame_size_bytes", "flow_count"],
      "properties": {
        "tool": { "type": "string" },
        "protocol": { "enum": ["tcp", "udp", "mixed", "other"] },
        "duration_s": { "type": "number", "minimum": 1 },
        "warmup_s": { "type": "number", "minimum": 0 },
        "frame_size_bytes": { "type": ["integer", "null"] },
        "frame_size_layer": { "enum": ["l2", "udp_payload", "unknown"] },
        "flow_count": { "type": "integer", "minimum": 1 },
        "stream_count": { "type": "integer", "minimum": 1 },
        "command": { "type": ["string", "null"] }
      }
    },
    "cpu_core_set": {
      "type": "array",
      "items": { "type": "integer" }
    },
    "cpu_guest_agg": { "enum": ["max", "sum", "sender_only", "unspecified"] },
    "summary": {
      "type": "object",
      "required": ["throughput_bps", "pps", "cpu_host_pct", "offload_hit_ratio"],
      "properties": {
        "throughput_bps": { "type": ["number", "null"] },
        "pps": { "type": ["number", "null"] },
        "pps_omitted_reason": { "type": ["string", "null"] },
        "latency_us": {
          "type": ["object", "null"],
          "properties": {
            "p50": { "type": "number" },
            "p90": { "type": "number" },
            "p99": { "type": "number" },
            "mean": { "type": "number" }
          }
        },
        "jitter_us": { "type": ["number", "null"] },
        "jitter_method": { "type": ["string", "null"] },
        "cpu_host_pct": { "type": ["number", "null"] },
        "cpu_dpu_pct": { "type": ["number", "null"] },
        "cpu_guest_pct": { "type": ["number", "null"] },
        "cpu_guest_sender_pct": { "type": ["number", "null"] },
        "cpu_guest_receiver_pct": { "type": ["number", "null"] },
        "offload_hit_ratio": { "type": ["number", "null"], "minimum": 0, "maximum": 1 },
        "loss_ratio": { "type": ["number", "null"] }
      }
    },
    "runs": {
      "type": "array",
      "minItems": 1,
      "items": { "type": "object" }
    },
    "offload_evidence": {
      "type": "object",
      "required": ["method"],
      "properties": {
        "method": {
          "enum": [
            "none",
            "ovs_offload_checker",
            "rte_flow",
            "dao_counters",
            "doca_counters",
            "unavailable"
          ]
        },
        "classification_counts": { "type": ["object", "null"] },
        "checker_json_path": { "type": ["string", "null"] },
        "notes": { "type": "string" }
      }
    },
    "artifacts": {
      "type": "array",
      "items": { "type": "string" }
    },
    "notes": { "type": "string" }
  }
}
```

### Minimal example

```json
{
  "schema_version": "odse-perf-result/v1alpha1",
  "scenario_id": "ovs_hw_offload_kernel",
  "vendor_backend_name": null,
  "status": "ok",
  "started_at": "2026-08-15T12:00:00Z",
  "ended_at": "2026-08-15T12:08:00Z",
  "environment": "hardware",
  "identity": {
    "sku": "<SKU>",
    "firmware": "<FW_VERSION>",
    "kernel": "6.x",
    "ovs_version": "3.x",
    "qemu_version": null,
    "doca_or_dao_version": null,
    "host_hostname": "<HOST>",
    "dpu_hostname": "<DPU>"
  },
  "topology": {
    "shape": "single_host_hairpin",
    "path": "vm_to_vm",
    "bridge": "br-int"
  },
  "traffic": {
    "tool": "iperf3",
    "protocol": "tcp",
    "duration_s": 30,
    "warmup_s": 10,
    "frame_size_bytes": null,
    "frame_size_layer": "unknown",
    "flow_count": 1,
    "stream_count": 1,
    "command": "iperf3 -c <SINK_IP> -t 30"
  },
  "cpu_core_set": [2, 3],
  "cpu_guest_agg": "max",
  "summary": {
    "throughput_bps": 0,
    "pps": null,
    "pps_omitted_reason": "segmentation_enabled",
    "latency_us": null,
    "jitter_us": null,
    "jitter_method": null,
    "cpu_host_pct": 0,
    "cpu_dpu_pct": null,
    "cpu_guest_pct": 0,
    "cpu_guest_sender_pct": 0,
    "cpu_guest_receiver_pct": 0,
    "offload_hit_ratio": null,
    "loss_ratio": 0
  },
  "runs": [],
  "offload_evidence": {
    "method": "ovs_offload_checker",
    "classification_counts": null,
    "checker_json_path": "artifacts/checker.json",
    "notes": "Replace numeric zeros with measured medians; null remains if unknown."
  },
  "artifacts": ["artifacts/checker.json"],
  "notes": "Example only — not a real measurement."
}
```

In real files, `summary` medians come from `runs[]`; do not commit
fabricated throughputs. The zeros in the example are **placeholders
for the template**, not a baseline to compare against.
