# Architecture Overview

Status: Draft
Scope: Whole-project architecture reference
Audience: Contributors, vendor integration engineers, operators evaluating ODSE

> **Framing note.** This document describes an **open-source equivalent of
> the VMware vSphere Distributed Services Engine (vDSE) *use case*** —
> offloading infrastructure networking/services from the general-purpose
> host CPU onto a DPU/IPU, under a vendor-neutral control plane. It is
> **not** a clone of vDSE, vSphere, or NSX, and it does not claim feature,
> API, or behavioral equivalence with any VMware product. Where this
> document says "equivalent," it means *architecturally analogous*, not
> *identical* or *compatible*. See [Non-goals](#14-non-goals) for an
> explicit list of what this project does not attempt.

---

## Table of contents

1. [Problem statement](#1-problem-statement)
2. [VMware vDSE conceptual model](#2-vmware-vdse-conceptual-model)
3. [Open-source equivalent model](#3-open-source-equivalent-model)
4. [Host node role](#4-host-node-role)
5. [DPU/IPU node role](#5-dpuipu-node-role)
6. [Control plane role](#6-control-plane-role)
7. [Common datapath layer](#7-common-datapath-layer)
8. [Open orchestration layer](#8-open-orchestration-layer)
9. [Vendor backend layer](#9-vendor-backend-layer)
10. [VM workload flow](#10-vm-workload-flow)
11. [Kubernetes workload flow](#11-kubernetes-workload-flow)
12. [Network function offload flow](#12-network-function-offload-flow)
13. [Design principles](#13-design-principles)
14. [Non-goals](#14-non-goals)

---

## 1. Problem statement

Modern multi-tenant infrastructure increasingly runs its switching,
routing, security policy (ACLs/security groups/firewalling), and
observability functions on a DPU (Data Processing Unit) or IPU
(Infrastructure Processing Unit) rather than on the general-purpose host
CPU. The motivations are well established:

- **Host CPU reclamation.** Every cycle spent on vSwitch datapath
  processing, tunnel encap/decap, connection tracking, or policy
  enforcement on the x86/ARM host is a cycle not sold to a tenant workload.
- **Blast-radius reduction.** If infrastructure networking and security
  policy execute on hardware outside the host's trust domain, a compromised
  host workload (or even a compromised host OS/hypervisor) cannot trivially
  read or rewrite the network policy that constrains it.
- **Consistent operations across heterogeneous hardware.** Operators want
  one provisioning, lifecycle, and observability model across NICs/DPUs
  from different silicon vendors, rather than a bespoke integration per
  vendor per platform.
- **Line-rate acceleration.** SR-IOV, switchdev-based hardware offload, and
  vDPA let networking scale with hardware fast-path capacity instead of
  host core count.

VMware's vSphere Distributed Services Engine (vDSE) is the most visible
commercial packaging of this idea: it moves NSX-based networking/security
services onto supported DPUs, orchestrated by vSphere/vCenter/NSX Manager.
It is a strong proof that the architecture pattern works operationally —
but it is a closed, single-vendor-stack, license-gated product tied to
ESXi, vCenter, and NSX.

**The problem this project addresses:** there is no open, vendor-neutral,
documented, testable reference architecture that reproduces the *same
architectural pattern* — host/DPU role separation, a common control-plane
contract, and pluggable hardware backends — using only open components
(Linux, KVM/QEMU, OVS, OVN, OVN-Kubernetes, OpenStack, SR-IOV, switchdev,
vDPA, and OPI-style APIs). Operators who want DPU offload today without
adopting VMware's stack must integrate each vendor's SDK/tooling from
scratch, with no shared architecture, no shared test methodology, and no
shared vendor-capability baseline. Open Distributed Services Engine (ODSE)
exists to fill that gap.

---

## 2. VMware vDSE conceptual model

vDSE, as a commercial product, composes four proprietary layers. This
section describes the *public conceptual shape* of that model only, as
context for section 3 — it is not a technical specification of VMware's
implementation, and no part of this project depends on, derives from, or
reimplements VMware source code, protocols, or licensed material.

```text
                         +-----------------------------+
                         |     vCenter / NSX Manager    |
                         |  (single management plane)   |
                         +---------------+---------------+
                                         |
                                         v
        +---------------------------------------------------------+
        |                    ESXi Host (x86 CPU)                  |
        |                                                          |
        |   VM workloads --> vSphere virtual networking (vNIC)     |
        |                          |                                |
        |                          v                                |
        |            NSX switching / routing / DFW / policy         |
        |            (control plane on host or offloaded)            |
        +---------------------------+------------------------------+
                                     |
                                     v  (DPU-backed datapath uplink)
                       +-----------------------------+
                       |   Supported DPU / SmartNIC   |
                       |  runs NSX datapath services  |
                       |  (switching, DFW, telemetry) |
                       +---------------+---------------+
                                       |
                                       v
                               Physical network
```

Key characteristics of this model, as relevant to what ODSE reproduces
architecturally:

- **Single-vendor control plane.** vCenter/NSX Manager is the sole source
  of truth for both compute and network/security policy.
- **DPU as an NSX execution target.** The DPU does not expose a
  vendor-neutral API to vCenter; it is programmed through NSX's own
  DPU-integration path, specific to each supported partner DPU.
  ODSE does not attempt to reproduce this specific integration mechanism.
- **Host/DPU separation of concerns.** Compute (ESXi/VM) stays on the host
  CPU; switching, distributed firewalling (DFW), and related network
  services are offloaded to the DPU's own packet-processing engine.
- **DPU partner ecosystem.** VMware certifies specific DPU hardware
  (historically including Pensando, NVIDIA BlueField, and others) to run
  the NSX datapath.

This is the pattern ODSE targets — host/DPU role separation with a
common control layer above pluggable hardware — **not** NSX's distributed
firewall feature set, NSX's specific control protocols, or vSphere's VM
lifecycle model, none of which this project reimplements or claims to
match.

---

## 3. Open-source equivalent model

ODSE reproduces the *architectural pattern* from section 2 — not the
product — using only open components. The control plane is not a single
proprietary manager; it is whichever open orchestrator matches the
workload type (OpenStack for VMs, OVN-Kubernetes for pods), backed by a
common, vendor-neutral DPU/IPU API contract (OPI-aligned) for the pieces
that must be vendor-specific.

```text
                 VM (KVM/QEMU)                 Pod (Kubernetes)
                       |                              |
                       v                              v
        +-------------------------+     +--------------------------+
        | OpenStack Neutron/Nova  |     |     OVN-Kubernetes        |
        |  (VM network control)  |     |  (pod network control)    |
        +------------+------------+     +-------------+--------------+
                     |                                |
                     +----------------+---------------+
                                      |
                                      v
                     +--------------------------------+
                     |     OVN logical network layer   |
                     |  (logical switches/routers/ACLs) |
                     +----------------+-----------------+
                                      |
                                      v
                     +--------------------------------+
                     |   OVS (host and/or DPU-resident) |
                     |   hardware-offload-aware datapath |
                     +----------------+-----------------+
                                      |
                                      v
            VF / representor / vDPA / virtio-net / SR-IOV binding
                                      |
                                      v
                     +--------------------------------+
                     |     DPU/IPU hardware pipeline     |
                     |  (vendor-specific fast path;       |
                     |   programmed via OPI-style API)    |
                     +----------------+-----------------+
                                      |
                                      v
                               Physical network
```

The "common product" in this model is **not** a single universal
datapath binary that runs identically on every vendor's silicon. It is:

- a common architecture and role model (host / DPU / control plane),
- a common topology and inventory model (what hardware exists, how it is
  wired, what it can do),
- a common lifecycle model (provision, configure, upgrade, decommission),
- a common validation and test methodology (the same functional,
  performance, offload, and security tests run against every vendor), and
- vendor plugins that implement that common model on top of AMD Pensando,
  NVIDIA BlueField, Intel IPU, and Marvell OCTEON hardware respectively.

This mirrors the mapping table already established in the top-level
[`README.md`](../../README.md#3-how-this-maps-to-vmware-vdse-conceptually);
this document expands on the mechanics behind that mapping.

---

## 4. Host node role

The **host node** is the general-purpose compute node: the x86 (or ARM)
server that runs tenant workloads — KVM/QEMU virtual machines and/or
Kubernetes pods. It is deliberately kept as network-and-policy-*thin* as
the deployed offload mode allows.

Responsibilities:

- Run the hypervisor (KVM/QEMU + libvirt) and/or a container runtime plus
  kubelet.
- Own the VM/pod lifecycle: scheduling, boot, storage attach, vNIC/vNIC-VF
  assignment.
- Present workload-facing network interfaces as one of: an SR-IOV VF via
  VFIO passthrough, a `vhost-vdpa`-backed `virtio-net` device, or a
  standard veth/OVS internal port for software-path pods.
- Run only the *host-side* half of the control-plane agents needed to bind
  workloads to the datapath (e.g. Neutron's L2 agent equivalent /
  `ovn-controller` in non-offloaded topologies, kubelet + CNI shim for
  pods, SR-IOV device plugin for VF allocation).
- Enforce host-level baseline hardening (IOMMU enabled, `vfio-pci`/
  `vhost_vdpa` module policy, hugepage/NUMA/CPU-isolation configuration for
  performance-sensitive workloads).

Deliberately **not** the host node's job in an offloaded topology:

- Running the OVS/OVN forwarding logic for offloaded flows (that moves to
  the DPU/IPU — see section 5).
- Terminating tenant-facing security policy enforcement once a flow is
  fully offloaded to the DPU/IPU fast path.
- Holding DPU/IPU management credentials — the host has no privileged path
  into the DPU's management plane (see the trust-boundary discussion in
  section 13 and `docs/threat-model/`).

```text
+------------------------------------------------------------------+
|                             Host Node                            |
|                                                                    |
|  +--------------+   +--------------+   +------------------------+ |
|  | KVM/QEMU VM  |   | KVM/QEMU VM  |   |     Kubernetes pod      | |
|  +------+-------+   +------+-------+   +-----------+------------+ |
|         |                  |                        |              |
|     VF (VFIO)        vhost-vDPA                 veth/VF (CNI)      |
|         |                  |                        |              |
|  +------v------------------v------------------------v-----------+  |
|  |     Host-side control agents (Neutron agent / ovn-controller  |  |
|  |     / kubelet+CNI / SR-IOV device plugin) — binding only,     |  |
|  |     not the offloaded forwarding path                         |  |
|  +----------------------------------+------------------------------+  |
|                                     |                                 |
+-------------------------------------+---------------------------------+
                                      |  PCIe (SR-IOV VF / representor
                                      |  channel to DPU-side eswitch)
                                      v
                              (see DPU/IPU node, section 5)
```

---

## 5. DPU/IPU node role

The **DPU/IPU node** is not a separate physical server in most
deployments — it is the DPU/IPU card plugged into the host, running its
own independent OS, CPU cores, and packet-processing engine (embedded
switch / NPU / DPDK pipeline / vendor accelerator, depending on the
vendor). ODSE treats it as an independent node from an *architectural and
trust* standpoint, even though it shares a chassis with the host.

Responsibilities:

- Run the DPU-resident OS and management stack (`ansible/roles/dpu_base`
  plus a `vendor_*` role).
- Own the embedded switch (eswitch): SR-IOV VF creation, `switchdev` mode,
  and representor port lifecycle for every VF handed to the host.
- Run the DPU-side forwarding engine: OVS with hardware offload
  (kernel TC flower, OVS-DPDK, or a vendor-accelerated OVS variant such as
  OVS-DOCA), and/or `ovn-controller` when the DPU participates directly in
  an OVN chassis topology.
- Enforce network policy (ACLs, security groups, distributed
  firewall-style rules expressed as OVN ACLs) for flows that reach the
  offloaded fast path, independent of host workload state.
- Expose a vendor-neutral, OPI-aligned management API (inventory,
  provisioning, network, telemetry, lifecycle — see section 9) that the
  control plane and vendor backend use instead of talking to host-visible
  interfaces.
- Optionally host lightweight network functions (NAT, simple firewall/ACL,
  proxy, telemetry export) as DPU-resident services — see section 12.

```text
+------------------------------------------------------------------+
|                          DPU/IPU Node                             |
|                    (independent OS, CPU, NPU)                     |
|                                                                    |
|  +--------------------------------------------------------------+ |
|  |               OPI-aligned management API service               | |
|  |   inventory | provisioning | network | telemetry | lifecycle  | |
|  +---------------------------+------------------------------------+ |
|                              |                                     |
|                              v                                     |
|  +--------------------------------------------------------------+ |
|  |         Vendor backend adapter (Pensando / BlueField /         | |
|  |         Intel IPU / OCTEON — implements the API above)         | |
|  +---------------------------+------------------------------------+ |
|                              |                                     |
|                              v                                     |
|  +--------------------------------------------------------------+ |
|  |     DPU-side OVS (hw offload / OVS-DPDK / vendor-accelerated)  | |
|  |     + optional ovn-controller (OVN chassis participation)      | |
|  +----+----------------+----------------+----------------+--------+ |
|       |                |                |                |          |
|   representor       representor     representor       uplink        |
|   (VF1's port)      (VF2's port)    (VFn's port)      port(s)       |
|       ^                ^                ^                |          |
|       |  PCIe channel to host VFs       |                v          |
+-------+----------------+----------------+------------- Physical ----+
                                                            network
```

Representor ports are the architectural hinge of this model: each
host-visible SR-IOV VF has a corresponding representor netdevice on the
DPU side. Attaching that representor to the DPU-side OVS bridge is what
lets OVN/OVS policy apply to — and hardware-offload — traffic that the VM
or pod believes it is sending directly out a NIC.

---

## 6. Control plane role

There is intentionally **no single control-plane process** analogous to
vCenter/NSX Manager in this model. Instead, ODSE defines a **control-plane
role** that is filled by whichever open orchestrator matches the workload
type, all speaking into the same logical network abstraction (OVN) and the
same DPU/IPU API contract (OPI-aligned):

```text
+---------------------------+      +---------------------------+
|   OpenStack control plane |      |   Kubernetes control plane |
|   (Neutron + Nova)        |      |   (OVN-Kubernetes control  |
|   - VM port binding       |      |    plane components)       |
|   - VF/representor mapping|      |   - Pod port binding       |
+-------------+---------------+      +-------------+---------------+
              |                                    |
              +------------------+-----------------+
                                 |
                                 v
                  +--------------------------------+
                  |   OVN northbound / southbound    |
                  |   databases (logical network      |
                  |   source of truth)                 |
                  +----------------+-------------------+
                                   |
                                   v
                  +--------------------------------+
                  |  ovn-controller instances (host   |
                  |  and/or DPU chassis) — apply       |
                  |  logical state to local OVS        |
                  +----------------+-------------------+
                                   |
                                   v
                  +--------------------------------+
                  |  OPI-aligned DPU/IPU API layer    |
                  |  (inventory/provisioning/lifecycle |
                  |   — the parts OVN doesn't cover)   |
                  +--------------------------------+
```

Responsibilities of the control-plane role:

- **Workload-to-port binding.** Map a VM's vNIC or a pod's interface to a
  concrete VF, representor, or vDPA device, and record that mapping so it
  survives migration/rescheduling (`docs/architecture/port-binding-metadata.md`
  — planned; see the project plan).
- **Logical network state.** Express tenant intent (switches, routers,
  ACLs/security groups, NAT rules) as OVN logical network objects,
  independent of which vendor's hardware ultimately executes them.
- **DPU/IPU lifecycle coordination.** Trigger provisioning, firmware
  policy, and health/telemetry collection via the OPI-aligned API,
  independent of the OVN control path — this is the piece with no
  Neutron/OVN-Kubernetes native equivalent, and is where
  `kubernetes/dpu-operator` and `ansible/roles/opi_services` fit.
- **Placement awareness.** Scheduling decisions (Nova scheduler, Kubernetes
  scheduler) must be aware of which hosts have which DPU/IPU capability
  attached, since offload capability is not uniform across the fleet.

What the control-plane role explicitly does **not** do: it does not
mediate every packet (that would defeat the purpose of offload), and it
is not a single process — OpenStack's control plane and Kubernetes'
control plane can coexist on the same physical fleet, each owning a
disjoint set of hosts, without needing to merge into one management
system.

---

## 7. Common datapath layer

The datapath layer is the shared "vocabulary" every vendor backend must
speak, regardless of which silicon executes the actual forwarding. This is
Layer 1 in the phased implementation plan
(`open-source-vdse-equivalent-plan.md`, Section 5) and is intentionally the
first thing validated, before any orchestration is layered on top.

```text
   Workload-visible interface
   (virtio-net via vDPA, or VF via VFIO/SR-IOV, or veth for sw-path)
                |
                v
   +----------------------------------------------------------+
   |  Kernel primitives: SR-IOV, switchdev eswitch mode,        |
   |  representor netdevices, tc flower classifier/offload      |
   +----------------------------------------------------------+
                |
                v
   +----------------------------------------------------------+
   |  Open vSwitch (kernel datapath, OVS-DPDK, or vendor-        |
   |  accelerated OVS variant) — bridges representors, applies  |
   |  OpenFlow-derived flows, requests hardware offload          |
   +----------------------------------------------------------+
                |
                v
   +----------------------------------------------------------+
   |  OVN (logical flow compiler) — translates logical switch/  |
   |  router/ACL state into the OVS flows above                 |
   +----------------------------------------------------------+
                |
                v
        Vendor hardware fast path (see section 9)
```

Components and their role:

| Component | Role |
|---|---|
| SR-IOV | Partitions one physical NIC/DPU function into many virtual functions (VFs), one per workload interface. |
| switchdev | Kernel eswitch mode that exposes the DPU/NIC's embedded switch as manageable Linux netdevices instead of an opaque firmware black box. |
| Representor ports | One virtual netdevice per VF, appearing on the DPU/host management side, standing in for "the VF's other end" for OVS bridging purposes. |
| tc flower | Kernel classifier/offload mechanism OVS uses to push individual flow rules into hardware via `ndo_setup_tc`. |
| OVS | The programmable software switch bridging representors, uplinks, and tunnel ports; source of truth for the compiled flow table. |
| OVN | Logical network layer compiling tenant-facing abstractions (switches, routers, ACLs, NAT, load balancing) down into OVS flows. |
| vDPA | Presents a standard virtio-net data-plane to the guest while the control plane and hardware backing it can vary by vendor. |
| VFIO | Kernel framework used to hand a VF directly to a KVM/QEMU guest for near-bare-metal passthrough performance. |
| DPDK | User-space poll-mode packet processing, used by OVS-DPDK and several vendor accelerated backends for the software (or hybrid) fast path. |

This layer is deliberately vendor-agnostic in its *interfaces* (every
Linux kernel exposes SR-IOV/switchdev/tc the same way), even though the
*quality and completeness* of a given vendor's implementation of these
interfaces varies — which is precisely why `docs/vendor-readiness/` exists
as a separate, honest tracking mechanism rather than assuming uniform
support.

---

## 8. Open orchestration layer

The orchestration layer maps workload intent onto the common datapath
layer and, where needed, onto the DPU/IPU management API. It corresponds
to Layer 2 in the project plan.

```text
   VM-oriented path                         Pod-oriented path
   +----------------------+                 +----------------------+
   |  OpenStack Nova       |                 |  Kubernetes           |
   |  - schedules VM to a  |                 |  scheduler            |
   |    DPU-capable host   |                 |  - schedules pod to a |
   |  - requests VF/vDPA   |                 |    DPU-capable node   |
   |    port from Neutron  |                 +-----------+-----------+
   +-----------+-----------+                             |
               |                                          v
               v                              +----------------------+
   +----------------------+                   |  OVN-Kubernetes CNI   |
   |  OpenStack Neutron    |                   |  - binds pod iface to |
   |  - ML2/OVN driver     |                   |    VF/representor     |
   |  - SmartNIC port      |                   |  - programs OVN       |
   |    binding profile    |                   |    logical port       |
   +-----------+-----------+                   +-----------+-----------+
               |                                            |
               +--------------------+-----------------------+
                                    |
                                    v
                    +--------------------------------+
                    |     OVN (shared logical layer)    |
                    +----------------+-------------------+
                                     |
                                     v
                    +--------------------------------+
                    |   Ansible (host_base, dpu_base,   |
                    |   sriov_setup, switchdev_setup,    |
                    |   ovs_setup, ovn_setup,             |
                    |   qemu_vdpa_setup, opi_services)   |
                    |   — day-0/day-1 lifecycle             |
                    +--------------------------------+
```

Two orchestrators are used because VM-shaped and pod-shaped workloads have
genuinely different lifecycle and networking models — ODSE does not try to
force one orchestrator to own both, but does require both to converge on
the same OVN logical network layer and the same DPU/IPU API contract, so
that a DPU's offload behavior does not depend on which orchestrator placed
the workload.

Ansible plays the "day-0/day-1" role that vSphere's lifecycle management
plays in vDSE, but scoped to configuration and provisioning rather than
runtime traffic steering: bringing a host or DPU from bare OS to a
known-good, validated state (IOMMU/hugepage tuning, SR-IOV/switchdev
enablement, OVS/OVN install and baseline config, vDPA setup, OPI service
deployment).

---

## 9. Vendor backend layer

The vendor backend layer is where hardware-specific reality meets the
common model. Each of the four initial vendor tracks — AMD Pensando,
NVIDIA BlueField, Intel IPU, and Marvell OCTEON — implements the same
conceptual API surface using entirely different underlying mechanisms.

```text
              +--------------------------------------------+
              |     Common OPI-aligned API contract          |
              |  Inventory | Provisioning | Network |         |
              |  Telemetry | Lifecycle                        |
              +----+-----------+-----------+-----------+------+
                   |           |           |           |
        +----------v--+ +------v------+ +--v----------+ +-----v---------+
        | AMD Pensando | |   NVIDIA    | | Intel IPU   | |   Marvell     |
        |   backend    | |  BlueField  | |   backend   | |   OCTEON      |
        |              | |   backend   | |             | |   backend     |
        | ionic driver | | DOCA / OVS- | | OpenShift/  | | DAO OVS       |
        | DSC mgmt path| | DOCA, DOCA  | | upstream    | | offload app,  |
        | (SR-IOV/     | | Flow, BFB   | | DPU Operator| | DPDK graph,   |
        |  switchdev   | | image,      | | OPI-style   | | rte_flow,     |
        |  exposure:   | | switchdev   | | daemon      | | representors  |
        |  discovery-  | | eswitch     | |             | |               |
        |  in-progress)| |             | |             | |               |
        +--------------+ +-------------+ +-------------+ +---------------+
```

Design consequences of this layering:

- **The API contract is fixed; the implementation is not.** A control
  plane or test suite calling "create VF and attach representor to OVS"
  should not need vendor-specific logic — the vendor backend absorbs that
  difference.
- **Capability discovery is a first-class API, not an afterthought.**
  Because not every vendor exposes switchdev/representors/vDPA identically
  (or, in some cases, at all, at a given firmware/driver revision), the
  Inventory API's job is to report *actual* capability, and downstream
  automation must branch on it rather than assume uniformity.
- **A vendor backend can be "behind" without blocking the others.** The
  project plan explicitly sequences NVIDIA BlueField first (most mature
  public OVS-DOCA/OVN-Kubernetes DPU material), Intel IPU second (OpenShift
  DPU Operator / OPI story), Marvell OCTEON third (DAO OVS offload), and
  AMD Pensando as a parallel discovery track (representor/switchdev
  exposure not yet confirmed at the time of writing) — see
  `docs/vendor-readiness/` for current, living status per vendor.
- **OPI is the target alignment, not a hard prerequisite.** Where a
  vendor's current tooling does not yet speak an OPI-style API directly,
  the backend may initially wrap vendor-native tooling (CLI, SDK, Redfish)
  behind the common contract, with OPI-native conformance tracked as an
  upgrade path (see `tests/vendor-certification/opi-conformance/` in the
  project plan).

---

## 10. VM workload flow

This section traces a VM-oriented workload end-to-end, covering both
supported host-side interface types.

### 10.1 VF passthrough (VFIO) path

```text
1. Nova schedules VM to a DPU-capable compute host, requests a VF port
   from Neutron (SmartNIC/switchdev port-binding profile).
2. sriov_setup-provisioned host creates/allocates an SR-IOV VF.
3. libvirt attaches the VF to the VM via VFIO (qemu/vfio, qemu/libvirt-xml).
4. On the DPU side, the VF's representor already exists (switchdev_setup)
   and is attached to the DPU-resident OVS bridge (ovs_setup).
5. Neutron/OVN programs an OVN logical switch port bound to that
   representor; ovn-controller on the DPU chassis compiles it to OVS flows.
6. First packets from the VM hit the OVS/OVN slow path for classification.
7. Once a flow is established, OVS requests hardware offload via tc
   flower; subsequent packets for that flow bypass software entirely.
8. Traffic exits via the DPU uplink to the physical network.
```

```text
 VM ---VF(VFIO)--> [host PCIe] --VF channel--> representor --> DPU OVS/OVN
                                                                     |
                                                          (flow offloaded to
                                                           DPU hw fast path
                                                           after first packets)
                                                                     |
                                                                     v
                                                            Physical network
```

### 10.2 vDPA path

```text
1. Nova/Neutron requests a vDPA-backed port instead of a raw VF.
2. qemu_vdpa_setup binds the chosen VF to a vDPA management device.
3. QEMU exposes a standard virtio-net device to the guest, backed by
   vhost-vDPA (qemu/vdpa, qemu/libvirt-xml/vhost-vdpa.xml).
4. The guest driver is unaware of the underlying vendor hardware — it only
   ever sees virtio-net.
5. Steps 4-8 from the VFIO path above apply identically on the DPU side:
   representor attach, OVN logical port binding, slow-path classification,
   then hardware offload for established flows.
```

**Why both paths exist:** VF/VFIO gives closer-to-bare-metal performance
and lets the guest driver see NIC-specific features, at the cost of losing
live-migration support in most configurations. vDPA preserves the
standard virtio-net guest ABI (and therefore live migration and driver
portability) while still allowing the host/DPU side to swap in a
hardware-accelerated backend. `docs/architecture/vfio-vs-vdpa.md` (planned)
is the place to record the detailed trade-off analysis.

---

## 11. Kubernetes workload flow

Kubernetes-oriented workloads follow OVN-Kubernetes' DPU-mode pattern:
splitting the CNI's own control components across a *host-side* cluster
(where pods run) and a *DPU-side* cluster (where the network control
agent for those pods actually runs), connected by the same VF/representor
mechanism used in the VM flow.

```text
   Host Kubernetes cluster (workload plane)      DPU-side components
   +------------------------------------+        +--------------------------+
   |  kube-scheduler places pod on a    |        |                          |
   |  DPU-capable node                  |        |                          |
   |               |                    |        |                          |
   |               v                    |        |                          |
   |  kubelet + CNI (ovnkube-node,      |        |                          |
   |  host side) requests VF from       |        |                          |
   |  SR-IOV device plugin              |        |                          |
   |               |                    |        |                          |
   |               v                    |        |                          |
   |  Pod's eth0 = VF (or VF-derived     |        |                          |
   |  interface); pod is otherwise       |        |                          |
   |  unaware of DPU offload             |        |                          |
   +---------------+----------------------+        |                          |
                   |  PCIe VF channel                |                          |
                   v                                 |                          |
       (representor appears on DPU) --------------> ovnkube-node (DPU side)     |
                                                     binds OVN logical port to   |
                                                     the representor              |
                                                     |                            |
                                                     v                            |
                                              DPU-resident OVS/OVN datapath      |
                                              (same offload mechanics as the      |
                                               VM flow: slow path -> hw offload)  |
                                                     |                            |
                                                     v                            |
                                               Physical network                  |
                                                     ^                            |
                                                     |                            |
        OVN northbound/southbound DB (shared control-plane state) <--------------+
```

Two deployment modes are supported, matching the project plan's
phasing:

1. **Simulated DPU mode (CI/dev).** `ci/kind-simulated-dpu` runs the full
   host-side and DPU-side `ovnkube` component split inside a KinD cluster
   using kernel `netdevsim`/mock switchdev devices, validating the control
   flow and manifests with no physical hardware dependency.
2. **Hardware DPU mode.** The same manifests target real DPU-capable
   nodes, with `kubernetes/dpu-operator` responsible for discovering DPU
   capability, labeling nodes accordingly, and driving the DPU-side
   lifecycle that Ansible would drive in a non-Kubernetes-managed
   environment.

The pod itself never becomes aware that its `eth0` is backed by an
offloaded VF rather than a plain veth — from the workload's perspective,
Kubernetes pod networking behaves identically; the offload is entirely a
property of the node and its DPU, not of the pod spec (beyond the
resource request that triggers VF allocation).

---

## 12. Network function offload flow

Beyond VM/pod connectivity, the DPU/IPU can host small, focused
infrastructure network functions directly — the closest analog to NSX's
distributed services (firewall, NAT, load balancing, telemetry) running on
DPU hardware, but here implemented as ordinary DPU-resident
services/processes rather than a proprietary NSX datapath module.

```text
                        Host workload traffic
                                |
                                v
              +----------------------------------+
              |   DPU/IPU embedded switch/eswitch  |
              |   (representor ports, as before)    |
              +----------------+-------------------+
                               |
                               v
              +----------------------------------+
              |   DPU-resident network function    |
              |   (e.g. stateful ACL/NAT, simple    |
              |    L4 proxy, packet filter,          |
              |    telemetry exporter)               |
              |   managed via the OPI-aligned         |
              |   Network + Lifecycle APIs            |
              +----------------+-------------------+
                               |
                               v
                        External network
```

Flow, end to end:

```text
1. Operator (via OPI-aligned API, driven by ansible/roles/opi_services or
   kubernetes/opi manifests) requests deployment of a network function
   onto a specific DPU.
2. The vendor backend translates that request into vendor-specific
   placement: a container/process on the DPU's Arm cores, a DPDK
   application instance, or an equivalent construct, depending on vendor.
3. OVS/OVN on the DPU is configured to steer the relevant traffic
   (by logical port, ACL match, or explicit service-function chaining)
   through the network function before it reaches the uplink.
4. The network function enforces its policy (allow/deny, NAT, rate limit,
   export flow records) independent of host workload state — a compromised
   or unresponsive host workload cannot disable or bypass it.
5. Telemetry API on the DPU surfaces counters/flow records from the
   function to tools/telemetry-collector for observability.
```

The initial scope here (per the project plan) is deliberately narrow:
simple, well-understood functions (proxy, ACL, NAT, telemetry, packet
filter) used to validate the *placement and lifecycle mechanism*, not an
attempt to build a general-purpose service-function-chaining platform or
a DFW-equivalent policy engine in the first iteration.

---

## 13. Design principles

These principles govern every architectural decision in this project and
are drawn directly from the project's key design rules
(`open-source-vdse-equivalent-plan.md`, Section 11):

1. **Vendor logic stays behind a plugin boundary.** No orchestration or
   test code should need vendor-specific branches; if it does, that logic
   belongs in a `vendor_*` role or backend adapter instead.
2. **No assumption of uniform capability.** switchdev, representors, and
   vDPA support are *not* assumed present or identical across vendors —
   every capability is discovered and recorded, never assumed.
3. **Do not claim NSX or vSphere equivalence anywhere.** Documentation,
   test names, and API naming avoid implying feature parity with any
   VMware product; where a concept is analogous, it is described as
   analogous, not as equivalent or compatible.
4. **OPI is the long-term alignment target, not a launch prerequisite.**
   A vendor backend may temporarily wrap native vendor tooling behind the
   common contract before it can speak an OPI-native API.
5. **Measurable datapath offload before orchestration complexity.**
   A vendor track must demonstrate real hardware offload (flow-level, with
   counters) before Kubernetes/OpenStack integration work is prioritized
   for that vendor.
6. **Inventory and validation come before automation.** You cannot safely
   automate configuration of hardware whose capabilities you have not
   first discovered and tested.
7. **Explicit, per-vendor gap documentation.** `docs/vendor-readiness/`
   exists specifically so that "vendor X does not yet support Y" is a
   documented, tracked fact, not a silent limitation discovered in
   production.
8. **KVM/QEMU and Kubernetes tracks stay separate but reusable.** Both
   converge on the same OVN logical layer and the same common datapath
   layer (section 7), but neither orchestrator is forced to depend on the
   other.
9. **Tests are the cross-vendor contract.** The definition of "a vendor
   backend works" is entirely operational: it is defined by which tests in
   `tests/` it passes (see the Definition of Done in the project plan),
   not by a claim in a slide deck.
10. **Host/DPU trust boundary is architectural, not incidental.** The host
    has no privileged path to DPU management state; this is treated as a
    security property to be tested (`tests/security/`,
    `docs/threat-model/`), not merely a deployment convention.

---

## 14. Non-goals

To keep scope honest and avoid overstating what this project delivers,
the following are explicitly **not** goals of ODSE:

- **Not a clone of VMware vSphere.** ODSE does not reimplement ESXi, the
  vSphere Client, vMotion, DRS, HA, or any other vSphere platform feature.
- **Not a clone of VMware NSX.** ODSE does not reimplement NSX Manager,
  the NSX distributed firewall as a specific product feature, NSX's
  micro-segmentation UI/policy model, or any NSX wire protocol. OVN
  ACLs/logical routers are architecturally analogous to some NSX
  *concepts*, not compatible with or derived from NSX itself.
- **Not a claim of feature or performance parity with vDSE.** Whether a
  given open-source combination on a given vendor's hardware matches
  vDSE's throughput, latency, or feature completeness is an open,
  per-vendor empirical question — tracked in `docs/vendor-readiness/` and
  `tests/performance/`, never assumed.
- **Not a general-purpose, production-grade multi-tenant cloud platform.**
  This project produces a reference architecture, automation, and test
  harness — it does not itself productize billing, quota enforcement,
  self-service tenant portals, or SLA-backed support.
- **Not a universal hardware abstraction/compiler (e.g. a P4-style
  cross-vendor pipeline compiler).** The vendor backend layer (section 9)
  deliberately allows heterogeneous implementations rather than compiling
  one program to every target.
- **Not guaranteed feature parity across the four initial vendors.**
  AMD Pensando, NVIDIA BlueField, Intel IPU, and Marvell OCTEON are
  expected to reach readiness on different timelines with different
  capability sets; parity is an aspiration for the ecosystem to close over
  time, not a starting assumption or an initial milestone requirement.
- **Not a storage offload platform (initial scope).** NVMe-oF or other
  storage-acceleration use cases are out of scope for the initial
  implementation; at most, discovery hooks may be added for future work.
- **Not bare-metal provisioning/PXE tooling.** ODSE automation assumes an
  already-installed base OS on both host and DPU; initial OS imaging is a
  prerequisite handled by existing tooling outside this project's scope.
- **Not a commercially supported product.** There are no SLAs; this is a
  reference implementation and test harness for the community and vendor
  partners to build on.

---

## References

- Top-level [`README.md`](../../README.md) — project scope, vendor
  tracks, and milestones.
- [`open-source-vdse-equivalent-plan.md`](../../open-source-vdse-equivalent-plan.md)
  — detailed phased implementation plan, vendor workstreams, epics, and
  definition of done referenced throughout this document.
- `docs/vendor-readiness/` — living, per-vendor capability status.
- `docs/threat-model/` — host/DPU trust-boundary analysis referenced in
  section 13.
- `docs/test-plan/` — test strategy referenced in sections 10-12.
