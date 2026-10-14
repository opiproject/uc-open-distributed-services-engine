# Vendor certification process (ODSE)

Status: Draft — **how a backend earns a level**, not a certificate
from a standards body and **not** NSX/vDSE parity.

This process applies to **AMD Pensando**, **NVIDIA BlueField**,
**Intel IPU**, and **Marvell OCTEON**. A level is granted only for a
**pinned** identity: SKU + firmware + host kernel + (if used) DOCA/DAO
train. “NVIDIA” or “Intel” as a brand is not a certifiable object.

Companion:

- Functional cases: [`functional-test-plan.md`](functional-test-plan.md) (`FN-*`)
- Performance: [`performance-methodology.md`](performance-methodology.md)
- OPI: [`../../kubernetes/opi/README.md`](../../kubernetes/opi/README.md) §8
- Threat tests: [`../threat-model/host-dpu-threat-model.md`](../threat-model/host-dpu-threat-model.md) `TM-*`
- Readiness narratives: [`../vendor-readiness/`](../vendor-readiness/)
- Artifact tree: [`../../tests/vendor-certification/README.md`](../../tests/vendor-certification/README.md)

**Levels are cumulative.** Claiming Level *N* requires Levels `0 … N`
on the **same** identity. You may **stop** at the highest level the
hardware/docs support (e.g. AMD discovery-first often stops at 0–1).

**Skip vs fail vs N/A**

| Result | Meaning | Effect on level |
|---|---|---|
| Pass | Required test met; evidence stored | Counts |
| Fail | Gate was applicable and behaviour wrong | Level **not** awarded |
| Skip / N/A | Documented gate absent (no vDPA, no representors) | Cannot use that *path* to pass the level; may use an **allowed alternate** if the level text says so |
| Simulation | Kind/netdevsim | May support **Level 0** harness checks only; **not** Levels 1–5 |

Kind **must not** be used to raise
`docs/vendor-readiness/vendor-capability-matrix.md` rows to `supported`.

**Who signs:** an ODSE maintainer records `certified_level` in the
report. Vendors may supply evidence; they do not self-assign `supported`
in the matrix.

---

## Global evidence rules

Every awarded level’s report directory contains:

- `identity.json` — sku, fw, kernel, ovs, doca/dao, host/dpu hostnames
- Per-test logs/JSON named after the test id
- No secrets (Redfish passwords, SSH keys)

Metrics that do not apply are JSON `null`. Invented zeros or
`offloaded:yes` without dumps = **fail the level**, not a warning.

---

## Level 0 — Inventory only

**Intent:** The card/host is identifiable without claiming datapath.

### Required tests

- FN-01 Inventory discovery (host; DPU OS too if it exists)
- Honest `vendor_hints` (Intel `ice` ≠ IPU proof)

### Required evidence

- `dpu_inventory.py` JSON from each OS in scope
- `lspci -nn` excerpt (or equivalent) showing vendor/device ids

### Required metrics

- None (no iperf). Record `inventory_sections_available` counts only if
  useful; not a pass gate.

### Required documentation

- Filled placeholders in that vendor’s
  `docs/vendor-readiness/<vendor>.md` lab log: `<SKU>`, `<FW_VERSION>`
- Statement: DPU OS / Redfish / rshim **present or absent**

### Pass/fail

- **Pass:** FN-01 exit 0; schema valid; no fake devices.
- **Fail:** crash, fabricated PCI, claiming IPU/DSC from driver name
  alone.
- **Does not pass:** switchdev, OVS, VMs.

---

## Level 1 — VF and basic connectivity

**Intent:** A VF (or documented vendor equivalent) exists and two
endpoints using it can ping. **No** offload claim.

### Required tests

- Level 0
- FN-03 PF discovery
- FN-04 VF creation (or skip only if vendor path is **documented** and
  VFs still appear)
- FN-11 VM-to-VM **or** two host netdev VFs pinging (record which)
- FN-02 if a DPU management endpoint was claimed at L0

### Required evidence

- sysfs `sriov_numvfs` / `virtfn*` list **or** vendor VF create log
- ping capture (5 packets each way)
- If FN-02 in scope: mgmt success from control plane

### Required metrics

- Connectivity only: `ping_loss_ratio` = 0 for the test burst.
- No throughput gate.

### Required documentation

- `<PF_NAME>`, `<VF_INDEX>`, `<VF_PCI>`
- Whether representors were **not** required (L1 allows VF as plain
  NIC)

### Pass/fail

- **Pass:** VFs exist; ping works; L0 held.
- **Fail:** `sriov_setup` run against unsupported sysfs without skip;
  ping fail with VFs up.
- **N/A:** no SR-IOV on SKU → **cannot** award L1 (AMD-4 not-exposed).
- VFIO/vDPA not required.

---

## Level 2 — OVS offload baseline

