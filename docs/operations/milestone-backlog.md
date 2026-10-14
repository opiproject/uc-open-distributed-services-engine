# Milestone backlog (ODSE)

This is the **engineering backlog** derived from
[`open-source-vdse-equivalent-plan.md`](../../open-source-vdse-equivalent-plan.md)
and the design rules in that document §11. It is **not** a claim that
milestones are complete. Many trees already contain **skeletons**
(docs, Ansible roles, Python stubs); a milestone is done only when
**acceptance criteria** below are met.

Rules that apply to every milestone:

- Vendor logic stays behind plugins; do not assume switchdev/vDPA/OVS
  offload on every SKU.
- Do not claim NSX/vDSE equivalence.
- Tests (`FN-*`, certification levels) are the contract.
- Kind/simulation cannot satisfy hardware-only acceptance.

Certification mapping (informal): M1 ≈ L0–L1, M2 ≈ L2, M3 ≈ L3, M4/M5
≈ L4, M6 ≈ L5 (partial without OVN), M7 = per-vendor levels, M8 =
threat tests, M9 = L2–L5 + perf schema.

---

## Summary

| ID | Milestone | Goal (one line) | Depends on | Cert level (typical) |
|---|---|---|---|---|
| M0 | Repository and architecture | Shared mental model and repo layout | — | — |
| M1 | Inventory and host baseline | Discover hardware; prepare Linux hosts | M0 | L0 |
| M2 | OVS hardware offload baseline | Representors + OVS + honest offload evidence | M1 | L2 |
| M3 | KVM/QEMU VFIO and vDPA | Guests on VF or virtio-vDPA | M2 | L3 |
| M4 | OpenStack off-path DPU | Nova/Neutron/OVN bind to DPU representor | M3 | L4 (OS) |
| M5 | OVN-Kubernetes DPU | Two-cluster (or equivalent) pod offload | M2 (sim), M3 (hw) | L4 (K8s) |
| M6 | OPI abstraction layer | Vendor-neutral API + adapter | M1; L5 needs M4 or M5 | L5 |
| M7 | Vendor backend enablement | NVIDIA first; Intel/Marvell/AMD per docs | M2–M6 as applicable | per vendor |
| M8 | Security and threat model | Host/DPU/control-plane tests, no overclaim | M2+ | TM-* |
| M9 | Performance and certification | Methodology + signed vendor reports | M2–M8 | L0–L5 |

Suggested sequence: **M0 → M1 → M2 → M3**, then **M5 simulated** in
parallel with **M7 NVIDIA**, then **M4** / **M5 hardware**, **M6**,
**M8** throughout, **M9** as evidence accumulates. AMD discovery
(M7) can run beside M1 without blocking NVIDIA L2.

---

## M0 — Repository and architecture

**Goal:** Anyone can find the plan, roles, trust boundary, and vendor
matrix without inventing a second architecture.

### Stories

1. Publish architecture overview, API model, vDSE mapping, roles/trust.
2. Publish vendor capability matrix (conservative ratings).
3. Freeze repo layout (`ansible/`, `docs/`, `qemu/`, `kubernetes/`,
   `tools/`, `tests/`, `ci/`).
4. Record non-goals (no NSX clone, no uniform vendor capability).

### Deliverables

- `docs/architecture/*`, `open-source-vdse-equivalent-plan.md`
- `docs/vendor-readiness/vendor-capability-matrix.md`
- Top-level `README.md`

### Acceptance criteria

- New contributor can explain host vs DPU vs control plane from docs.
- Matrix has no `supported` row without a cited reason (and later M9
  evidence for upgrades).

### Risks

- Docs drift from code; treat this backlog as the check.

### Dependencies

- None.

### Suggested tests

- Doc review only (no FN). `ci/lint` when yamllint/markdown exists.

---

## M1 — Inventory and host baseline

**Goal:** Enumerate PCI/netdev/devlink/OVS on a node; install baseline
packages; create VFs when sysfs allows — without claiming offload.

