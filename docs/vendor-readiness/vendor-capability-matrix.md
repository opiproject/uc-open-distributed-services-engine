# Vendor Capability Matrix

Status: Draft — initial capability assessment, largely based on public
vendor documentation rather than this project's own testing
Scope: Cross-vendor capability tracking for AMD Pensando, NVIDIA
BlueField, Intel IPU, and Marvell OCTEON
Audience: Contributors, vendor integration engineers, anyone deciding
which vendor track to work on next
Companion documents: [`../architecture/overview.md`](../architecture/overview.md) §9,
[`../architecture/api-model.md`](../architecture/api-model.md)

> **Framing note.** This matrix records the *current state of knowledge*
> about each vendor's capability, not a certification and not a promise of
> future support. Per the project's design principles
> (`docs/architecture/overview.md` §13), this project does not assume
> uniform capability across vendors and documents gaps explicitly rather
> than silently. Where public information is insufficient, ambiguous, or
> untested, the rating is **discovery-required** — this is the default,
> not an exception. Ratings will change as each vendor track produces
> real test results (see [How this matrix is maintained](#how-this-matrix-is-maintained)).

---

## State legend

| State | Meaning | Bar required to use it |
|---|---|---|
| `supported` | The capability is available and there is strong, largely independent (not solely vendor-marketing) evidence it works as described — e.g. mainline Linux kernel support, multiple independent public write-ups, long-standing production use. | High. Reserved for capabilities with broad, multi-source confirmation. Does **not** by itself mean this project has independently re-validated it — see the per-row notes and `tests/vendor-certification/` for actual validation status. |
| `likely` | A vendor has published documentation or a reference architecture describing this capability, but it has not yet been independently validated by this project, or is documented by fewer/less independent sources than a `supported` rating would require. | Moderate. Used when there is a credible, named, vendor-published path — not a marketing claim without technical detail. |
| `discovery-required` | Public information is insufficient, ambiguous, firmware/model-dependent, or simply untested. This is the **default rating** whenever there is real uncertainty. | Default — no evidence bar; this is the "we don't actually know yet" state. |
| `not-applicable` | The capability category does not apply to this vendor's product form factor or architecture. | High — requires a specific, confirmed architectural reason. Not currently used in this matrix; see note below. |
| `blocked` | A specific, identified blocker (licensing, missing driver, firmware limitation, explicit vendor statement) currently prevents this capability. | High — requires a specific, citable blocker. Not currently used in this matrix; see note below. |
| `future` | Acknowledged as a roadmap item (by vendor material, community discussion, or this project's own plan) but not available today in any form, and not part of the vendor's currently published near-term workstream. | Moderate — used for capabilities explicitly out of near-term scope rather than merely unconfirmed. |

**Why `not-applicable` and `blocked` are not used below:** as of this
writing, no capability/vendor combination has been confirmed with enough
certainty to justify either state without risking an overclaim (a
`not-applicable` or `blocked` rating is itself a confident claim, not an
absence of one). Both remain available for future revisions once a
specific architectural reason or citable blocker is confirmed per vendor.

---

## Matrix

| Capability | AMD Pensando | NVIDIA BlueField | Intel IPU | Marvell OCTEON |
|---|---|---|---|---|
| DPU OS access | discovery-required | supported | likely | likely |
| Management API | discovery-required | likely | likely | discovery-required |
| Redfish support | discovery-required | discovery-required | likely | discovery-required |
| SR-IOV VF creation | discovery-required | supported | likely | likely |
| switchdev support | discovery-required | supported | discovery-required | discovery-required |
| representor support | discovery-required | supported | discovery-required | likely |
| OVS hardware offload | discovery-required | supported | discovery-required | likely |
| vendor OVS backend | discovery-required | likely | discovery-required | likely |
| DPDK/rte_flow path | discovery-required | supported | discovery-required | likely |
| vDPA support | discovery-required | likely | discovery-required | discovery-required |
| VFIO passthrough support | discovery-required | supported | discovery-required | discovery-required |
| OVN integration | discovery-required | likely | discovery-required | discovery-required |
| OVN-Kubernetes DPU mode | discovery-required | likely | likely | future |
| OpenStack off-path DPU mode | discovery-required | likely | future | future |
| OPI alignment | discovery-required | likely | likely | discovery-required |
| P4Runtime/IPDK pipeline path | discovery-required | not applicable | discovery-required (mock client implemented) | discovery-required |
| Telemetry support | discovery-required | supported | discovery-required | likely |
| Firmware lifecycle | discovery-required | likely | discovery-required | discovery-required |
| Security isolation | discovery-required | discovery-required | discovery-required | discovery-required |

Note the last row: **security isolation is `discovery-required` for every
vendor, with no exceptions.** The host/DPU trust-boundary assumptions in
`docs/architecture/roles-and-trust-boundary.md` §5 — particularly whether
any vendor's PCIe VF channel exposes a host-triggerable control primitive
— have not been independently verified for any of the four vendors.
No vendor gets the benefit of the doubt here regardless of how mature
their other capabilities are.

---

## Notes per vendor

### AMD Pensando

Almost every capability is rated `discovery-required`. This matches the
project's own phased plan, which explicitly treats AMD Pensando as a
**discovery-first track** rather than a track ready for immediate
integration work (`open-source-vdse-equivalent-plan.md` §7.4).

- The `ionic` Ethernet driver for Pensando DSC NICs is mainline in the
  Linux kernel and reasonably well documented — this gives confidence in
  basic host-side NIC functionality, but **not** in DPU-side management
  access, SR-IOV VF exposure, or representor/switchdev availability, none
  of which are confirmed in public material at the level of detail
  available for NVIDIA or Marvell.
- Whether SR-IOV VF creation and a representor/switchdev model are
  exposed at all on Pensando DSC hardware is an open question this
  project has not yet answered — the plan's own workstream (AMD-4, AMD-5)
  frames this explicitly as "verify whether," not "confirm that."
- If representors are not exposed, the plan's fallback (AMD-7) is to
  define an alternative model via vendor APIs or the OPI Network API —
  this is a contingency, not a confirmed working path, and is why OPI
  alignment is also rated `discovery-required` rather than `likely` here.
- **Recommended next step:** execute AMD-1 through AMD-5 from the project
  plan (driver verification, DSC enumeration, management-path
  availability, SR-IOV/representor exposure) before attempting any
  higher-layer integration work for this vendor.

### NVIDIA BlueField

The most mature track by a clear margin, reflected in the highest number
of `supported` ratings in this matrix. This is consistent with the
project's sequencing decision to use NVIDIA BlueField first
(`open-source-vdse-equivalent-plan.md` §8, Track B).

- SR-IOV, switchdev, representor ports, kernel TC-flower OVS hardware
  offload, DPDK/`rte_flow` (via the `mlx5` PMD), and VFIO passthrough are
  all mainline-Linux-kernel or long-standing DPDK capabilities with
  extensive independent documentation (kernel.org, major distribution
  vendors, and numerous conference/community write-ups) — hence
  `supported` for those rows.
- `vendor OVS backend` (OVS-DOCA) is rated `likely`, not `supported`: it
  is NVIDIA-published and technically credible, but is newer and less
  independently validated across the ecosystem than the kernel TC-offload
  path.
- vDPA support (`mlx5_vdpa`) is rated `likely` rather than `supported` —
  the kernel driver exists and is documented, but it is materially less
  production-proven and less widely deployed than the SR-IOV/switchdev
  path at the time of writing.
- OVN integration, OVN-Kubernetes DPU mode, and OpenStack off-path DPU
  mode are all rated `likely`: NVIDIA and/or Red Hat have published
  reference material for each, but none has been independently validated
  by this project, and OVN-Kubernetes DPU mode specifically remains an
  evolving upstream feature rather than a broadly production-hardened one.
- **Recommended next step:** proceed with the plan's NVIDIA workstream
  (`open-source-vdse-equivalent-plan.md` §7.1) starting from switchdev and
  kernel OVS offload validation, since those have the strongest
  underlying evidence, before moving to OVS-DOCA and vDPA validation.

### Intel IPU

The strongest track for **OPI alignment and Kubernetes-native DPU
management** specifically, but with a narrower and shallower public
technical footprint for the lower-level datapath mechanics (switchdev,
representors, DPDK) compared to NVIDIA or Marvell.

- DPU OS access, Redfish support, and Management API are rated `likely`
  on the strength of the published Intel/Red Hat OpenShift DPU Operator
  and IPU platform material, which explicitly describes IMC (management
  complex) access and Redfish reachability
  (`open-source-vdse-equivalent-plan.md` §7.2, Intel-2).
- switchdev, representor, OVS-offload, vendor-OVS-backend, and
  DPDK/`rte_flow` rows are all `discovery-required`: the plan itself
  frames these as needing per-stack validation ("validate per IPU stack"),
  and no equivalently detailed public material exists for Intel IPU as
  exists for NVIDIA's `mlx5` ecosystem or Marvell's DAO documentation.
- OVN-Kubernetes DPU mode and OPI alignment are rated `likely` — this is
  genuinely Intel's flagship published use case (OpenShift DPU Operator +
  OPI-style daemon), the strongest such story among the four vendors —
  but it remains unvalidated by this project and is therefore not rated
  `supported`.
- OpenStack off-path DPU mode is rated `future`: it is not part of
  Intel's current published IPU workstream at all (which is
  Kubernetes/OpenShift-centric), not merely unconfirmed.
- `P4Runtime/IPDK pipeline path` is rated `discovery-required` like
  most other Intel datapath rows, but with a caveat the other rows
  don't have: a mock-mode client, a `cli`-mode client that shells the
  real `p4rt-ctl` utility over SSH (the same mechanism the official
  [`ipdk-io/ipu-playbook`](https://github.com/ipdk-io/ipu-playbook)
  automation uses on real E2100 hardware), and capability-detection
  wiring already exist in this codebase
  (`plugins/intel_ipu/p4runtime.py`), as an **alternate**, independent
  control plane to the OPI/DPU Operator path above — not a fallback of
  it. IPDK's `infrap4d` P4Runtime daemon predates the OpenShift DPU
  Operator historically. Real ACC reachability on a specific lab's
  hardware, the compiled pipeline/`p4info` for that SKU, and a pinned
  gRPC client (for the separate `grpc` mode) are all still unvalidated
  by this project (see [`intel-ipu-p4-ipdk.md`](intel-ipu-p4-ipdk.md)),
  which is why the rating stays `discovery-required` rather than
  `likely`.
- **OVS control path (`intel.control_plane: ovs`)** is a third
  alternate to the P4Runtime/IPDK row above, also rated
  `discovery-required` for the same real-hardware-unvalidated reasons
  — but it is **not** an independent hardware mechanism the way the
  other rows in this table are independent of each other. It is an
  alternate P4Runtime *client* (ordinary `ovs-vsctl`/`ovs-ofctl`
  instead of `p4rt-ctl`) onto the *same* `infrap4d` backend as the
  P4Runtime/IPDK row, per IPDK's own docs describing its patched
  `ovs-vswitchd` as itself a P4Runtime gRPC client. Do not read a
  future "Intel: ovs = likely" as symmetric with NVIDIA's OVS-TC/
  OVS-DOCA row below — see
  [`intel-ipu-ovs-p4.md`](intel-ipu-ovs-p4.md) for the exact
  distinction. `plugins/base/ovs.py`, `plugins/intel_ipu/ovs_p4.py`,
  and `IntelIpuBackend`'s `configure_ovs_bridge()`/
  `_submit_ovs_forwarding()` exist in this codebase (mock/SSH-planned,
  same as the P4Runtime row); real ACC `ovs-vswitchd` reachability is
  unvalidated.
- **Recommended next step:** pursue the plan's Intel workstream
  (`open-source-vdse-equivalent-plan.md` §7.2) starting from OpenShift DPU
  Operator deployment and OPI-daemon validation, which is where the
  strongest public material exists, before attempting lower-level
  datapath validation.

### Marvell OCTEON

A track with solid, named datapath documentation (DAO, DPDK, `rte_flow`)
but very little public material on Kubernetes/OpenStack control-plane
integration.

- SR-IOV VF creation, representor support, OVS hardware offload, the
  vendor OVS backend (DAO OVS offload), DPDK/`rte_flow`, and telemetry are
  all rated `likely` on the strength of Marvell's own published DAO (Data
  Plane Accelerator) documentation, which names each of these capabilities
  explicitly (`open-source-vdse-equivalent-plan.md` §7.3).
- `switchdev support` is rated `discovery-required` even though
  representor support is rated `likely`: Marvell's documented representor
  model operates at the DPDK layer, and it is not confirmed whether this
  is equivalent to, or interoperates with, the kernel `switchdev` eswitch
  model used elsewhere in this project's common datapath layer
  (`docs/architecture/overview.md` §7).
- vDPA support, VFIO passthrough support, OVN integration, Management API,
  and Redfish support are all `discovery-required` — none of these are
  addressed with comparable specificity in currently available Marvell
  DAO material.
- OVN-Kubernetes DPU mode and OpenStack off-path DPU mode are both rated
  `future`: neither appears in Marvell's current published workstream,
  which is scoped to OVS/OVN control-plane integration "where feasible"
  rather than a committed Kubernetes or OpenStack integration path.
  OPI alignment is `discovery-required`, consistent with the plan's own
  "candidate" (i.e., aspirational, unconfirmed) framing for this vendor.
- **Recommended next step:** pursue the plan's Marvell workstream
  (`open-source-vdse-equivalent-plan.md` §7.3) starting from DAO OVS
  offload deployment and `rte_flow` validation, and treat switchdev
  interoperability as an explicit open question to resolve early, since it
  affects whether Marvell can share the same OVS/OVN integration path used
  for the other vendors or needs a materially different one.

---

## How this matrix is maintained

This matrix is a living document, not a one-time assessment:

- Ratings should be revised **only** on the basis of a concrete result:
  a passing/failing test in `tests/vendor-certification/`, a specific
  cited vendor document, or a specific engineering finding — not general
  impressions.
- Each vendor should eventually have its own detailed readiness document
  under `docs/vendor-readiness/` (e.g. `nvidia-bluefield.md`,
  `intel-ipu-e2100.md`, `marvell-octeon.md`, `amd-pensando.md`, per
  `open-source-vdse-equivalent-plan.md` §7) that this matrix summarizes
  and links to once those documents exist.
- A rating upgrade from `discovery-required`/`likely` to `supported`
  should always be traceable to a specific passing test defined in
  `tests/vendor-certification/`, consistent with the project-wide
  principle that **tests are the contract across vendors**
  (`docs/architecture/overview.md` §13).
- A rating should be **downgraded** just as readily as it is upgraded if
  a previously assumed capability turns out not to work as documented —
  this matrix's value depends on it staying honest under revision, not on
  looking complete.

---

## References

- [`../architecture/overview.md`](../architecture/overview.md) §9 (Vendor
  backend layer) and §13 (Design principles).
- [`../architecture/api-model.md`](../architecture/api-model.md) — the
  API surface each vendor backend must implement; the `Management API`
  and `OPI alignment` rows above reflect readiness against this model.
- [`../architecture/roles-and-trust-boundary.md`](../architecture/roles-and-trust-boundary.md)
  §5 — the isolation assumptions underlying the `Security isolation` row.
- [`../../open-source-vdse-equivalent-plan.md`](../../open-source-vdse-equivalent-plan.md)
  §7 (Vendor Workstreams) and §10 (Vendor Certification) — source material
  for the per-vendor notes above.
- `tests/vendor-certification/` — where future rating upgrades must be
  evidenced.