**Intent:** Representor (kernel **or** documented DPDK/DAO port) is on
an OVS/DAO switch; `hw-offload` **or** vendor equivalent is set;
traffic plus **honest** offload evidence.

### Required tests

- Level 1
- FN-05 Representor discovery (kernel) **or** Marvell DPDK map
  documented in evidence (`<DPDK_REPR>`)
- FN-06 if kernel `devlink eswitch` is the chosen path; else N/A with
  reason
- FN-07 Bridge (or DAO dataplane instance)
- FN-08 `hw-offload` **or** vendor enable key (`<DAO_OFFLOAD_ENABLE>`)
- FN-17 Offload validation after FN-11 traffic
- FN-16 Telemetry/checker JSON

### Required evidence

- `vf_to_representor_map.py` JSON **or** DAO port list
- `ovs-vsctl show` **or** DAO equivalent
- `ovs_offload_checker.py` JSON; vendor `rte_flow`/DAO dump if checker
  is `unknown`

### Required metrics

- One `odse-perf-result/v1alpha1` job is **optional** at L2.
- **Required:** `offload_evidence.method` set; `offload_hit_ratio`
  may be `null` if unknown.
- **Pass gate is not** a bitrate. Pass gate **is** evidence quality.

### Required documentation

- Path: `kernel_tc` | `ovs_doca` | `dao_ovs` | `other`
- Explicit **skip of kernel TC** if DAO-only (not a fail)

### Pass/fail

- **Pass:** Attach + traffic + evidence file; if vendor docs promise
  TC `in_hw` and none appears with traffic → **fail**.
- **Fail:** `hw-offload=true` with no representor/DAO port; fake
  markers; calling baseline software OVS this level.
- **Cannot award** if FN-05 is null **and** no DPDK map (typical AMD
  without representors).

---

## Level 3 — VM offload with VFIO or vDPA

**Intent:** Guest uses **VFIO VF** and/or **vhost-vDPA**; still
forwards via the L2 eswitch/DAO (not a disconnected VF).

### Required tests

- Level 2
- **At least one of:** FN-09 VFIO **or** FN-10 vDPA (both allowed)
- FN-11 using that guest NIC
- FN-12 recommended; required if the level report claims north-south
- FN-17 on representor owner while the VM traffic runs

### Required evidence

- Guest `lspci`/`ethtool -i` (vendor NIC vs `virtio_net`)
- libvirt XML used (redact secrets)
- Checker JSON during VM traffic

### Required metrics

- Optional perf job `scenario_id` `vfio_passthrough` and/or
  `vhost_vdpa` per performance methodology (N≥3 if published).
- L3 **award does not require** beating baseline iperf.

### Required documentation

- Which path: `vfio` | `vdpa` | `both`
- IOMMU group note for VFIO
- vDPA: `<VHOST_VDPA_DEV>`; skip VFIO-only vendors without vDPA

### Pass/fail

- **Pass:** Guest ABI matches the chosen path; ping; L2 still true.
- **Fail:** virtio claimed as VFIO; vhost-user labeled `vhost_vdpa`;
  VM traffic never hits recorded representor/DAO port.
- Intel/Marvell/AMD vDPA/VFIO **discovery-required** — skip the
  missing path; L3 still possible on the other if L2 held.

---

## Level 4 — OVN / OpenStack or OVN-Kubernetes

**Intent:** Logical port binding from OVN (Neutron **or** OVN-K8s),
not only `ovs-vsctl add-port` by hand.

### Required tests

- Level 3 **or** a written exception: pods-only lab may substitute
  FN-13/14 **with VF-backed pods** instead of FN-09/10, still needing
  L2.
- **One track:**
  - **K8s:** FN-13, FN-14; DPU-mode pairing documented
    (`kubernetes/ovn-kubernetes/README.md`)
  - **OpenStack:** VM spawn with Neutron smart-NIC/DPU binding
    (when `openstack/` runbooks exist); FN-11/12 under OVN
- FN-02 still true (ovn-controller / SB not on tenant VF)

### Required evidence

- `kubectl get po,svc` **or** `openstack server/port show`
- ovn-controller chassis on the **documented** node (DPU vs host)
- Logical port ↔ representor/VF map

### Required metrics

- None required. Optional pod/VM perf job
  `path: pod_to_pod` / `vm_to_vm`.

### Required documentation

- Chart/OpenStack **pinned versions** (not comment-only Helm)
- `dpu_ovn_chassis` vs `host_ovn_offpath_false`

### Pass/fail

- **Pass:** Workload IPs work **and** bind is OVN-driven.
- **Fail:** Kind veth pods filed as L4 hardware; NVIDIA Helm keys on
  Intel; OpenStack **future** vendors (Marvell, Intel per matrix)
  cannot be awarded K8s-vs-OS they did not run.
- Matrix: OVN-K8s DPU `likely` ≠ this level until this evidence exists.

---

## Level 5 — OPI-compatible lifecycle and telemetry