### Stories

1. `dpu_inventory.py` graceful JSON.
2. `ansible/roles/host_base` (virt/IOMMU check, KVM, OVS packages).
3. `ansible/roles/sriov_setup` with disruptive-change gate.
4. `ansible/roles/dpu_base` skeleton for DPU OS when reachable.

### Deliverables

- `tools/inventory/`, `ansible/roles/host_base`, `sriov_setup`,
  `dpu_base`

### Acceptance criteria

- FN-01 pass on a Linux host (even without a DPU).
- FN-03 pass for a chosen PF.
- FN-04 pass **or** documented skip (no `sriov_totalvfs`).
- `host_base` does not fail the play on missing IOMMU unless told to.

### Risks

- `sriov_numvfs` reset destroys VMs; role must refuse by default.
- Inventory vendor_hints over-claimed (Intel `ice`).

### Dependencies

- M0.

### Suggested tests

- FN-01, FN-03, FN-04. Cert **Level 0** (and L1 if VFs+ping later).

---

## M2 — OVS hardware offload baseline

**Goal:** Map VF→representor (or document DPDK-only), put ports on
OVS, set `hw-offload` when applicable, collect checker JSON.

### Stories

1. `switchdev_setup` via `devlink` (vendor hooks only if needed).
2. `ovs_setup` bridges/ports/hw-offload/datapath_type.
3. `vf_to_representor_map.py` conservative matching.
4. `ovs_offload_checker.py` + `dpu_telemetry_collector.py`.

### Deliverables

- Those roles/tools; FN-05–08/16/17 evidence on **hardware** for at
  least one SKU **or** explicit N/A (no representors).

### Acceptance criteria

- FN-07 pass wherever OVS runs.
- FN-05: non-null representor **or** honest null + vendor note.
- FN-08 matches intent when kernel OVS is the datapath.
- FN-17: checker runs; no fabricated `offloaded:yes`.
- Kind must not be used to mark L2 complete.

### Risks

- Running host switchdev in NVIDIA DPU mode (host PF legacy).
- Treating AMD `ionic` as TC offload.
- Marvell DAO vs distro OVS mix.

### Dependencies

- M1. Vendor docs for eswitch owner (host vs DPU OS).

### Suggested tests

- FN-05, FN-06, FN-07, FN-08, FN-16, FN-17. Cert **Level 2**.

---

## M3 — KVM/QEMU VFIO and vDPA

**Goal:** Two documented guest paths: vendor NIC via VFIO, virtio-net
via vhost-vDPA; VM-to-VM (and optionally VM-to-wire) connectivity.

### Stories

1. VF passthrough runbook + libvirt template.
2. vDPA runbook + libvirt template; skip if no mgmtdev.
3. Attach representor still required for eswitch labs (from M2).
4. Note VFIO vs vDPA isolation (threat model / vDPA host kernel).

### Deliverables

- `qemu/vfio/`, `qemu/vdpa/`, `qemu/libvirt-xml/` templates
- `ansible/roles/qemu_vdpa_setup` beyond README when implemented

### Acceptance criteria

- FN-09 **or** skip with empty `vdpa`/`vfio` gate documented.
- FN-10 **or** skip.
- FN-11 pass on the path that was not skipped.
- FN-12 if north-south is in scope.

### Risks

- Shared IOMMU groups; guest driver missing for VFIO.
- Labeling vhost-user as vDPA.

### Dependencies

- M2 for eswitch labs. M1 IOMMU for VFIO.

### Suggested tests

- FN-09, FN-10, FN-11, FN-12. Cert **Level 3**.

---

## M4 — OpenStack off-path DPU model

**Goal:** Neutron/OVN logical port binds to DPU representor; Nova
consumes host VF; `ovn-controller` on documented chassis.

### Stories

