# VMware vDSE/NSX to Open-Source Equivalent Mapping

Status: Draft
Scope: Concept-by-concept mapping reference, companion to
[`overview.md`](overview.md)
Audience: Contributors, vendor integration engineers, anyone evaluating
"what in ODSE corresponds to X in VMware's stack"

> **Framing note.** This table maps *concepts* to *architecturally
> analogous* open-source constructs used by this project. A row in this
> table is **not** a claim that the open-source equivalent implements the
> same code, protocol, API, certification process, or feature completeness
> as the VMware concept it is mapped against. Some rows map cleanly;
> others map only partially, and are marked accordingly in the Maturity
> column. See [Section: Gaps](#gaps-with-no-direct-open-source-equivalent)
> for concepts this project does not currently address at all. This
> document does not claim NSX or vSphere equivalence — see
> [`overview.md`](overview.md#14-non-goals) for the project's non-goals.

## Maturity level definitions

| Level | Meaning |
|---|---|
| **baseline** | Built on mature, widely deployed upstream open-source components; works the same way regardless of DPU/IPU vendor. |
| **experimental** | Architecture and mechanism are defined and partially implemented/tested, but not yet validated end-to-end across environments. |
| **vendor-specific** | The mechanism exists, but its capability, performance, or availability depends materially on which of the four vendor backends (AMD Pensando, NVIDIA BlueField, Intel IPU, Marvell OCTEON) is in use — see `docs/vendor-readiness/`. |
| **future** | Identified as a needed capability but not yet designed or scheduled in the current milestones (see top-level `README.md`). |

---

## Mapping table

| VMware concept | Open-source equivalent | Implementation notes | Maturity level | Validation method |
|---|---|---|---|---|
| **ESXi host** | Linux host running KVM/QEMU + libvirt (the "host node" role, `overview.md` §4) | `ansible/roles/host_base` provisions IOMMU/hugepage/CPU-isolation baseline; workloads are KVM/QEMU VMs presenting an SR-IOV VF (VFIO) or `vhost-vdpa` virtio-net interface, or Kubernetes pods on the same node | baseline | `tests/functional` host baseline checks; `ci/molecule` role tests against `host_base` |
| **ESXi-on-DPU** (DPU running its own hypervisor instance, managed as vSphere inventory) | DPU-resident Linux OS running DPU-side OVS/OVN and OPI-aligned services (the "DPU/IPU node" role, `overview.md` §5) | `ansible/roles/dpu_base` plus a `vendor_*` role bring up the DPU's own OS/kernel; the DPU does **not** run a second hypervisor — it runs infrastructure services (switching, policy, telemetry), not guest VMs | baseline for the DPU-side Linux/OVS bring-up; **no equivalent** for treating the DPU as inventory-managed "just another ESXi host" (see Gaps) | `tests/functional` DPU bring-up/reachability tests; `tools/inventory` DPU discovery checks |
| **vSphere Distributed Switch (vDS)** | Open vSwitch (OVS) + OVN logical switching, with representor ports attaching DPU-side OVS to host-visible VFs | `ansible/roles/ovs_setup` + `ansible/roles/ovn_setup`; logical switch state comes from Neutron/OVN-Kubernetes via OVN northbound DB, compiled to OVS flows by `ovn-controller` | baseline for OVS/OVN itself (mature upstream projects); **vendor-specific** for whether the vDS-equivalent bridge is hardware-offloaded end-to-end, which depends on switchdev/representor maturity per vendor | `tests/offload-validation/test-switchdev-representors.sh`; `tests/offload-validation/test-ovs-offload-basic.md` |
| **NSX Enhanced Datapath** (a.k.a. ENS — DPDK-accelerated userspace fast path used by NSX) | OVS-DPDK, or a vendor-accelerated OVS variant (e.g. OVS-DOCA on BlueField, DAO OVS offload on OCTEON) | Selected as an alternative to the kernel datapath when the vendor backend supports a poll-mode/accelerated fast path; configured as an `ovs_setup` variant per vendor track | **vendor-specific** — DPDK/accelerated-OVS backend availability and performance characteristics differ materially across the four vendor tracks | `tests/performance/` throughput/latency/PPS comparisons between kernel datapath, OVS-DPDK, and vendor-accelerated OVS |
| **NSX Distributed Firewall (DFW)** | OVN ACLs / security groups, compiled to OVS flow-level (and, where supported, hardware conntrack/ACL table) enforcement at the logical port | Neutron security groups and Kubernetes NetworkPolicies both compile down to OVN ACLs; enforcement point is the OVN logical port bound to the workload's VF/representor, independent of host workload state | baseline for L3/L4 stateless/stateful ACL enforcement via OVN (upstream, mature); **experimental/vendor-specific** for hardware-offloaded stateful conntrack enforcement, which depends on vendor conntrack-offload support | `tests/security/` policy-enforcement tests; `tests/offload-validation/` conntrack offload checks |
| **NSX routing** (distributed logical routing, Tier-0/Tier-1 gateways, NAT, ECMP) | OVN logical routers (distributed logical router model), exposed via Neutron routers or OVN-Kubernetes gateway routers | NAT, ECMP, and inter-subnet routing all expressed as OVN logical router configuration; the OVN gateway-chassis model is the rough analog of NSX's centralized Tier-0 gateway role | baseline for OVN logical routing itself (mature upstream); **experimental** for hierarchical, DPU-scale multi-tier routing topologies mirroring T0/T1 separation at fleet scale | `tests/functional` routing/NAT connectivity tests (VM-to-VM, pod-to-pod, pod/VM-to-external) |
| **vCenter** | No single equivalent — split across OpenStack (Nova + Neutron, for VM workloads) and the Kubernetes API server (for pod workloads), unified only at the shared OVN logical layer and the OPI-aligned API layer (`overview.md` §6) | Each orchestrator owns workload lifecycle for its own workload type; there is intentionally no single management process analogous to vCenter | baseline per individual orchestrator (OpenStack and Kubernetes are each independently mature); **no equivalent** for a single unified compute+network+DPU management pane (see Gaps) | Per-orchestrator control-plane integration tests (`openstack/`, `kubernetes/ovn-kubernetes/`) |
| **vSphere Lifecycle Manager (vLCM)** | Ansible roles (`host_base`, `dpu_base`, `vendor_*`) for host/DPU day-0/day-1 lifecycle; `kubernetes/dpu-operator` for Kubernetes-managed DPU lifecycle | vLCM's image-based, cluster-wide, desired-state lifecycle model has no single OSS counterpart here; Ansible provides declarative, idempotent role-based configuration instead of image-based baselining | baseline for Ansible-driven configuration lifecycle; **experimental** for `dpu-operator`-driven declarative lifecycle in Kubernetes; **no equivalent** for image-based golden-image cluster baselining (see Gaps) | `ci/molecule` role idempotency tests; `tests/vendor-certification` firmware/lifecycle dry-run tests |
| **NSX Manager** | No single equivalent — closest architectural analogs are (a) the OVN northbound/southbound databases as the logical network state store, and (b) the OPI-aligned API layer (`ansible/roles/opi_services`, `kubernetes/opi`) as the DPU/IPU management-plane analog (`overview.md` §6, §9) | NSX Manager's combined network-policy-authoring UI, API, and DPU-partner-integration responsibilities are split here between OVN (network policy/state) and the OPI API layer (DPU lifecycle/inventory/telemetry) | baseline for OVN northbound/southbound DB as logical-state store (mature upstream); **experimental** for the OPI-aligned API layer as a DPU management-plane equivalent | Control-plane functional tests against OVN DB state; `tests/vendor-certification/opi-conformance/` (planned) |
| **UPTv2 / VMDirectPath** (VMware's passthrough-networking pair: UPTv2 = migratable uniform passthrough, VMDirectPath = raw non-migratable PCI passthrough) | VMDirectPath → SR-IOV VF passthrough via VFIO (`qemu/vfio`); UPTv2 → vDPA-backed `virtio-net` via `vhost-vdpa` (`qemu/vdpa`) | VFIO passthrough gives near-bare-metal performance but, in most configurations, no live migration; vDPA preserves the standard virtio-net guest ABI so migration and driver portability are structurally possible, mirroring UPTv2's migratable-passthrough goal | baseline for VFIO VF passthrough (mature, well-understood, but explicitly non-migratable); **vendor-specific/experimental** for vDPA as the UPTv2 analog, since hardware vDPA support and live-migration compatibility both vary by vendor firmware/driver maturity | `tests/performance/vf-passthrough/`; `tests/performance/vdpa/`; live-migration functional testing (planned, not yet implemented — see Gaps) |
| **DPU telemetry** (vDSE/NSX DPU-side counters, flow stats, health metrics surfaced to vCenter/NSX Manager) | OPI-aligned Telemetry API + `tools/telemetry-collector`, backed by `ethtool`/`devlink`/OVS datapath counters and vendor-specific counters (e.g. DOCA telemetry, DPDK `rte_flow` stats, DAO counters) | Baseline counters (link state, packet/byte counts, drops) come from standard Linux tooling on the DPU; offload-hit-ratio and accelerator-specific counters require vendor-specific collectors behind the same Telemetry API contract | baseline for standard Linux/OVS counters; **vendor-specific** for detailed hardware-offload and accelerator telemetry | `tests/performance/` telemetry collection checks; `tools/offload-checker` cross-validation against raw counters |
| **DPU lifecycle** (vDSE/NSX DPU provisioning, firmware upgrade, health-driven remediation, drain/evacuate) | `ansible/roles/dpu_base` + `vendor_*` roles for provisioning; `kubernetes/dpu-operator` for cluster-managed lifecycle; OPI-aligned Lifecycle API for firmware/restart/reboot/drain actions | Firmware upgrade mechanics are inherently vendor-specific (each vendor ships its own firmware tooling/format); the Lifecycle API normalizes the *verbs* (upgrade, restart, reboot, drain) but not the underlying firmware artifact or process | baseline for Ansible-driven provisioning; **experimental** for operator-driven Kubernetes lifecycle; **vendor-specific** for firmware upgrade execution itself | `tests/vendor-certification` firmware/lifecycle update dry-run test (per the project's "advanced" Definition of Done tier) |

---

## Gaps: where there is no direct open-source equivalent

The following VMware vDSE/NSX capabilities have **no** current or planned
mapping in this project. They are listed explicitly so the absence is a
documented decision, not a silent omission, consistent with the project's
design principle of explicit per-vendor and per-capability gap tracking
(`overview.md` §13).

1. **A single unified management plane spanning compute, network,
   security, and DPU lifecycle.** vCenter + NSX Manager together present
   one pane of glass across all of these concerns. ODSE deliberately
   splits this across OpenStack, Kubernetes, OVN, and the OPI API layer
   (`overview.md` §6); there is no plan to build a unifying UI/API on top
   of all of them.
2. **DPU-as-hypervisor-inventory model.** ESXi-on-DPU means the DPU runs a
   real ESXi instance that vCenter manages as ordinary host inventory.
   ODSE's DPU node runs infrastructure services, not a second hypervisor,
   and is not represented as "a compute host" to any orchestrator — it is
   a distinct node type with its own management path.
3. **Identity- and context-aware (L7) firewalling.** NSX DFW supports
   context/identity-based rules (e.g. Active Directory group membership,
   application-identity signatures). OVN ACLs operate at L3/L4; there is
   no L7/identity policy engine in scope for this project.
4. **Built-in encryption and inline security services** (NSX IPsec VPN,
   TLS inspection, IDS/IPS as an integrated NSX service). No component in
   the current architecture provides these; they are not mapped and not
   currently planned.
5. **Cross-site/cross-cluster network policy federation** (NSX
   Federation). OVN's multi-chassis model is single logical-network-domain
   oriented; there is no federation-equivalent spanning independently
   managed ODSE deployments.
6. **DRS/HA-style DPU-capability-aware workload rebalancing.** vSphere
   DRS/HA can proactively rebalance or restart workloads with awareness of
   DPU-offload state. Nova/Kubernetes scheduler placement here is
   capability-aware at initial placement time only (see
   `overview.md` §6); there is no adaptive rebalancing engine.
7. **Storage offload to DPU** (e.g. vSAN-style or NVMe-oF storage
   acceleration on the DPU). Explicitly out of scope for the initial
   implementation (see `overview.md` §14 Non-goals); at most, future
   discovery hooks may be added.
8. **A vendor-certified hardware compatibility guide equivalent to the
   VMware Compatibility Guide (VCG).** `docs/vendor-readiness/` is this
   project's closest analog, but it is community/project-maintained
   capability tracking, not a vendor-certified compatibility program.
9. **Validated, cross-vendor live migration for hardware-accelerated VM
   networking.** UPTv2 ships as a certified, migratable passthrough
   solution. The vDPA-based analog in this project is architecturally
   capable of supporting migration but is not yet validated end-to-end
   across vendor backends — tracked as future work, not claimed as
   working today.
10. **Image-based, cluster-wide desired-state lifecycle baselining**
    (vLCM's image model). Ansible/operator-driven lifecycle here is
    role/reconciler-based, not image-baseline-based; no equivalent to
    vLCM's image compliance/remediation model is planned.

---

## References

- [`overview.md`](overview.md) — full architecture overview this mapping
  table supplements.
- Top-level [`README.md`](../../README.md) — project scope and the
  higher-level vDSE conceptual mapping table (§3).
- [`open-source-vdse-equivalent-plan.md`](../../open-source-vdse-equivalent-plan.md)
  — phased implementation plan referenced for maturity/validation status.
- `docs/vendor-readiness/` — living, per-vendor capability status
  underlying every "vendor-specific" maturity rating above.
- `docs/threat-model/` — trust-boundary analysis relevant to the DFW and
  DPU-lifecycle rows.