**Intent:** `VendorBackend` (or vendor OPI daemon mapped to
`odse.opi/v1alpha1`) does inventory, telemetry, and at least one
lifecycle verb **without** lying about capabilities.

### Required tests

- Level 4 **or** exception: L2+L3 lab with **no** OVN yet may award
  L5 **only** as `level_5_opi_partial` in the report — **not** full
  L5. Full L5 needs L4.
- OPI-1…OPI-8, OPI-10 (`kubernetes/opi/README.md`)
- FN-16 via `collect_telemetry` **or** documented mapping from
  checker JSON into `TelemetrySample`
- `submit_lifecycle` **dryRun** if supported; if not, **explicit
  error** (OPI-7) — that error is a **pass** for the flag
- FN-18 **or** TM-8: one recorded fail-open/fail-closed observation
- TM-1 / OPI-10: tenant VF cannot reach OPI listen address

### Required evidence

- Adapter/daemon logs (no secrets)
- Sample objects: `DPUDevice`, `TelemetrySample`,
  `LifecycleOperation` JSON
- `switchdevSupported`/`vdpaSupported` **false** unless L2/L3 proved
  them (OPI-2)

### Required metrics

- Telemetry: at least one real counter; **no** synthetic zeros
- Lifecycle: `status.state` transitions recorded

### Required documentation

- Mapping table: vendor proto → `odse.opi/v1alpha1` (Intel especially)
- `opi_services` listen/TLS facts

### Pass/fail

- **Pass:** Objects validate; OPI-5 reject `hwOffload` if unproven;
  tenant cannot scrape OPI.
- **Fail:** `UnsupportedOperation` stubs filed as L5; `hwOffload:
  true` on AMD without L2; daemon on `0.0.0.0` without TLS.

---

## Vendor expectations (not automatic levels)

| Vendor | Typical ceiling until labs exist | Notes |
|---|---|---|
| NVIDIA | Highest public datapath evidence | Still start L0; DOCA is L2 `ovs_doca` not automatic L5 |
| Intel | OPI/OpenShift **likely**; datapath discovery | May reach L5 mapping before kernel L2 — **full L5 still needs L4** |
| Marvell | DAO L2 via DPDK repr | Kernel FN-06 N/A OK; K8s L4 **future** |
| AMD | L0–L1 until representors/API found | Do not award L2 on `ionic` alone |

---

## Matrix updates

A `supported` cell in `vendor-capability-matrix.md` requires:

1. This process at the matching level (e.g. switchdev ⇔ L2 kernel path)
2. Link to `tests/vendor-certification/reports/<vendor>/<id>/`
3. Date and identity pin

`likely` stays until that exists. Downgrade if a later lab fails.

---

## Example certification report template

Copy to
`tests/vendor-certification/reports/<vendor>/<YYYYMMDD>-<sku>/REPORT.md`.

```markdown
# ODSE vendor certification report (template)

- vendor: nvidia | intel | marvell | amd
- sku: <SKU>
- firmware: <FW_VERSION>
- host_os / kernel: <HOST_OS>
- dpu_os: <DPU_OS> | none
- doca_or_dao_version: <VER> | n/a
- report_id: <YYYYMMDD>-<sku>
- operator: <name>
- date_utc: <RFC3339>
- claimed_level: 0-5
- awarded_level: 0-5 | none
- environment: hardware   # simulation cannot award ≥1

## Identity
Attach identity.json (lspci, inventory hashes).

## Level checklist
| Level | Result (pass/fail/not_run) | Evidence paths |
| 0 | | inventory.json |
| 1 | | vf-list.txt, ping.log |
| 2 | | map.json, checker.json, ovs-show.txt |
| 3 | | guest-lspci.txt, domain.xml |
| 4 | | ovn-or-k8s logs |
| 5 | | opi-samples.json |

## Tests run
| ID | Result | Artifact |
| FN-01 | pass/fail/skip | |
| … | | |

## Metrics (if any)
Link odse-perf-result JSON. offload_hit_ratio: <n | null>

## OPI (L5)
switchdevSupported: true/false
vdpaSupported: true/false
hwOffload rejected when unproven: yes/no/n/a

## Fail-open / fail-closed (L5 / FN-18)
observed: open | closed | mixed | not_run

## Deviations
(e.g. L3 vdpa skip; L2 dao_ovs not kernel_tc)

## Maintainer decision
awarded_level: <n>
matrix_updates_proposed: none | list rows
signature: <maintainer>
```

Example `identity.json`:

```json
{
  "schema_version": "odse-cert-identity/v1alpha1",
  "vendor": "nvidia",
  "sku": "<SKU>",
  "firmware": "<FW_VERSION>",
  "kernel": "<KERNEL>",
  "ovs_version": "<OVS>",
  "doca_or_dao_version": null,
  "host_hostname": "<HOST>",
  "dpu_hostname": "<DPU>",
  "claimed_level": 0,
  "awarded_level": null
}
```
