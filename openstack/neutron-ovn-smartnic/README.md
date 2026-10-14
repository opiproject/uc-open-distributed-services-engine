# Neutron OVN SmartNIC / off-path DPU (ODSE)

How Open Distributed Services Engine implements an **OpenStack
off-path SmartNIC DPU** model with **ML2/OVN**: the guest sees a host
VF (or vDPA parent); OVN programs the **representor on the DPU OS**.

This is **not** a Neutron fork, not an OpenStack distro, and not
on-path SmartNIC (representors on the hypervisor). Pin
`<OPENSTACK_RELEASE>` in the lab report before claiming cert Level 4.

Companion: [`docs/architecture/openstack-offpath-dpu.md`](../../docs/architecture/openstack-offpath-dpu.md)
(design), [`nova-vnic-remote-managed/`](../nova-vnic-remote-managed/)
(Nova), [`docs/architecture/overview.md`](../../docs/architecture/overview.md)
§10.1.

Placeholders (`<NEUTRON_VNIC_TYPE>`, chassis hostname keys) must be
filled from **that** OpenStack + Neutron OVN docs. Do not copy NVIDIA
eswitch onto Intel/Marvell/AMD.

Expected later files (not required to read this README): commented
`ml2_conf.ini` snippets, example `openstack port create` JSON.

---

## 1. Purpose

OpenStack VMs on DPU-capable compute need:

- **Nova** to plug a PCI VF (or vhost-vDPA) into QEMU.
- **Neutron** to own the logical port (network, security groups, binding).
- **OVN** to compile that port to OVS on the chassis that **owns the
  representor**.

Off-path means that chassis is the **DPU**, not `nova-compute`’s
hypervisor OVS. Traffic classification, ACLs, and (when proven)
hardware offload run next to the eswitch. The host CPU is not the
tenant vSwitch.

Without this split, operators either hairpin tenant packets through
the hypervisor (on-path) or bind OVN to the wrong OVS and never
offload.

---

## 2. Architecture diagram

```text
                    OpenStack control plane
         +-------------------------------------------+
         |  Nova API/scheduler   Neutron ML2/OVN     |
         |  Placement            OVN NB / SB         |
         +------------------+------------+-----------+
                            |            |
              schedule VM   |            | logical ports
                            v            v
 Host (x86 compute)                    DPU OS (Arm / IPU)
 +---------------------------+         +---------------------------+
 | nova-compute, libvirt     |  PCIe   | ovn-controller (chassis)  |
 | QEMU + VFIO VF (or vDPA)  | <-----> | ovs-vswitchd + br-int     |
 | sriov_setup VFs           |  VF     | representor netdev        |
 | NO tenant ovn-controller* |         | switchdev / vendor DP     |
 +---------------------------+         +-------------+-------------+
       guest eth0 = VF                               |
                                                     v
                                              physical uplink

 * Unless the lab is explicitly on-path SmartNIC (see §8).
```

One OVN cluster. Two machines (host + DPU) per offloaded compute
node. Management/API networks are **not** the tenant VF.

---

## 3. Host-side components

| Component | Role |
|---|---|
| `nova-compute` + libvirt/QEMU | Spawn VM; PCI/vDPA attach |
| `sriov_setup` | Create VFs; do not reconfigure live `numvfs` unsafely |
| IOMMU / `vfio-pci` | VF passthrough (`qemu/vfio/`) |
| Optional vDPA | `qemu/vdpa/` instead of raw VF in guest |
| Placement / PCI whitelist | Only DPU-backed PFs in device spec |
| Management NIC | Nova, SSH, OVN SB **client if any** — not tenant data |

Host does **not** attach the representor to `br-int` in off-path mode.
Representors are often **invisible** on the hypervisor.

Ansible: `host_base`, `sriov_setup`. Not `switchdev_setup` on the host
unless the SKU is on-path.

---

## 4. DPU-side components

| Component | Role |
|---|---|
| DPU OS (`dpu_base`) | Reachable mgmt; packages |
| `switchdev_setup` | Eswitch; representors (`flavour pcivf`) |
| `ovs_setup` | `br-int` (or documented bridge); hw-offload knobs |
| `ovn-controller` | Chassis; binds iface-id → representor |
| `ovs-vswitchd` | Datapath; TC/vendor offload |
| Uplink PF on DPU | FN-12 wire path |
| Optional `opi_services` | Inventory/lifecycle; loopback+TLS |

