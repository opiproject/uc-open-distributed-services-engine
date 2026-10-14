# Vendor maturity execution plan — VM-to-VM single-DPU lab

How to bring up [the VM-to-VM single-DPU use case](lab-runbook-vm2vm-single-dpu.md)
for each of the four ODSE vendor tracks **one at a time**, without
copying NVIDIA's switchdev/OVS-TC/OVS-DOCA assumptions onto Marvell,
Intel, or AMD.

This is an **execution plan**, not a certification. It tracks how far a
given vendor track has actually been run on real hardware, using the
tooling built for this use case (`tools/labctl.py`,
`tools/lab_cleanup.py`, `tools/topology-discovery/vf_to_representor_map.py`,
`tools/offload-checker/ovs_offload_checker.py`,
`tools/forwarding_intent.py`). Code existing in `plugins/<vendor>/` is
**not** the same as a stage being proven — see
[Rule 6, vendor-implementation-roadmap.md](vendor-implementation-roadmap.md#implementation-rules).

Companions:

- [`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md) — the 15 `labctl` stages this plan maps onto.
- [`labctl-usage.md`](labctl-usage.md) — CLI reference for every stage below.
- [`vendor-implementation-roadmap.md`](vendor-implementation-roadmap.md) — the repo-wide, use-case-agnostic 7-phase model (discovery → certification). This document is the VM-to-VM-specific version of that model.
- [`../vendor-readiness/`](../vendor-readiness/) — per-vendor hardware/firmware discovery notes (DOCA, DAO, IMC/Redfish, ionic).
- [`cleanup-and-rollback.md`](cleanup-and-rollback.md) — what Stage 8 cleanup does and does not guarantee.
- [`../../config/README-lab-vm2vm.md`](../../config/README-lab-vm2vm.md) — lab config schema referenced below as `config/lab-<vendor>-vm2vm.yaml`.

**Do not skip stages** by inventing devices, representors, VF-to-representor
mappings, or `offloaded: yes`. A vendor track advances one stage at a
time, and only when the pass criteria for that stage are met with
evidence from real hardware (`tests/results/<vendor>/<run-id>/...`).
Fixture tests and dry-run output are necessary but not sufficient to
advance past Stage 1.

---

## 1. Shared maturity stage model

Every vendor track is scored against the same ten stages. The stage
number is the **highest stage fully passed**; a track is not "at
Stage 5" if Stage 4 evidence is missing or stale.

| Stage | Name | What "pass" means | Primary `labctl` stage(s) |
|---|---|---|---|
| 0 | Inventory only | `tools/inventory/dpu_inventory.py` identifies the vendor PCI device (vendor/device ID, driver) on the real host. No mutation attempted. | `discover` |
| 1 | PF and driver discovery | PF name, PF PCI address, driver, and firmware version are read from the real device and match `config/lab-<vendor>-vm2vm.yaml`. `validate-config` passes against real values, not placeholders. | `discover`, `validate-config` |
| 2 | SR-IOV VF creation | `sriov_numvfs` set to the configured count on the real PF; VFs enumerate in sysfs with the expected PCI addresses. `--dry-run` plan matches what `--apply` actually did. | `prepare-vfs` |
| 3 | VF-to-representor mapping | `vf_to_representor_map.py` (or vendor plugin) resolves every configured VF to a representor (or documents why the vendor has none, e.g. AMD without switchdev) with a named `mapping_method`, not `phys_port_name` guessing. | `prepare-switchdev`, `discover-representors` |
| 4 | OVS bridge attach | Representors (or vendor-specific ports) are added to the OVS bridge from `ovs.bridge` in config; `ovs-vsctl show` matches the intended topology. Software forwarding path exists end to end. | `prepare-ovs` |
| 5 | VM VF passthrough | Both VMs boot with the assigned VF attached via VFIO; guest `lspci` shows the vendor NIC/VF, not virtio-net (unless the track is vDPA, which is Stage 9). | `create-vms`, `attach-vfs` |
| 6 | VM-to-VM traffic | Ping and `iperf3` succeed between the two VM guest IPs across the intended path (VF → representor → OVS bridge, or vendor-specific datapath). This proves connectivity only, not offload. | `configure-forwarding`, `configure-vm-ips`, `run-traffic` |
| 7 | Offload evidence | `ovs_offload_checker.py` (or the vendor's offload evidence source, e.g. DAO/rte_flow counters) reports at least `probable_offloaded` or `probable_partial` with a stated confidence, using real dpctl/tc/ethtool (or vendor-equivalent) signals gathered during Stage 6 traffic. | `validate-offload` |
| 8 | Telemetry and cleanup | Vendor `collect_telemetry()` (or vendor-neutral fallback) returns real, non-fabricated samples, and `lab_cleanup.py --apply` has been run at least once end to end with a clean `cleanup-summary.json` (no unexpected `error` entries). | `collect-evidence`, `cleanup` |
| 9 | Advanced backend path | The vendor-specific "beyond kernel OVS-TC" datapath for that track (OVS-DOCA for NVIDIA, DAO offload for Marvell, OPI/DPU-Operator-driven path for Intel, or a documented vDPA/advanced path once discovered for AMD) is proven with the same evidence bar as Stage 7. | Vendor plugin `configure_forwarding` / `collect_telemetry` beyond the kernel default |

Stages 0–8 are **shared shape** across vendors; only the backend
mechanism differs. Stage 9 is intentionally vendor-defined because the
"advanced" datapath is not the same technology per vendor.

A track can be legitimately **blocked** below Stage 9 forever (e.g. AMD
without switchdev support on its firmware never reaches Stage 3/4 in
the OVS sense) — record that as a blocker, not a failure to try harder.

---

## 2. NVIDIA BlueField

Code: `plugins/nvidia_bluefield/` (`discovery.py`, `switchdev.py`,
`ovs.py`, `vdpa.py`, `telemetry.py`, `backend.py`). Hardware notes:
[`../vendor-readiness/nvidia-bluefield.md`](../vendor-readiness/nvidia-bluefield.md),
[`../vendor-readiness/nvidia-bluefield-implementation.md`](../vendor-readiness/nvidia-bluefield-implementation.md).

- **Expected first target:** Stage 7 (offload evidence) on a single
  BlueField-2/3 card with kernel OVS-TC — this is the vendor with the
  most mature in-tree kernel path (`mlx5`, `devlink eswitch`,
  `phys_port_name=pf0vf<N>`), so it should be the first track to reach
  a `probable_offloaded` verdict.
- **Likely backend path:** switchdev (`devlink eswitch set pcivf`) →
  representors on `br-int` with `other_config:hw-offload=true` → kernel
  TC-flower offload. OVS-DOCA (Stage 9) is optional and only attempted
  once `doca` appears in the eswitch's advertised datapath types.
- **What not to assume:** DPU mode vs NIC mode changes who owns
  switchdev (Arm PF vs host PF — see vendor-readiness §2/§4). Do not
  flip host-side eswitch mode in DPU mode. Do not assume `mlxconfig`
  flag names or that VF creation needs no firmware reset — confirm
  against the pinned `<DOCA_VERSION>` first.
- **Required config file:** `config/lab-nvidia-vm2vm.yaml` (copied from
  `lab-nvidia-vm2vm.example.yaml`, `vendor.type: nvidia-bluefield`).
- **Required plugin files:** `plugins/nvidia_bluefield/discovery.py`,
  `switchdev.py`, `ovs.py`, `backend.py` (all already implemented; only
  `vdpa.py` and the OVS-DOCA path are Stage 9 candidates).
  `tools/topology-discovery/vendor_plugins/nvidia_sf.py` only if the
  lab uses Scalable Functions instead of VFs.
- **Required tests:** `tests/vendor/nvidia/test_discovery.py`,
  `test_switchdev.py`, `test_ovs.py`, `test_configure_forwarding.py`
  (all present and fixture-passing); hardware evidence lives under
  `tests/results/nvidia-bluefield/<run-id>/` per stage, not as new unit
  tests.
- **Pass criteria:** Stages 0–7 per §1, using `mapping_method:
  devlink_pcivf` at Stage 3 and TC `in_hw` or dpctl offloaded flows at
  Stage 7. Stage 9 requires `datapath_type` to actually be the DOCA
  string documented for the pinned train, not assumed.
- **Blockers to record:** DOCA/BFB/firmware train drift; whether VF
  creation requires a firmware NUM_OF_VFS write before OS
  `sriov_numvfs`; host-vs-Arm PF split in DPU mode; OVS package
  conflicts if OVS-DOCA packages replace distro OVS.

**Vendor-specific guidance:** start with switchdev, representors, OVS
hardware offload, then OVS-DOCA optional.

---

## 3. Marvell OCTEON

Code: `plugins/marvell_octeon/` (`discovery.py`, `dpdk.py`,
`dao_ovs.py`, `telemetry.py`, `backend.py`). Hardware notes:
[`../vendor-readiness/marvell-octeon.md`](../vendor-readiness/marvell-octeon.md),
[`../vendor-readiness/marvell-octeon-implementation.md`](../vendor-readiness/marvell-octeon-implementation.md).

- **Expected first target:** Stage 1 (PF and driver discovery) plus
  DAO/DPDK binary presence checks. Do not target Stage 4+ (kernel OVS
  bridge attach) until it is confirmed the lab card exposes kernel
  representors at all — Marvell's offload story is DPDK/DAO-first, not
  kernel-switchdev-first.
- **Likely backend path:** DPDK-bound representor ports plus DAO OVS
  offload (`dao_ovs.py`), evaluated against `rte_flow`/DAO counters
  rather than kernel TC. Kernel switchdev + `devlink eswitch` is
  **not assumed** to exist on this SKU until discovery proves it.
- **What not to assume:** that `ovs-vsctl show` listing ports means DAO
  offload is active (`ovs_present: true` does not imply
  `ovs_hw_offload_available`); that DAO env vars or SDK binaries being
  present means a running datapath; that kernel representor naming
  matches NVIDIA's `pf0vf<N>` convention.
- **Required config file:** `config/lab-marvell-vm2vm.yaml` (from
  `lab-marvell-vm2vm.example.yaml`, `vendor.type: marvell-octeon`,
  `ovs.datapath_type: netdev`).
- **Required plugin files:** `plugins/marvell_octeon/discovery.py`
  (implemented), `dpdk.py` (implemented), `dao_ovs.py` (implemented as
  discovery-required stub — needs real DAO CLI wiring before Stage 4),
  `backend.py`.
- **Required tests:** `tests/vendor/marvell/test_discovery.py`,
  `test_dao.py`,
  `test_configure_forwarding_discovery_required.py` (present). A new
  `test_configure_forwarding.py` with real DAO evidence is required
  before claiming Stage 4+ in this document's table.
- **Pass criteria:** Stages 0–3 use kernel discovery only. Stage 4
  requires either documented kernel representor support **or** a DAO
  OVS bridge with representor-equivalent ports — record which path the
  SKU actually took. Stage 7 requires DAO/`rte_flow` hit counters, not
  kernel `in_hw`.
- **Blockers to record:** whether kernel representors exist on this
  SKU at all; which DAO/DPDK version is paired with which SDK; whether
  `configure-ovs` can stay skipped through Stage 6 while still proving
  VM-to-VM traffic over a DPDK path.

**Vendor-specific guidance:** start with DAO OVS offload and DPDK
representor model if available.

---

## 4. Intel IPU

Code: `plugins/intel_ipu/` (`discovery.py`, `opi.py`,
`dpu_operator.py`, `telemetry.py`, `backend.py`). Hardware notes:
[`../vendor-readiness/intel-ipu-e2100.md`](../vendor-readiness/intel-ipu-e2100.md),
[`../vendor-readiness/intel-ipu-implementation.md`](../vendor-readiness/intel-ipu-implementation.md).

- **Expected first target:** Stage 0/1 — confirm the lab NIC is
  actually an IPU/E2100/"Infrastructure Processing Unit" and not an
  `ice`/E810 NIC (matrix note: **ice / E810 is not an IPU**), then
  confirm whatever management path (Redfish/IMC/OPI) the SKU documents
  is reachable.
- **Likely backend path:** OPI and/or DPU Operator-driven configuration
  (`opi.py`, `dpu_operator.py`) rather than direct `devlink`/`ovs-vsctl`
  on the host. SR-IOV/switchdev/OVS/vDPA all stay `skipped` in the
  capability report until a named IPU procedure exists for the pinned
  IMC/firmware version.
- **What not to assume:** the NVIDIA-style OVS-DOCA flow, `devlink
  eswitch` semantics, or that Redfish "configured" means Redfish
  "probed" (a config entry existing is not proof the client executed
  anything — the HTTP client stays inert until implemented). Management
  CLI values in config are read, never executed, until the client is
  real.
- **Required config file:** `config/lab-intel-vm2vm.yaml` (from
  `lab-intel-vm2vm.example.yaml`, `vendor.type: intel-ipu`).
- **Required plugin files:** `plugins/intel_ipu/discovery.py`
  (implemented), `opi.py`, `dpu_operator.py` (both currently
  discovery-required stubs), `backend.py`.
- **Required tests:** `tests/vendor/intel/test_discovery.py`,
  `test_opi.py`,
  `test_configure_forwarding_discovery_required.py` (present). Real
  Stage 4+ evidence needs a new test exercising the actual OPI/DPU
  Operator call path once implemented, not the mock client.
- **Pass criteria:** Stage 1 requires a real IPU PCI ID match (not
  `ice`). Stage 4+ requires a documented, named IPU datapath
  configuration procedure — no "OPI deploy succeeded" being treated as
  `in_hw` at Stage 7 without an offload signal.
- **Blockers to record:** whether the SKU is reachable at all without a
  DPU Operator/Kubernetes control plane; which management interface
  (Redfish vs IMC CLI vs OPI gRPC) the pinned firmware documents;
  whether kernel representors exist on the host side.

**Vendor-specific guidance:** start with discovery, Redfish/management,
OPI/DPU Operator path. Do not assume NVIDIA-style OVS-DOCA flow.

---

## 5. AMD Pensando

Code: `plugins/amd_pensando/` (`discovery.py`, `ionic.py`, `rdma.py`,
`telemetry.py`, `backend.py`). Hardware notes:
[`../vendor-readiness/amd-pensando.md`](../vendor-readiness/amd-pensando.md),
[`../vendor-readiness/amd-pensando-implementation.md`](../vendor-readiness/amd-pensando-implementation.md)
(gap template G-01…G-12).

- **Expected first target:** Stage 0/1 via `ionic`/`ionic_rdma` kernel
  module and `ethtool -i` discovery. Do not target switchdev,
  representors, OVS offload, or vDPA stages until discovery on the
  actual DSC proves they exist — this track has the fewest confirmed
  capabilities in the matrix today.
- **Likely backend path:** unknown/discovery-required beyond `ionic`
  netdev + VF passthrough. If representors do not exist on this SKU,
  the AMD-7 OPI Network path is the documented contingency rather than
  fabricating `br-int` ports.
- **What not to assume:** that an AMD host bridge is a DSC (PCI ID
  check first); that missing RDMA support is an Ethernet failure
  (`ionic` without `ionic_rdma` is a valid, partial state); that OVS
  hardware offload is available (`try_ovs_offload` stays `false` until
  proven, and capability report keeps `ovs_hw_offload` `null`, not
  `false`-as-disproof).
- **Required config file:** `config/lab-amd-vm2vm.yaml` (from
  `lab-amd-vm2vm.example.yaml`, `vendor.type: amd-pensando`).
- **Required plugin files:** `plugins/amd_pensando/discovery.py`
  (implemented), `ionic.py` (implemented), `rdma.py` (implemented),
  `backend.py`. No switchdev/OVS module exists yet — do not create one
  before discovery justifies it.
- **Required tests:** `tests/vendor/amd/test_discovery.py`,
  `test_ionic.py`,
  `test_configure_forwarding_discovery_required.py` (present). Gap
  entries G-07…G-10 in the vendor-readiness doc must be filled from a
  real lab before writing new configure-stage tests.
- **Pass criteria:** Stage 1 requires real Pensando/`ionic` PCI ID
  match. Stages 2–4 require an explicit lab finding (yes/no) for
  SR-IOV, switchdev, and representor support before any config/plugin
  work targets them. Stage 6 (VM-to-VM traffic) may only ever be
  proven over plain `ionic` VF passthrough without OVS if that is what
  discovery shows — that is a valid, if lower, milestone.
- **Blockers to record:** whether this SKU exposes representors or
  switchdev at all; RDMA availability; whether AMD-7 OPI Network is
  required to reach Stage 4 equivalence.

**Vendor-specific guidance:** start with ionic and ionic_rdma
discovery. Do not assume switchdev, representors, OVS offload, or vDPA
until discovered.

---

## 6. Vendor maturity table

This table is the living status tracker. Update it after every lab
session with real evidence paths — do not advance "Current stage"
without a corresponding `tests/results/<vendor>/<run-id>/` entry.

As of this document's creation, no vendor track has run against
physical hardware in this repo's lab tooling; all four are Stage 0
(tooling and fixtures exist, hardware not yet exercised).

| Vendor | Current stage | Next stage | Evidence required | Automation status | Risk | Notes |
|---|---|---|---|---|---|---|
| NVIDIA BlueField | 0 — Inventory only (no card run yet) | 1 — PF and driver discovery | `dpu_inventory.py` output + `discover` stage JSON from a real BlueField-2/3 card | Discovery, switchdev, OVS, and forwarding-intent code paths implemented and fixture-tested (`tests/vendor/nvidia/`); `labctl.py` full pipeline dry-run verified | Low for Stages 1–4 (mature kernel path); medium for OVS-DOCA (Stage 9, packaging drift) | Best-positioned track to reach Stage 7 first; do not let its maturity set the bar for the other three |
| Marvell OCTEON | 0 — Inventory only (no card run yet) | 1 — PF and driver discovery | `discover` stage JSON showing OCTEON/cnxk PCI IDs; DAO/DPDK binary presence report | Discovery and DPDK detection implemented; `dao_ovs.py` is a discovery-required stub (no real DAO CLI wiring) | Medium — offload story depends on DAO version/SDK pairing that is not yet pinned | Kernel representors not confirmed to exist on target SKU; may need DPDK-only Stage 4 evidence |
| Intel IPU | 0 — Inventory only (no card run yet) | 1 — PF and driver discovery, confirm IPU vs `ice`/E810 | `discover` stage JSON with a genuine IPU/E2100 PCI ID (not `ice`) | Discovery implemented; `opi.py`/`dpu_operator.py` are discovery-required stubs, HTTP/gRPC clients inert | Medium-high — management path (Redfish/IMC/OPI) not yet confirmed for the target SKU/firmware | Do not reuse NVIDIA switchdev/OVS-DOCA assumptions; likely needs a DPU Operator control plane before Stage 4 |
| AMD Pensando | 0 — Inventory only (no card run yet) | 1 — PF and driver discovery via `ionic` | `discover` stage JSON with Pensando PCI ID + `ionic`/`ionic_rdma` module state | Discovery, `ionic`, and `rdma` collectors implemented; no switchdev/OVS module exists (intentional, capability unconfirmed) | High — most capabilities in the matrix are `discovery-required`; Stage 4+ may not be reachable on this SKU | Gap template G-01…G-12 in vendor-readiness doc must be filled from lab findings before advancing |

---

## 7. How to advance a stage

1. Run the relevant `labctl.py` stage(s) from §1's table with
   `--dry-run` first, then `--apply` once the plan looks correct and
   `config/lab-<vendor>-vm2vm.yaml` has `safety.dry_run_default: false`.
2. Confirm the JSON output under
   `tests/results/<vendor>/<run-id>/<stage>/` matches the pass
   criteria in that vendor's section above — not just "the command
   exited 0".
3. Update this document's §6 table: bump "Current stage" only after
   evidence exists, update "Next stage", link the run-id, and record
   any new blocker.
4. If a stage cannot be reached (e.g. AMD has no representors), record
   that explicitly as a permanent or provisional blocker rather than
   leaving the table stale — a documented "stuck at Stage 3, no
   representors on this firmware" is more useful than silence.
5. Never backfill a stage from a Kind/netdevsim run or from another
   vendor's evidence. Per-vendor, per-SKU only.
