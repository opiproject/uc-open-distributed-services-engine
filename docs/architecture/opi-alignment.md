# OPI alignment (ODSE)

Status: Draft  
Scope: How Open Distributed Services Engine (ODSE) should relate to the
[Open Programmable Infrastructure](https://opiproject.org/) (OPI)
project — not a claim of OPI Forum certification or NSX compatibility.  
Audience: Contributors, vendor integration engineers  
Companions: [`api-model.md`](api-model.md), [`overview.md`](overview.md)
§9 / §13, [`kubernetes/opi/README.md`](../../kubernetes/opi/README.md),
[`docs/test-plan/vendor-certification.md`](../test-plan/vendor-certification.md)
(Level 5)

OPI alignment is **milestone M6** and **cert Level 5**. It is the
long-term north star for the DPU/IPU management surface. It is **not**
a prerequisite for inventory, OVS-TC, VFIO, or vDPA (M1–M3).

Operators running the VM-to-VM lab: this file is contributor design.
What OPI that lab **actually uses today** (vocabulary and honesty rules,
not a gRPC service) is
[`../operations/user-doc-flow.md`](../operations/user-doc-flow.md).
What published OPI APIs we can adopt, what OPI still lacks for that
use case, and the phased tie-in are
[`opi-tie-in-roadmap.md`](opi-tie-in-roadmap.md).

---

## 1. Why OPI matters

Operators want one lifecycle and inventory language across NVIDIA,
Intel, Marvell, and AMD silicon. Without a shared contract, every
orchestrator (Ansible, OVN-Kubernetes DPU operator, OpenStack
off-path) grows four copies of “list cards, create VFs, attach a
port, collect stats.”

OPI (LFN / OPI Project) exists to define **vendor-neutral APIs** for
DPU/IPU inventory, networking, storage, and lifecycle, typically
exposed as gRPC/protobuf (and related Kubernetes objects) on the
device or a nearby agent. Aligning ODSE with that direction:

- Avoids a private `odse.opi/v1alpha1` that can never talk to a
  vendor’s real OPI daemon (especially **Intel**, which is
  OPI/OpenShift-first).
- Gives tests a stable envelope (`apiVersion`, `kind`, `metadata.id`)
  even when the wire protocol is still JSON over a stub.
- Keeps tenant policy in OVN; OPI is for **device and DPU-resident
  function** control, not a second NSX.

ODSE must not wait for a complete OPI spec to ship useful datapaths.
NVIDIA kernel OVS-TC can certify Level 2–3 with **no** OPI. Intel may
map OPI objects **before** kernel switchdev is proven. Both are valid
as long as capability flags stay honest.

---

## 2. Which parts of this project should eventually map to OPI APIs

These ODSE surfaces should converge on OPI (or a documented mapping
table to a vendor OPI daemon):

| ODSE piece | Why it belongs on OPI |
|---|---|
| Inventory (`DPUDevice`, `HostPF`, VF, representor) | Same facts as `tools/inventory`; OPI Inventory / Device APIs |
| Provisioning (`createVFs`, `setEswitchMode`, bind driver) | Day-1 mutations OPI Lifecycle / Network often cover |
| Network attach (`NetworkAttachment`, representor ↔ bridge) | OPI Network / Interface APIs |
| Offload policy (`OffloadPolicy.hwOffload`) | Capability + desired datapath; reject if unproven (OPI-5) |
| Telemetry (`TelemetrySample`) | OPI Telemetry; omit missing metrics (OPI-6) |
| Lifecycle (`firmwareUpgrade`, reboot, drain) | OPI Lifecycle; flags must error if unsupported (OPI-7) |
| DPU-resident `NetworkFunction` | OPI NF / app lifecycle — **not** OVN ACLs |
| API credentials / listen policy | OPI security; TM-1 / OPI-10 |

Kubernetes CRs under `kubernetes/opi` and Ansible `opi_services` are
the **ODSE-facing** shape. A future gRPC client should present the
same objects; field names may be mapped, semantics must not.

**Do not map to OPI:** OVN northbound as a whole, QEMU/libvirt XML,
Kind cluster scripts, performance CSV schema. Those stay Linux/KVM/OVN
artifacts. OPI may *trigger* attach; it does not replace `virsh`.

---

## 3. Which parts should remain vendor-specific

Stay behind `VendorBackend` / `vendorExtensions` / Ansible vendor
roles. Do **not** promote these into the common schema until at least
three vendors share a comparable concept.

- Firmware image format, BFB, IMC/Redfish payloads, rshim, DAO CLI,
  DOCA Flow handles, `rte_flow` rule IDs.
- Eswitch mode names beyond `legacy` / `switchdev` (treat others as
  unknown).
- Datapath type strings (`netdev`, OVS-DOCA, hardware vswitch).
- Drain fail-open vs fail-closed.
- Metric **names** (`ethtool -S` vs DOCA vs DPDK); only derived
  metrics (e.g. offload hit ratio) may be normalized and must be
  labeled as derived.
- Whether representors are kernel netdevs, DPDK ports, or OPI-only
  attachments (AMD contingency AMD-7).

Vendor Ansible roles (`vendor_nvidia_bluefield`, `vendor_intel_ipu`,
…) remain the **enablement** path. The adapter **wraps** them; it does
not replace mlxconfig or Redfish.

---

## 4. Proposed adapter model

```text
 Orchestrator (Ansible, dpu-operator, OVN-K8s, Nova)
                      |
                      v
         ODSE objects (odse.opi/v1alpha1)
                      |
                      v
              VendorBackend ABC
         /     |        |        \
      AMD   NVIDIA    Intel    Marvell     (+ simulated)
         \     |        |        /
          kernel / CLI / SDK / Redfish / vendor OPI gRPC
```

Rules (already in `kubernetes/opi/adapter/base.py`):

1. **One ABC** (`list_devices`, attach, offload policy, telemetry,
   lifecycle, NF). Stubs raise `UnsupportedOperation` until a track
   implements them.
2. **Map vendor failures** to `BackendError` (`retryable` /
   `unsupported`). Do not leak SDK exceptions to callers.
3. **Intel path:** prefer wrapping a vendor OPI daemon when it exists;
   fill a **mapping table** (vendor proto field → ODSE object). Do not
   assume the daemon already speaks `odse.opi/v1alpha1`.
4. **NVIDIA / Marvell / AMD:** wrap kernel + documented CLI first;
   add `capabilities.opiNative: true` only when a real OPI endpoint
   is used.
5. **`simulated` backend** for Kind (OPI-9). Never call a vendor SDK
   from CI.
6. **`vendorExtensions` additive only.** Stripping them must still
   validate (OPI-3).
7. **Listen:** `opi_services` defaults to loopback + TLS. Non-loopback
   without TLS fails the role. Tenant networks must not reach the
   socket (OPI-10 / TM-1).

Inventory round-trip: `list_devices()` should reuse
`tools/inventory/dpu_inventory.py` facts, not a second discovery
story.

---

## 5. OPI conformance test approach

Home (planned): `tests/vendor-certification/opi-conformance/`. Until
cases land, [`kubernetes/opi/README.md`](../../kubernetes/opi/README.md)
§8 is the contract.

| Layer | What it proves | Hardware? |
|---|---|---|
| Schema / OPI-1, OPI-3 | Envelope and additivity | No (CI) |
| Honesty / OPI-2, OPI-5, OPI-6, OPI-7 | No fake capabilities, zeros, or ignored flags | No for reject paths; yes for “true” flags |
| Attach / OPI-4, OPI-8 | Real representor or error; idempotency | Skip if no representors |
| Simulated / OPI-9 | Kind never hits vendor SDK | Kind only |
| Security / OPI-10 | Tenant cannot scrape OPI | Needs a datapath + mgmt net (TM-1) |
| Cert Level 5 | OPI-1…8, OPI-10 **and** Level 4 (OpenStack or OVN-K8s hw) | Yes for full award |

A **generic client** (no `if nvidia`) must round-trip common fields
for all four vendor stubs plus `simulated`. Vendor extra cases live
in `docs/vendor-readiness/`.

Passing OPI-5 on a stub that always rejects `hwOffload: true` is a
**pass for the flag**, not a pass for offload. Kind cannot award
Level ≥ 1, and cannot award Level 5.

---

## 6. Short-term approach before full OPI availability

Until a vendor ships a stable OPI endpoint (or ODSE runs a gRPC
server):

1. Keep **JSON objects** in `api-model.md` / adapter `models.py` as
   the contract. Wire protocol can be local Python or REST later.
2. Implement **Ansible + kernel** (inventory, SR-IOV, switchdev, OVS,
   checker) without calling OPI. That is M1–M2 and is the NVIDIA
   first track.
3. Ship **adapter stubs** that load via `load_backend('<vendor>')`
   and raise `UnsupportedOperation` — enough for OPI-1/3/9 in CI.
4. Intel: document Redfish/IMC and OpenShift DPU operator; map OPI
   when a daemon is in the lab, not by copying NVIDIA eswitch tasks.
5. AMD: if no representors, do not fake OVS attach; point at OPI
   Network as **contingency** (AMD-7) with an empty mapping table
   marked TODO.
6. **No open gRPC on `0.0.0.0`** in the skeleton.

This is enough to start M6 without blocking M2/M3 labs.

---

## 7. Long-term API abstraction plan

1. **Freeze semantics** of `odse.opi/v1alpha1` (this repo). Additive
   fields only.
2. **Generate or hand-maintain a mapping** from OPI protobuf (when
   versions are cited in vendor docs) → ODSE objects. Version pins
   belong in `docs/vendor-readiness/`, not hardcoded in the ABC.
3. **Prefer vendor-native OPI** when `capabilities.opiNative` is
   true; keep kernel wrap as fallback for NVIDIA OVS-TC labs.
4. **Promote extensions** to common fields when three vendors share
   them; deprecate via `vendorExtensions.deprecated` / `migratesTo`.
5. **Expose one process** (`opi_services`) that speaks gRPC and/or
   CRs; orchestrators stop SSH-ing vendor CLIs for day-2 attach.
6. **Level 5 certification** only with L4 + TM-1 + OPI-1…8, OPI-10.
   NF (FN-15) stays skip until a vendor actually runs an image.

If OPI and ODSE diverge, **tests win**: change the adapter mapping,
not the FN/OPI honesty rules (no fake `offloaded: yes`, no assumed
switchdev).

---

## Alignment table

| Project API category | Potential OPI alignment | Vendor plugin responsibility | Test requirement |
|---|---|---|---|
| Inventory (`DPUDevice`, PF/VF, representor) | OPI Inventory / Device | Discover via kernel, Redfish, or vendor inventory; never invent PCI or `switchdevSupported: true` | OPI-1, OPI-2; FN-01; cross-check `dpu_inventory.py` |
| Provisioning (VFs, eswitch, bind, mgmt net) | OPI Lifecycle / Network mutate | Idempotent apply; reboot vs live is vendor-documented | OPI-8; FN-03, FN-04, FN-06; skip if sysfs/`devlink` absent |
| Network attach (`NetworkAttachment`) | OPI Network / Interface | Bind **real** representor or error; use `vf_to_representor_map` | OPI-4; FN-07 |
| Offload policy / `OffloadFlow` observe | OPI Network + Telemetry (observe, not a flow compiler) | Set knobs via OVS/vendor DP; report `unknown` if no `in_hw` | OPI-5; FN-08, FN-17; Kind must not require `in_hw` |
| Telemetry (`TelemetrySample`) | OPI Telemetry | Map native counters; **omit** missing (no fake zeros) | OPI-6; FN-16 |
| Lifecycle (firmware, reboot, drain) | OPI Lifecycle | Image transport vendor-specific; reject unsupported `dryRun` / rollback flags | OPI-7; vendor-cert L5; drain behaviour recorded in vendor-readiness |
| Network function (DPU-resident) | OPI NF / app API | Runtime (container/DPDK) is vendor-specific; OVN ACLs stay in OVN | FN-15 skip until implemented; then OPI apply/delete |
| Security policy (NF ACLs + API IAM) | OPI IAM / limited NF policy — **not** OVN NB | Authn/z for the adapter; NF engine semantics documented per vendor | OPI-10, TM-1, TM-6; OVN audit consistency when claimed |
| Vendor extensions | Out of OPI common set (or vendor proto) | Opaque payload; must not override common fields | OPI-3; schema lint |
| Simulated backend | No OPI silicon | In-memory / Kind facts only | OPI-9; never vendor SDK in CI |

---

## References

- OPI Project: https://opiproject.org/ (external; pin versions in
  vendor docs when implementing a mapping).
- [`api-model.md`](api-model.md) — ODSE object contract.
- [`kubernetes/opi/README.md`](../../kubernetes/opi/README.md) — adapter
  and OPI-1…10.
- [`docs/operations/milestone-backlog.md`](../operations/milestone-backlog.md)
  — M6.
- [`docs/threat-model/host-dpu-threat-model.md`](../threat-model/host-dpu-threat-model.md)
  — TM-1.
