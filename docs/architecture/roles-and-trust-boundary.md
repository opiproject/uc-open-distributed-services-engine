# Roles and Trust Boundary

Status: Draft
Scope: Role definitions and security trust boundary for Host Node,
DPU/IPU Node, and Control Plane
Audience: Contributors, vendor integration engineers, security reviewers,
operators
Companion documents: [`overview.md`](overview.md),
[`vdse-to-open-equivalent-mapping.md`](vdse-to-open-equivalent-mapping.md)

> **Framing note.** This document describes the *intended* role
> separation and trust boundary for Open Distributed Services Engine
> (ODSE). It states design intent and assumptions, not a verified security
> guarantee. Nothing in this document should be read as a security
> certification, and nothing here claims parity with any commercial
> product's security model. See
> [Assumptions and limitations](#5-assumptions-and-limitations) for an
> explicit list of what is *not* covered or *not yet validated*.

---

## Table of contents

1. [Host Node](#1-host-node)
2. [DPU/IPU Node](#2-dpuipu-node)
3. [Control Plane](#3-control-plane)
4. [Threat boundary](#4-threat-boundary)
5. [Assumptions and limitations](#5-assumptions-and-limitations)

---

## 1. Host Node

The Host Node is the general-purpose compute node running tenant
workloads (KVM/QEMU VMs and/or Kubernetes pods). See
[`overview.md` §4](overview.md#4-host-node-role) for its architectural
role; this section focuses specifically on responsibilities, exposed
interfaces, and security posture.

### Responsibilities

- Run the hypervisor (KVM/QEMU + libvirt) and/or container runtime +
  kubelet for tenant workloads.
- Own workload lifecycle: scheduling, boot, storage attach, and
  vNIC/vNIC-VF assignment.
- Bind a workload's network interface to a VF (VFIO), a `vhost-vdpa`
  virtio-net device, or a software-path veth/OVS internal port.
- Run only the host-side half of control-plane binding agents (SR-IOV
  device plugin, kubelet + CNI shim, Neutron/OVN host-side agent where
  applicable).
- Enforce host-level baseline hardening: IOMMU enabled, restrictive
  `vfio-pci`/`vhost_vdpa` device-node permissions, hugepage/NUMA/CPU
  isolation for performance-sensitive workloads.

### Components that run there

- Linux kernel, KVM, QEMU, libvirt
- kubelet, container runtime, CNI plugin/shim (host-side `ovnkube-node` in
  Kubernetes DPU-mode deployments)
- SR-IOV Kubernetes device plugin (where applicable)
- Host-side monitoring/telemetry agents (node exporter-style tooling
  feeding `tools/telemetry-collector`)
- In non-offloaded/software-path topologies only: a full local OVS/OVN
  agent (`ovn-controller`) — not present in DPU-offloaded topologies,
  where this logic runs on the DPU instead (see section 2)

### Interfaces exposed

- **Toward the control plane:** libvirt API (local, driven by Nova),
  kubelet API (driven by the Kubernetes control plane) — both are
  management-plane interfaces, not data-plane interfaces.
- **Toward the workload:** the assigned VF (via VFIO), the `vhost-vdpa`
  character device, or a veth pair — a single, workload-scoped data-plane
  interface per attached network.
- **Toward the DPU/IPU:** none, by design. The host has no direct API,
  SSH, or management channel into the DPU's OS, OPI API server, or vendor
  management interface. The only path from host to DPU is the PCIe VF
  data-plane channel itself (see section 4 for what that channel does and
  does not permit).
- **Toward operators:** standard host administrative access (SSH,
  configuration management), which is out of scope for this document's
  workload-facing trust boundary but is itself a privileged path that must
  be separately secured.

### Security boundary

- The host kernel/hypervisor boundary isolates tenant workloads from each
  other (VM isolation via KVM; namespace/cgroup isolation for pods). This
  is standard Linux/KVM isolation, not something ODSE adds to.
- The host is treated, for the purposes of the DPU/control-plane trust
  model, as a **potentially compromised domain**: a tenant workload
  escaping VM/pod isolation and gaining host root should still not obtain
  any privileged path to the DPU's management plane, the OVN
  northbound/southbound databases, or other tenants' VFs/representors.
- The host is trusted only to the extent needed to request and consume a
  data-plane interface (VF/vDPA/veth) that the control plane has already
  authorized and bound.

### Failure impact

- A host node failure or reboot affects only the workloads scheduled on
  that host.
- A host node compromise (up to and including host root) is architecturally
  scoped to: (a) that host's own workloads, and (b) whatever network reach
  the already-provisioned data-plane interfaces permit under current OVN
  policy. It should **not** extend to modifying policy for other
  workloads, other hosts, or the DPU's own configuration — see section 4
  for the caveats on this claim.
- A host failure does **not** by design revoke or alter DPU-side policy
  state for that host's former workloads; DPU-side cleanup (e.g. tearing
  down stale representor bindings) is a control-plane-driven reconciliation
  action, not something the host itself is trusted to perform on its own
  behalf.

### Observability requirements

- Host-level resource, workload placement, and interface-binding metrics
  and logs, correlated through `tools/telemetry-collector`.
- Sufficient audit logging of VF/vDPA device assignment to reconstruct
  "which workload had which data-plane interface, when" for incident
  response.
- Host-side observability must **not** surface DPU management credentials,
  DPU-side flow tables, or other tenants' telemetry to host operators or
  workloads — observability scope follows the same boundary as data-plane
  access (section 4).

---

## 2. DPU/IPU Node

The DPU/IPU Node is the DPU or IPU card's own independent OS, CPU cores,
and packet-processing engine. It is architecturally and trust-wise
independent from the host, even though it physically shares a chassis
with it. See [`overview.md` §5](overview.md#5-dpuipu-node-role) for its
architectural role.

### Responsibilities

- Own the embedded switch (eswitch): SR-IOV VF creation, `switchdev` mode,
  and representor port lifecycle for every VF handed to the host.
- Run the DPU-side forwarding engine (OVS with hardware offload, and/or a
  vendor-accelerated variant) and, where applicable, `ovn-controller` for
  direct OVN chassis participation.
- Enforce network policy (ACLs, security groups, NAT) for flows that reach
  the offloaded fast path, independent of host workload state.
- Expose the OPI-aligned management API (inventory, provisioning, network,
  telemetry, lifecycle) to the control plane.
- Optionally host lightweight DPU-resident network functions (NAT, ACL,
  proxy, telemetry export — see [`overview.md` §12](overview.md#12-network-function-offload-flow)).

### Components that run there

- The DPU-resident OS/kernel and vendor firmware/driver stack
  (`ansible/roles/dpu_base` + a `vendor_*` role)
- DPU-side `ovs-vswitchd` (kernel datapath, OVS-DPDK, or a vendor
  accelerated variant) and, where used, `ovn-controller`
- The OPI-aligned API server and its vendor backend adapter
  (`ansible/roles/opi_services`)
- Optional DPU-resident network function processes/containers

### Interfaces exposed

- **Toward the control plane:** the OPI-aligned API (inventory /
  provisioning / network / telemetry / lifecycle), and, where the DPU
  participates as an OVN chassis, the OVN southbound database connection.
  These are reachable only over a dedicated management network segment,
  not the tenant data-plane network.
- **Toward the host:** representor netdevices exist only within the DPU's
  own OS; the corresponding VF is what the host sees, and the only channel
  connecting the two is the PCIe VF data-plane channel — a data path, not
  a management path (see section 4).
- **Toward vendor tooling:** vendor-native management interfaces (Redfish,
  CLI, SSH) where the OPI-aligned API does not yet cover a given
  capability — these should be restricted to the same isolated management
  network as the OPI API, never exposed to tenant or host-workload
  networks.

### Security boundary

- The DPU OS is a distinct trust domain from the host OS. The host has no
  execution context, credentials, or API access into the DPU's OS, OVS/OVN
  state, or OPI API server.
- DPU-side policy state (OVN-compiled flows, ACLs, NAT rules) can only be
  changed through the sanctioned control-plane path (Neutron/Kubernetes →
  OVN → `ovn-controller` on the DPU, or the OPI API), never by a host
  workload or host OS process directly.
- The DPU's management-plane interfaces (OPI API, vendor tooling) must be
  network-reachable only from the control plane's management network —
  not from the tenant data-plane network and not from the host's own
  general-purpose network interfaces.

### Failure impact

- A DPU/IPU failure or reboot affects **every** workload whose data plane
  depends on that DPU — this is a materially larger blast radius per unit
  of hardware than a single VM/pod failure, and should be treated as a
  higher-severity failure domain in operational planning.
- Whether traffic fails open (forwards without policy enforcement) or
  fails closed (drops) during a DPU restart, firmware upgrade, or crash is
  **vendor- and configuration-dependent and not currently uniformly
  defined** across the four vendor tracks — see
  [Assumptions and limitations](#5-assumptions-and-limitations). This is
  called out explicitly because it materially affects both availability
  and security posture and must not be assumed favorably without
  vendor-specific verification.
- A DPU compromise (e.g. via a vulnerability in DPU-side software) is a
  high-impact event: it could affect policy enforcement for every
  workload bound through that DPU. The design goal of isolating policy
  enforcement on the DPU (section 4) reduces the likelihood of a host
  workload *causing* this, but does not eliminate the DPU itself as an
  attack surface.

### Observability requirements

- DPU health (link state, firmware/driver versions, temperature/power
  where exposed), offload hit ratio, flow-table occupancy, and
  policy-violation/drop counters, exported via the OPI Telemetry API to
  `tools/telemetry-collector`.
- DPU-side telemetry must be tamper-resistant from the host's perspective:
  the host must not be able to suppress, falsify, or read DPU-side
  telemetry it is not authorized to see.
- Sufficient logging of DPU-side configuration changes (who/what issued
  them, via which control-plane path) to support audit and incident
  response independent of host-side logs, since a host compromise should
  not be able to erase or alter DPU-side audit trails.

---

## 3. Control Plane

The Control Plane role is filled by whichever open orchestrator matches
the workload type (OpenStack for VMs, OVN-Kubernetes for pods), backed by
OVN as the shared logical network state store and the OPI-aligned API for
DPU/IPU lifecycle. See
[`overview.md` §6](overview.md#6-control-plane-role) for the full
architectural description.

### Responsibilities

- Map workloads to concrete data-plane interfaces (VF, representor, or
  vDPA device) and persist that mapping across the workload's lifecycle.
- Express and maintain tenant network intent (logical switches, routers,
  ACLs/security groups, NAT) as OVN logical network state.
- Coordinate DPU/IPU provisioning, configuration, and lifecycle via the
  OPI-aligned API, independent of the OVN data path itself.
- Maintain scheduling/placement awareness of which hosts have which
  DPU/IPU capability attached.

### Components that run there

- OpenStack Nova and Neutron (VM-oriented control plane)
- Kubernetes API server and OVN-Kubernetes control-plane components
  (pod-oriented control plane)
- OVN northbound and southbound databases (shared logical network state)
- `kubernetes/dpu-operator` (Kubernetes-managed DPU lifecycle)
- Ansible control node driving `ansible/roles/opi_services` and related
  roles for configuration-time lifecycle actions

### Interfaces exposed

- **Toward tenants/operators:** Neutron/Nova APIs, the Kubernetes API
  server — tenant- and operator-facing management interfaces, subject to
  their own native authentication/authorization (Keystone, Kubernetes
  RBAC), which this project relies on rather than replaces.
- **Toward the DPU/IPU fleet:** the OVN southbound database connection
  (consumed by `ovn-controller` instances) and the OPI-aligned API client
  channel — both should be confined to an isolated management network,
  not reachable from tenant workload networks.
- **Internally:** the OVN northbound/southbound database link itself is a
  high-value internal interface and should be treated as part of the
  control plane's own trust boundary, not exposed broadly.

### Security boundary

- The control plane is the **authoritative source of policy** for the
  entire fleet: it is the only path (by design) that can create logical
  network state, bind ports, or drive DPU/IPU lifecycle actions.
- Because of that authority, a control-plane compromise is the
  **highest-impact** compromise in this architecture — worse than any
  single host or DPU compromise — since it can reprogram policy across
  every host and DPU it manages. This is the concentrated blast radius
  that architecturally isolating hosts and DPUs is designed to create
  (fewer, more defensible high-value systems, rather than a broadly
  distributed attack surface).
- The control plane must not be directly reachable from tenant workload
  networks; its APIs are operator/tenant-management-facing, not
  data-plane-facing.

### Failure impact

- A control-plane outage stops new provisioning and policy changes from
  taking effect, but — because OVN separates the northbound/southbound
  database (desired state) from `ovn-controller`'s already-applied local
  flow state — **already-established data-plane forwarding is designed to
  continue** during a control-plane outage in properly deployed
  topologies. This is a property of OVN's architecture, not something
  ODSE adds; it depends on the control plane actually being deployed with
  appropriate redundancy (e.g. a clustered/RAFT northbound-southbound DB),
  which is an operational responsibility, not a given.
- A prolonged or unrecoverable control-plane loss (e.g. total loss of the
  OVN database with no backup/HA) would eventually block all new workload
  placement and policy changes fleet-wide, even if existing traffic
  continues forwarding in the interim.

### Observability requirements

- Control-plane health, including OVN database replication/quorum status
  (where clustered), reconciliation lag between desired and applied
  state, and API availability/error rates.
- A per-tenant, per-change audit log of policy and lifecycle actions
  (who/what requested a network or DPU lifecycle change, and when),
  sufficient to support both operational troubleshooting and security
  incident review.

---

## 4. Threat boundary

```text
   Tenant / workload network              Management network (isolated)
   +-------------------------+             +------------------------------+
   |   Host Node             |             |     Control Plane            |
   |                          |             |  Nova/Neutron, K8s API,      |
   |  VM/pod workload         |             |  OVN NB/SB DB, dpu-operator,  |
   |     |                    |             |  OPI API clients              |
   |     v                    |             +---------------+---------------+
   |  VF / vDPA / veth        |                             |
   |  (data plane only)       |                             |  OPI API,
   |     |                    |                             |  OVN SB DB
   +-----+--------------------+                             |
         |  PCIe VF channel                                 |
         |  (data plane only,                                |
         |   no management path)                             v
         v                                     +------------------------------+
   +-------------------------+                 |     DPU/IPU Node             |
   |   DPU-side eswitch /     |<----------------+  OPI API server, OVS/OVN,    |
   |   representor / OVS/OVN | management path  |  vendor backend, telemetry   |
   +-----------+-------------+                 +------------------------------+
               |
               v
        Physical network
```

### What host workloads can access

- The single data-plane interface assigned to them (a VF via VFIO, a
  `vhost-vdpa` virtio-net device, or a veth/VF for a pod), and only the
  network reachability that current OVN logical network policy permits for
  that specific port.
- Standard guest-visible resources mediated by the hypervisor/kubelet
  (CPU, memory, storage, the assigned network device) — nothing beyond
  what any conventional KVM/QEMU VM or Kubernetes pod could access.

### What host workloads must not access

- The DPU's OPI-aligned management API, vendor-native management
  interfaces (Redfish/CLI/SSH), or any DPU OS execution context.
- The OVN northbound/southbound databases directly (only the control plane
  and `ovn-controller` instances should reach these).
- Other tenants' VFs, representors, or vDPA devices, or another host's
  DPU/IPU node.
- The DPU's own `devlink`/eswitch control surface (VF creation,
  switchdev-mode changes) — this is a management action, not a data-plane
  action, and must remain outside host reach even though the *effects* of
  that surface (a VF being created) are what the host ultimately consumes.
- Host workloads also must not be able to read or tamper with DPU-side
  telemetry, audit logs, or policy state through any side channel exposed
  by a specific vendor's driver/firmware — this is an assumption requiring
  per-vendor verification, not a guarantee (see section 5).

### What the DPU management plane exposes

- The OPI-aligned API surface: Inventory, Provisioning, Network,
  Telemetry, and Lifecycle operations, reachable only from the isolated
  management network used by the control plane — never from the tenant
  data-plane network or the host's general-purpose network interfaces.
- Where a vendor's OPI-alignment is incomplete, vendor-native management
  interfaces (Redfish, CLI, SSH) may be exposed instead, under the same
  network-isolation requirement.
- Nothing on the DPU management plane should be reachable from, or
  advertise itself on, the tenant workload network segment.

### How policy enforcement should remain isolated

- Policy (ACLs, security groups, NAT, routing) is compiled by OVN and
  applied as flow/table state inside the DPU-resident OVS/OVN process —
  a process running in the DPU's own OS, in a memory and execution context
  the host cannot reach.
- The only sanctioned path to *change* that policy is: tenant/operator
  request → Neutron/Kubernetes API → OVN northbound DB → OVN
  southbound DB → `ovn-controller` on the DPU chassis → local OVS flow
  update. A host workload, or a compromised host OS, has no shorter path
  that bypasses this chain.
- Because the enforcement point and the request path are both outside
  host control, a host-side compromise cannot locally disable, flush, or
  rewrite the policy constraining it — it can, at most, generate traffic
  that the *existing* policy will evaluate as it always would.

### Why DPU-side networking services improve isolation

- Moving the forwarding/policy-enforcement engine to a separate OS,
  separate CPU domain, and separate management path than the tenant
  workload means that a full host compromise — including host **root** —
  does not, by itself, grant any privileged path to modify that policy.
  This is the central isolation improvement over a purely
  host-resident OVS/OVN deployment, where a host root compromise
  *would* typically be able to reconfigure the local OVS instance
  directly.
- This mirrors the underlying rationale for vDSE/NSX-style DPU offload in
  general (see [`overview.md` §2](overview.md#2-vmware-vdse-conceptual-model)):
  the value is not solely performance, it is that policy enforcement
  survives host compromise. ODSE reproduces this *architectural property*
  using open components — it does not claim to reproduce it with the same
  level of hardening, certification, or field validation as any specific
  commercial implementation.

---

## 5. Assumptions and limitations

This design is deliberately **not** presented as a proven security
guarantee. The following assumptions and limitations must be read
alongside section 4, and gaps here should be treated as open risks to
track in `docs/threat-model/`, not as resolved:

1. **The PCIe VF channel is assumed to be data-plane-only.** If a given
   vendor's VF driver/firmware exposes any host-triggerable control
   primitive over that same channel (a mailbox, doorbell, or driver
   `ioctl` that can affect representor state, eswitch configuration, or
   DPU OS behavior), that would constitute a side channel undermining the
   boundary described in section 4. This must be verified per vendor in
   `docs/vendor-readiness/` — it is not something this document can
   guarantee in the general case across four different vendor
   architectures.
2. **DPU firmware and silicon are assumed to be trustworthy and correctly
   implemented.** This project does not audit, formally verify, or
   otherwise validate vendor firmware or silicon-level security (e.g.
   resistance to malicious-driver-triggered DMA attacks, firmware
   vulnerabilities, or hardware side channels). A firmware-level
   vulnerability could invalidate the isolation properties described here
   regardless of how correctly the open-source layers above it are
   configured.
3. **No defense against physical access, supply-chain compromise, or
   hardware side-channel attacks.** Physically tampered hardware,
   counterfeit/compromised firmware images introduced before deployment,
   or cache-timing/electromagnetic side channels between host and DPU
   silicon are out of scope for this architecture entirely.
4. **No confidentiality or integrity guarantee for host-to-DPU traffic
   beyond what the PCIe/link layer and OVS/OVN already provide.** There is
   no built-in encryption of intra-chassis VF traffic in this design; if
   that is required, it must be added explicitly (e.g. via IPsec at the
   OVN layer) and is not assumed present by default.
5. **The control plane is assumed to be hardened, patched, and
   network-isolated.** This design concentrates authority into the
   control plane specifically so that fewer systems need this level of
   protection — but it does not reduce the *consequences* of a
   control-plane compromise. A compromised Neutron/Nova, Kubernetes API
   server, or OVN database can reprogram policy for the entire fleet; this
   architecture manages where that risk lives, it does not eliminate it.
6. **vDPA-backed paths may not have identical isolation properties to
   VFIO/SR-IOV paths.** Depending on vendor implementation, part of a
   vDPA control path may execute in host kernel space rather than
   entirely on the DPU. Isolation claims for vDPA-backed workloads must be
   verified per vendor and must not be assumed equivalent to the VFIO
   passthrough case without that verification.
7. **Fail-open/fail-closed behavior on DPU restart or failure is not yet
   uniformly defined.** Whether policy enforcement gaps exist during a
   DPU reboot, crash, or firmware upgrade window is vendor- and
   configuration-dependent and has not been standardized across the four
   vendor tracks at the time of writing. Operators should not assume
   fail-closed behavior unless it has been explicitly verified for their
   specific vendor and configuration.
8. **No formal security certification is claimed.** This project does not
   pursue or claim any certification (e.g. Common Criteria, FIPS) at this
   stage, and this document is not a substitute for one.
9. **These properties are design intent, not yet empirically validated.**
   As of this writing, the isolation properties described in section 4
   are architectural goals backed by the design of OVN/OVS/switchdev and
   the deliberate absence of a host-to-DPU management path — they have
   not yet been exercised end-to-end by the `tests/security/` suite
   referenced in `docs/test-plan/`. Treat this document as the target
   design to validate against, not as a completed validation report.
10. **Scope boundary with the top-level project non-goals.** Consistent
    with [`overview.md` §14](overview.md#14-non-goals), this document does
    not claim NSX Distributed Firewall-equivalent identity/L7 policy
    capability, does not claim any encryption/IDS/IPS capability, and does
    not claim a certified hardware trust model — the isolation described
    here is limited to the host/DPU/control-plane architectural separation
    itself.

---

## References

- [`overview.md`](overview.md) — full architecture overview, especially
  §4-§6 (role definitions) and §13 (design principles).
- [`vdse-to-open-equivalent-mapping.md`](vdse-to-open-equivalent-mapping.md)
  — concept-level mapping, including the DFW and DPU lifecycle rows
  relevant to this document's threat boundary.
- `docs/threat-model/` — the living, detailed threat-model documentation
  this overview feeds into.
- `tests/security/` — the test suite intended to validate (not yet fully
  validating) the isolation properties described here.
- [`open-source-vdse-equivalent-plan.md`](../../open-source-vdse-equivalent-plan.md)
  §8 (Phase 8: Security and isolation) — planned validation activities.