1. Architecture note `docs/architecture/openstack-offpath-dpu.md`
   (if missing) + `openstack/neutron-ovn-smartnic/`.
2. Port-binding profile / `vnic_type` placeholders filled from
   OpenStack + vendor docs (`<NEUTRON_VNIC_TYPE>`).
3. Nova PCI/SR-IOV whitelist for the VF.
4. Failure: host nova-compute down vs DPU ACL (link M8).

### Deliverables

- OpenStack runbooks; pinned OpenStack release in the lab report.

### Acceptance criteria

- FN-11/12 under **OVN-created** ports, not only manual `ovs-vsctl`.
- Chassis location recorded (`dpu` vs `host`).
- Cert **Level 4** OpenStack track. Intel/Marvell **future** in
  matrix — do not fake a pass.

### Risks

- Binding profile mismatch; OVN on wrong chassis.
- Scope creep into full OpenStack distro install.

### Dependencies

- M3 (VF) and M2 (representor/OVS). OVN (`ovn_setup`) on DPU/host.

### Suggested tests

- FN-11, FN-12, FN-02 (SB not on tenant net). OpenStack-specific
  spawn test when written.

---

## M5 — OVN-Kubernetes DPU model

**Goal:** Host cluster + DPU cluster (or equivalent split); pods with
VF `eth0`; representor bound on DPU; simulated CI without hardware.

### Stories

1. Document two-cluster model; comment-only Helm until chart pin.
2. Kind scripts: `ci/kind-simulated-dpu` (no fake L2–L5 cert).
3. Hardware overlay: pairing labels, SR-IOV plugin resource name.
4. FN-13/14 on hardware with VF, not only Kind veth.

### Deliverables

- `kubernetes/ovn-kubernetes/**`, Kind create/delete scripts
- Pinned chart version (acceptance for “deployed”)

### Acceptance criteria

- Simulated: two Kind clusters create/delete safely (scripts exist).
- Hardware: FN-13, FN-14; pod `eth0` is VF if DPU mode claimed.
- Helm `hardware/values.yaml` still comments-only ⇒ **not** M5 done.

### Risks

- Using simulated success as NVIDIA/Intel L4.
- Pairing host VF index ≠ DPU `vfnum`.

### Dependencies

- M0 docs. Simulated: none else. Hardware: M2–M3, M7 for resource
  names.

### Suggested tests

- K8S-DPU-1…3 (README); FN-13, FN-14. Cert **Level 4** K8s track.

---

## M6 — OPI abstraction layer

**Goal:** Common objects + `VendorBackend`; Ansible does not expose
OPI on tenant networks; capabilities stay honest.

### Stories

1. `kubernetes/opi` objects + adapter ABC.
2. `ansible/roles/opi_services` TLS/loopback safety.
3. Implement `list_devices` / telemetry / lifecycle per vendor as
   tracks allow (else `UnsupportedOperation`).
4. OPI-1…10 conformance notes.

### Deliverables

- `kubernetes/opi/adapter/`, `opi_services`, api-model alignment

### Acceptance criteria

- Stubs load via `load_backend('<vendor>')`.
- Non-loopback listen without TLS **fails** the role.
- Full cert L5 only with M4 or M5 (see vendor-certification.md).
- `hwOffload: true` rejected when L2 unproven (OPI-5).

### Risks

- Intel daemon ≠ `odse.opi/v1alpha1` without a mapping table.
- Shipping an open gRPC port in the skeleton.

### Dependencies

- M1 inventory. L5: M4 or M5 + M8 TM-1.

### Suggested tests

- OPI-1…8, OPI-10; FN-15 skip until NF implemented; FN-16.

---

## M7 — Vendor backend enablement

**Goal:** Per-vendor workstreams executed in **documented** order:
NVIDIA datapath first; Intel OPI/OpenShift-first; Marvell DAO/DPDK;
AMD discovery-first (no assumed OVS offload).

### Stories

