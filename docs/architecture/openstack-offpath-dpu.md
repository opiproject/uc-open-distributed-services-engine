# OpenStack off-path DPU (design)

Status: Draft  
Scope: Architectural model for OpenStack **Neutron + Nova + OVN** when
the tenant datapath and representors live on a **DPU/IPU OS**, not on
the hypervisor.  
Audience: Contributors, operators evaluating M4 / cert Level 4  
Companions: [`openstack/neutron-ovn-smartnic/README.md`](../../openstack/neutron-ovn-smartnic/README.md)
(implementation), [`overview.md`](overview.md) §6 and §10,
[`roles-and-trust-boundary.md`](roles-and-trust-boundary.md),
[`kubernetes/ovn-kubernetes/README.md`](../../kubernetes/ovn-kubernetes/README.md)
(same split for pods).

This document does **not** pin an OpenStack release, Neutron
`vnic_type` string, or vendor CLI. Those belong in the lab report and
vendor-readiness docs.

---

## 1. Problem

vSphere Distributed Services Engine–style offload for **VMs** means:
the guest uses an SR-IOV VF (or virtio-vDPA), while switching, ACLs,
and overlay encap run on the DPU. OpenStack already has Nova (compute)
and Neutron/OVN (logical network). The design question is **where
`ovn-controller` and `ovs-vswitchd` sit**, and how a Neutron port
binds a **host VF** to a **DPU representor**.

Two easy mistakes:

1. **On-path confusion.** Some SmartNIC designs keep representors on
   the hypervisor. That is a valid SKU, but it is **not** the off-path
   trust split ODSE treats as the strong default
   (`host-dpu-threat-model.md`).
2. **Manual OVS as “OpenStack.”** Adding a representor with
   `ovs-vsctl` proves M2, not M4. Level 4 requires **OVN-driven**
   bind (Neutron logical port).

---

## 2. Off-path vs on-path vs virtio

```text
 Off-path DPU (target)
   Guest VF on host ──PCIe── representor + OVN/OVS on DPU OS ── uplink

 On-path SmartNIC
   Guest VF on host ── representor + OVN/OVS on hypervisor ── uplink
   (DPU may still accelerate; control plane is host-local)

 Software virtio
   Guest tap/veth ── OVS/OVN on hypervisor ── uplink
```

ODSE M4 is **off-path**. Document an on-path lab explicitly
(`host_ovn_offpath_false`); do not mix evidence.

The Kubernetes analog is the two-cluster OVN-Kubernetes DPU model:
host owns the workload interface; DPU owns ovn-controller. OpenStack
uses two **OSes** (compute + DPU) rather than two kube-apiservers,
with the same logical split.

---

## 3. Control vs data vs management

| Plane | OpenStack / OVN | Where it runs |
|---|---|---|
| Control | Nova API, Neutron, Placement, OVN NB/`northd` | Controllers |
| Logical apply | `ovn-controller` | **DPU chassis** (off-path) |
| Data | `ovs-vswitchd`, eswitch, uplink | **DPU OS** |
| Workload I/O | VF / vDPA in QEMU | **Host** |
| Management | SSH, SB connection, OPI/Redfish | Isolated net; not tenant VF |

OPI (`opi-alignment.md`) does **not** replace Neutron. It may
inventory the card and run DPU-resident functions. Tenant security
groups stay OVN ACLs authored via Neutron.

---

## 4. Binding as the composition root

The only object that joins Nova, Neutron, and the DPU is the
**Neutron port bind**:

1. Neutron creates a logical port (UUID, network, SG).
2. Nova places the VM and claims a VF (PCI).
3. Binding profile carries VF identity + SmartNIC/DPU capability
   flags (`<NEUTRON_VNIC_TYPE>`, `<OPENSTACK_RELEASE>`).
4. OVN places the logical switch port on the **DPU chassis**.
5. `ovn-controller` matches `iface-id` to the representor netdev.
6. Guest traffic hits the VF; the eswitch presents it on the
   representor; OVN/OVS/TC (or vendor DP) forwards.

If step 4 uses the hypervisor chassis, packets never meet OVN on the
DPU. If step 5 uses the wrong `vfnum`, two tenants can be crossed or
blackholed. Mapping must use `devlink` / `phys_port_name`
(`vf_to_representor_map.py`), not netdev naming folklore.

Host_id (Nova) and chassis name (OVN) **may differ**. That is
expected in off-path; it must be written down, not “fixed” by
running ovn-controller on the host.

---

## 5. Failure and trust (design intent)

The point of off-path is a **smaller blast radius**: stopping
`nova-compute` should not uninstall DPU ACLs. Conversely, a DPU
`ovn-controller` or `ovs-vswitchd` fault **does** affect tenant
forwarding; fail-open vs fail-closed is **not** assumed (FN-18).

Southbound and OPI must be unreachable from the guest VF (FN-02,
TM-1). Firmware and eswitch remain vendor-specific; Neutron must not
embed DOCA/DAO/Redfish payloads.

Intel and Marvell OpenStack off-path are **future** in the capability
matrix. NVIDIA is **likely** and still unvalidated until FN-11/12
run on OVN-created ports. AMD needs representors (or a documented
non-OVS attach) before this design applies.

---

## 6. What “done” means

Skeletons in `openstack/` are not M4. Done when:

- OpenStack release is pinned.
- Chassis placement is `dpu` for the ports under test.
- FN-11 and FN-12 pass on Neutron/OVN ports.
- Cert Level 4 OpenStack track evidence is attached.

Kind cannot substitute. Manual representor plug cannot substitute.