Vendor roles (`vendor_nvidia_bluefield`, …) only as documented. AMD
without representors: **do not** pretend this model works; see
vendor-readiness (OPI contingency).

---

## 5. Neutron role

Neutron (ML2 + OVN mechanism driver) is the **port owner**:

- Create network / subnet / port; security groups → OVN ACLs.
- Set **binding** so OVN knows the port is SmartNIC/DPU-backed, not
  a hypervisor `normal` veth.
- Persist `binding:host_id`, `binding:vif_type`, `binding:profile`.
- Does **not** run `ovs-vsctl` on the hypervisor for these ports.

Configuration lives in Neutron’s OVN ML2 settings for
`<OPENSTACK_RELEASE>` (`<ML2_OVN_SMARTNIC_OPTIONS>`). Typical themes
(verify upstream): enable OVN, VIF plug for switchdev/remote-managed,
and metadata that names the **DPU chassis**.

Port create sketch (placeholders — not a frozen CLI):

```text
openstack port create --network <NET> \
  --vnic-type <NEUTRON_VNIC_TYPE> \
  --binding-profile <PROFILE_JSON> \
  <PORT>
```

`<NEUTRON_VNIC_TYPE>` is often in the family of `direct`,
`smart-nic`, or `remote-managed` depending on release. **Look it up.**
Wrong type → Nova plugs a virtio tap and OVN never sees a representor.

---

## 6. Nova role

Nova **schedules and plugs**. It does not compile OVN flows.

- Flavor/image + PCI alias / SR-IOV resource so the VM lands on a
  host with the PF.
- For `vnic_type` matching remote-managed/SmartNIC: pass VF BDF into
  libvirt; **do not** require OVS on the host for that NIC.
- Cooperate with Neutron: after bind, guest XML uses the VF Neutron
  allocated (`pci_slot` in profile).
- Details: [`nova-vnic-remote-managed/README.md`](../nova-vnic-remote-managed/README.md).

If Nova and Neutron disagree on host_id vs DPU chassis name, the VM
boots with a VF that has **no** logical port on the representor
(silent blackhole).

---

## 7. OVN role

OVN remains **source of truth** for tenant L2/L3/ACL (same as
non-DPU). Differences:

- Logical switch port `options:requested-chassis` (or equivalent for
  the pinned OVN) must be the **DPU chassis name**, not necessarily
  `nova-compute` hostname.
- `ovn-controller` on that chassis sets `external_ids:iface-id` on
  the representor to the Neutron port UUID (or documented iface-id).
- Southbound is reachable from the DPU **management** network only
  (FN-02 / TM-1). Tenant VF must not reach SB.

Neutron’s OVN driver writes NB; DPU `ovn-controller` reads SB.
Control-plane `ovn-northd` stays on the OpenStack controllers.

---

## 8. ovs-vswitchd and ovn-controller placement

| Mode | `ovs-vswitchd` (tenant) | `ovn-controller` | Representors |
|---|---|---|---|
| **Off-path DPU (this doc)** | DPU OS | DPU OS | DPU OS |
| On-path SmartNIC | Hypervisor | Hypervisor | Hypervisor |
| Software virtio (no DPU) | Hypervisor | Hypervisor | N/A (veth/tap) |

Record `dpu_ovn_chassis` vs `host_ovn_offpath_false` in the cert
report. Mixing modes on one host without a written exception fails
Level 4.

Host may still run OVS for **management** or leftover virtio ports;
that bridge must not be confused with tenant `br-int` on the DPU.

---

## 9. VF to DPU representor mapping

Binding is only correct if VF index / PCI on the **host** matches
the representor on the **DPU**.

1. On the host: VF PCI, `virtfnN`, VFIO bind
   (`tools/inventory`, Nova PCI).
2. On the DPU: `tools/topology-discovery/vf_to_representor_map.py`
   (`devlink` `flavour pcivf` + `vfnum`, else `phys_port_name`).
3. Pairing: host compute name ↔ DPU hostname / serial
   (`dpu_host_identity` is **not** inferred by the tool core; set it
   from inventory or vendor plugin).