1. NVIDIA: `nvidia-bluefield.md` + role; kernel OVS-TC lab (M2/M3).
2. Intel: Redfish/IMC discovery; no copied NVIDIA eswitch playbook.
3. Marvell: generic OVS vs DAO; DPDK representors; `rte_flow`.
4. AMD: ionic → SR-IOV? → representors?; OPI fallback AMD-7.
5. Keep matrix ratings honest (upgrade only via M9 reports).

### Deliverables

- Four `docs/vendor-readiness/*.md` + `ansible/roles/vendor_*`
- Lab identity pins (`<DOCA_VERSION>`, `<DAO_VERSION>`, `<DSC_SKU>`)

### Acceptance criteria

- Each vendor has a readiness doc and a role that **defaults to
  discover/check**, not firmware flash.
- At least one NVIDIA hardware path through M2 (project sequencing)
  **or** a dated blocker in the NVIDIA report.
- AMD must not have L2 awarded on inventory alone.

### Risks

- DOCA/DAO command freeze in git.
- Cross-applying Helm/eswitch between vendors.

### Dependencies

- M0 docs. Hardware labs: M1–M3. K8s/OS: M4/M5. OPI: M6.

### Suggested tests

- Vendor-specific subset of FN-01–18; cert levels per
  `vendor-certification.md` “typical ceiling” table.

---

## M8 — Security and threat model

**Goal:** Threat model is the **target to test**, not a guarantee;
management plane off the VF network; no “DPU = secure.”

### Stories

1. `host-dpu-threat-model.md` (assets through residual risk).
2. Implement TM-1 (tenant cannot reach OPI/Redfish).
3. TM-2 / FN-18 / K8S-DPU-10: kubelet stop vs DPU ACL.
4. Record fail-open vs fail-closed on DPU restart (no assumption).
5. `tests/security/` harness (TODO).

### Deliverables

- `docs/threat-model/`, security test stubs, OPI listen policy

### Acceptance criteria

- Threat model published with residual risks (firmware unaudited).
- TM-1 attempted on any lab that exposes OPI/Redfish.
- vDPA isolation not claimed equal to VFIO without TM-7.

### Risks

- Overclaim in README/marketing copy.
- On-path NIC (representors on host) weaker boundary.

### Dependencies

- M2+ for a real datapath; M6 if OPI is deployed.

### Suggested tests

- TM-1…TM-10 as applicable; FN-18. Not a cert level by itself.

---

## M9 — Performance and certification

**Goal:** Repeatable perf JSON + signed vendor certification reports;
matrix updates only from those reports.

### Stories

1. Enforce `performance-methodology.md` (warmup, N≥3, medians, null
   hit ratio).
2. Fill `tests/performance/` scripts (iperf/TRex) — still TODO.
3. `tests/vendor-certification/reports/<vendor>/<id>/REPORT.md`.
4. `ci/hardware-regression` wiring (TODO).
5. Upgrade/downgrade matrix cells with links.

### Deliverables

- Perf result files (`odse-perf-result/v1alpha1`)
- At least one signed report at the highest true level

### Acceptance criteria

- No published “vendor win” with `offload_hit_ratio: null` billed as
  hardware offload.
- Kind results tagged `environment: simulation` and not used for
  `supported`.
- Certification README layout exists (template already in
  `vendor-certification.md`).

### Risks

- Hairpin topology overstated as two-host.
- TSO on ⇒ invalid PPS.

### Dependencies

- M2–M3 for meaningful L2–L3 numbers; M4/M5 for L4; M6–M8 for L5.

### Suggested tests

- Perf jobs per scenario_id; FN-17 attached as artifact; full level
  checklists in the report template.

---

## Cross-cutting (not a milestone)

- `ci/lint`, molecule for Ansible, no secrets in git.
- Telemetry collector plugins remain empty until vendor tracks
  implement them (M7).
- Definition of Done (plan §10) maps to FN-01–18 + cert levels, not
  to “docs exist.”