4. Neutron/OVN: logical port UUID ↔ representor `iface-id`.

Never guess representors from names (`eth0v1`). If mapping is
`null`, **do not** `ovs-vsctl add-port`. AMD/Intel/Marvell: follow
vendor-readiness; kernel representors may be absent.

---

## 10. Port binding metadata

Minimum facts to store (Neutron port + OVN LSP + lab notes):

| Field | Meaning |
|---|---|
| `binding:vnic_type` | `<NEUTRON_VNIC_TYPE>` |
| `binding:vif_type` | Observed after bind (`<VIF_TYPE>`) |
| `binding:host_id` | Nova compute host |
| `binding:profile` | PCI (`pci_slot`), `physical_network`, capabilities e.g. `switchdev` if the release uses them |
| OVN chassis | DPU chassis name (may **differ** from `host_id`) |
| `iface-id` | Neutron port UUID on representor |
| VF PCI / vf_index | Host |
| `representor_netdev` | DPU |
| `mapping_method` | From `vf_to_representor_map.py` |

Example shape (illustrative, not schema-frozen):

```json
{
  "neutron_port_id": "<UUID>",
  "vnic_type": "<NEUTRON_VNIC_TYPE>",
  "nova_host_id": "<COMPUTE_HOSTNAME>",
  "ovn_chassis": "<DPU_CHASSIS>",
  "vf_pci": "<VF_PCI>",
  "vf_index": "<N>",
  "representor_netdev": "<REP>",
  "openstack_release": "<OPENSTACK_RELEASE>"
}
```

`vendorExtensions` / extra profile keys stay vendor-specific and
must not override these meanings.

---

## 11. Failure scenarios

| Failure | Tenant datapath | Isolation | What to record |
|---|---|---|---|
| `nova-compute` down | Running VMs **may keep** VF I/O | OVN ACLs still on DPU | FN-18 / TM-2 analog: hypervisor death ≠ ACL loss |
| DPU `ovn-controller` down | New ports fail; existing flows may stale | Fail-open vs fail-closed is **vendor/OVN-version specific** | FN-18 |
| DPU `ovs-vswitchd` restart | Brief drop then recover **or** persistent drop | Same | FN-18 |
| DPU reboot | VF link down until eswitch+OVS+OVN return | — | Recovery bound |
| Wrong chassis | VM up, ping fail | — | binding vs `ovn-sbctl show` |
| VF/representor mismatch | Blackhole or wrong tenant | Cross-connect risk | mapping JSON |
| SB on tenant net | — | **Fail TM-1 / FN-02** | Never “workaround” |
| Host OVS used by mistake | Software path or no offload | Claims L2/L4 falsely | Cert fail |

Do not assume fail-closed. See
[`docs/threat-model/host-dpu-threat-model.md`](../../docs/threat-model/host-dpu-threat-model.md).

---

## 12. Test plan

Hardware is **required** for Level 4 OpenStack. Docs + this README
are not a pass. No DPU: stop at design review; do not fake ports.

| ID | What | Pass |
|---|---|---|
| Doc review | Chassis placement written | Off-path vs on-path chosen |
| Map | `vf_to_representor_map.py` on DPU | `representor_netdev` non-null or skip vendor |
| Neutron bind | `openstack port show` | vnic_type + profile + host_id |
| OVN | `ovn-nbctl` / `ovn-sbctl` | LSP on **DPU** chassis; iface-id on representor |
| FN-11 | VM↔VM | Under **OVN-created** ports, not only manual `ovs-vsctl` |
| FN-12 | VM↔wire | Uplink is DPU data PF, not BMC |
| FN-02 | SB not on tenant VF | From guest |
| FN-08 / FN-17 | Offload knobs / evidence | Optional for L4; needed for L2 story |
| FN-18 | One restart | Recorded recovery / fail-open-closed |
| Cert L4 | [`vendor-certification.md`](../../docs/test-plan/vendor-certification.md) | Pinned OpenStack version; Intel/Marvell **future** unless they ran this |

Kind/simulation cannot award this track. NVIDIA is the first
hardware candidate (`likely` in matrix, unvalidated until this
evidence exists).
