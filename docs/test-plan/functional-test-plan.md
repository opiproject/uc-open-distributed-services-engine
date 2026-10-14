# Functional test plan (ODSE)

Status: Draft — **what must be true** for the stack, independent of
throughput. Performance: [`performance-methodology.md`](performance-methodology.md).
Security isolation: [`../threat-model/host-dpu-threat-model.md`](../threat-model/host-dpu-threat-model.md).
Offload *markers*: this plan’s FN-17 plus `tests/offload-validation/`.

Implementation home (when scripted): `tests/functional/`. Harness:
`ci/molecule` (roles), `ci/kind-simulated-dpu` (control-plane only),
`ci/hardware-regression` (real cards).

**Pass / skip / fail**

- **Pass:** expected result holds and evidence is stored.
- **Skip (`skipped_not_applicable`):** a documented gate failed (no
  DPU OS, no vDPA mgmtdev, no representors). Not a vendor insult.
- **Fail:** gate was met and the behaviour is wrong.
- Kind/simulation may pass FN-1 (partial), FN-7–8 (software OVS),
  FN-13–14 (veth). It **must not** be filed as pass for VFIO, vDPA,
  switchdev, or hardware offload.

IDs: `FN-01` … `FN-18`. Placeholders match vendor-readiness docs
(`<PF_NAME>`, `<DPU_OS>`, `<BRIDGE>`, …).

Vendor notes are **discovery-first**: do not treat NVIDIA kernel
switchdev as the default for Intel/Marvell/AMD.

---

## FN-01 Inventory discovery

**Objective:** Local inventory JSON is produced and is honest (unknown
= `null` / `available: false`), not a crash.

**Preconditions:** `python3` on the node under test; optional root for
full `devlink`/`ethtool`.

**Steps:**

1. Run `python3 tools/inventory/dpu_inventory.py --pretty -o <OUT>`.
2. Confirm `schema_version` present; inspect `pci_devices`, `netdevs`,
   `devlink_*`, `ovs`, `vendor_hints`.

**Expected result:** Exit 0. Missing tools appear as per-section
errors, not a failed process. No fabricated PCI devices.

**Evidence:** `<OUT>` JSON; command stderr if any.

**Vendor-specific notes:** NVIDIA hint may be `15b3` / `mlx5_core`
(high/medium). Intel `ice` is **low** confidence — not IPU proof.
Marvell driver names are hints. AMD: `1dd8` / `ionic` ≠ DSC management
or offload.

---

## FN-02 DPU management reachability

**Objective:** The **intended** DPU/IPU management endpoint answers
from the control-plane network and **not** from a tenant VF (intent;
full isolation is TM-1).

**Preconditions:** Lab has recorded `<MGMT_URL>` or SSH to `<DPU_OS>`
(or IMC Redfish). If none exists, **skip** (especially AMD §5).

**Steps:**

1. From the control-plane host: SSH or Redfish GET `<REDFISH_BASE>` /
   rshim probe as documented for the vendor — **no git-frozen CLI**.
2. From a tenant namespace/VF (if already provisioned): attempt the
   same URL/port.

**Expected result:** Control plane succeeds. Tenant path fails or is
unroutable. Record protocol (`ssh`|`redfish`|`rshim`|`other`).

**Evidence:** Timestamped success/fail; do not store passwords. Note
`dpu_hostname` / IMC address.

**Vendor-specific notes:** NVIDIA: rshim/DPU OS SSH — confirm DPU vs
NIC mode. Intel: Redfish IMC **likely**, unvalidated. Marvell:
SDK/DAO env **likely**; Redfish **discovery-required**. AMD:
**discovery-required**; skip if no path.

---

## FN-03 PF discovery

**Objective:** Host-visible PF netdev maps to a PCI function.

**Preconditions:** FN-01 or equivalent sysfs. `<PF_NAME>` chosen.

**Steps:**

1. `readlink -f /sys/class/net/<PF_NAME>/device` → `<PF_PCI>`.
2. Confirm `ethtool -i <PF_NAME>` returns a driver (or record error).
3. Optional: `sriov_totalvfs` exists or not (feeds FN-04).

**Expected result:** Stable `<PF_PCI>` in `0000:bb:dd.f` form. Driver
name recorded.

**Evidence:** Paths, `ethtool -i` text, inventory netdev entry.

**Vendor-specific notes:** NVIDIA DPU mode: host PF may be **legacy**
while Arm PF is switchdev — record **which** PF. Intel: representor
owner unknown. Marvell: PF may be bound to a vendor driver while
datapath is DPDK. AMD: `ionic` PF only.

---

## FN-04 VF creation

**Objective:** Requested VF count appears as `virtfn*` (or vendor API
equivalent documented in the lab).

**Preconditions:** FN-03; `sriov_totalvfs` ≥ requested `N` **or**
vendor FW path documented. Out-of-band SSH. `sriov_setup` safety:
do not disrupt existing non-zero `numvfs` without
`sriov_setup_allow_disruptive_reconfigure`.

**Steps:**

1. Read current `sriov_numvfs`.
2. Apply `ansible/roles/sriov_setup` or equivalent **only if** allowed.
3. Count `virtfn*`; list VF PCI.

**Expected result:** `N` VFs **or** skip if SR-IOV not exposed
(`<PENSANDO_SRIOV>=not-exposed`, Intel FW-only, etc.).

**Evidence:** sysfs before/after; `sriov_setup` facts file if used.

**Vendor-specific notes:** NVIDIA may need FW VF enable
(`<NVIDIA_SRIOV_FW_CMD>` from docs, not git). Intel: sysfs vs Redfish
**discovery-required**. Marvell: likely but confirm. AMD: **verify
whether**; do not assume.

---

## FN-05 Representor discovery

**Objective:** Each VF index maps to a representor **or** the tool
honestly returns `null`.

**Preconditions:** FN-04 VFs exist **or** switchdev already on. Run
the mapper on the **OS that owns representors**.

**Steps:**

1. `python3 tools/topology-discovery/vf_to_representor_map.py --pf <PF> --pretty -o <MAP>`.
2. `devlink port show` (if available).

**Expected result:** For kernel switchdev: `mapping_method`
`devlink_pcivf` or `phys_port_name` and non-null
`representor_netdev`. If DPDK-only (Marvell): `representor_netdev`
may be `null` — **skip** kernel attach tests, do not fail the mapper.

**Evidence:** `<MAP>` JSON; `devlink` snippet.

**Vendor-specific notes:** NVIDIA: kernel representors **supported**
in matrix; still confirm Arm vs host. Intel: `<INTEL_REPRESENTOR_OWNER>`.
Marvell: DPDK representors **likely**; kernel **discovery-required**.
AMD: **verify whether**.

---

## FN-06 switchdev enablement

**Objective:** Eswitch mode is `switchdev` (or vendor equivalent)
on the PF that must own it.

**Preconditions:** FN-03; `devlink` present. **Do not** run on the
wrong OS (NVIDIA DPU mode: often Arm-only).

**Steps:**

1. `devlink dev eswitch show pci/<PF_PCI>` (or documented handle).
2. If change is in scope: `ansible/roles/switchdev_setup` with
   explicit inventory; not over the PF used for SSH.

**Expected result:** Mode matches lab policy **or** skip if `devlink
eswitch` unsupported (Marvell/AMD/Intel discovery).

**Evidence:** Before/after `eswitch show`; switchdev_setup facts.

**Vendor-specific notes:** NVIDIA: re-read DOCA Switching for DPU vs
NIC mode. Intel/AMD/Marvell: **discovery-required** — skip ≠ fail.

---

## FN-07 OVS bridge creation

**Objective:** `<BRIDGE>` exists in `ovs-vsctl show`.

**Preconditions:** OVS running (`host_base` / `dpu_base` / FN-02 OS).

**Steps:**

1. `ansible/roles/ovs_setup` with `ovs_bridges: [<BRIDGE>]` (or
   `ovs-vsctl add-br` in a lab).
2. `ovs-vsctl br-exists <BRIDGE>`.

**Expected result:** Bridge present; datapath_type as requested or
OVS default. Idempotent second run.

**Evidence:** `ovs-vsctl show`; ovs_setup facts.

**Vendor-specific notes:** DAO replacing `vswitchd` (Marvell option C):
this test may target `<DAO_OVS_APP>` instead — record tool. Do not
assume distro OVS on IPU appliance OS.

---

## FN-08 OVS hardware offload setting

**Objective:** `other_config:hw-offload` matches
`ovs_enable_hw_offload` **when that OVS instance is the intended
datapath**.

**Preconditions:** FN-07. Kernel OVS path. If vendor datapath does
not use this key, **skip** and record `<DAO_OFFLOAD_ENABLE>` /
DOCA equivalent instead.

**Steps:**

1. Set via `ovs_setup` (`true` or `false` as the lab intends).
2. `ovs-vsctl get Open_vSwitch . other_config:hw-offload`.

**Expected result:** Value equals intent. This **does not** prove
packets are offloaded (that is FN-17).

**Evidence:** vsctl get output; facts file.

**Vendor-specific notes:** NVIDIA kernel: applicable. OVS-DOCA/DAO:
confirm whether the key is N/A. AMD: do not set `true` unless FN-05
passed.

---

## FN-09 VM with VF passthrough

**Objective:** Guest sees a **vendor NIC** (not virtio-net) for the
passed-through VF.

**Preconditions:** FN-04; IOMMU; `qemu/vfio/vf-passthrough.md`;
libvirt. Skip if no VFs or IOMMU groups unusable.

**Steps:**

1. Bind VF to `vfio-pci` (or `managed='yes'`).
2. Start domain from `qemu/libvirt-xml/vf-passthrough.xml.template`.
3. In guest: `lspci -nn`, `ethtool -i <GUEST_IFACE>`.

**Expected result:** PCI IDs match host VF vendor/device; driver is
vendor (e.g. mlx5), not `virtio_net`.

**Evidence:** guest `lspci`; host `virsh dumpxml` hostdev fragment.

**Vendor-specific notes:** NVIDIA VFIO **supported** as Linux
mechanism. Intel/Marvell/AMD: **discovery-required**. Guest image
must include the vendor driver.

---

## FN-10 VM with vDPA

**Objective:** Guest sees **virtio-net** backed by `/dev/vhost-vdpa-*`.

**Preconditions:** `vdpa mgmtdev show` non-empty;
`qemu/vdpa/vhost-vdpa.md`. Else **skip**.

**Steps:**

1. `vdpa dev add` as documented; record `<VHOST_VDPA_DEV>`.
2. Start `vhost-vdpa.xml.template` domain.
3. Guest: `ethtool -i` → `virtio_net`; `lspci` virtio IDs (`1af4`).

**Expected result:** virtio-net, not VF PCI ID. Device exclusive to
one QEMU.

**Evidence:** `vdpa dev show`; guest ethtool; libvirt `type='vdpa'`.

**Vendor-specific notes:** NVIDIA `mlx5_vdpa` **likely**. Others
**discovery-required**. Marvell may be vhost-user instead — skip this
ID, do not fake vDPA.

---

## FN-11 VM-to-VM connectivity

**Objective:** L3 (or L2) connectivity between two VMs on the same
logical switch / OVS bridge.

**Preconditions:** Two VMs (VFIO, vDPA, or virtio baseline).
Representors (or DPDK ports) attached if using eswitch (FN-05, FN-07).
IPs `<VM_A_IP>`, `<VM_B_IP>`.

**Steps:**

1. `ping -c 5` A→B and B→A.
2. Optional: `iperf3` short run (functional, not perf methodology).

**Expected result:** Echo replies; no reliance on hypervisor
software bridge if the lab claims eswitch (still **functional** if
packets flow).

**Evidence:** ping output; `ip neigh`; which `scenario` bind was used.

**Vendor-specific notes:** Same-host hairpin vs two-host — record
shape. AMD without representors: only if an alternative attach
exists (OPI) or skip.

---

## FN-12 VM-to-wire connectivity

**Objective:** VM reaches a tester on the **physical uplink** (or
documented gateway), not the management SSH NIC unless it *is* the
uplink under test (discouraged).

**Preconditions:** FN-11 path plus uplink on the eswitch/DAO.
`<WIRE_IP>` on a known tester.

**Steps:**

1. Ping/TCP from VM to `<WIRE_IP>`.
2. Confirm source is `<GUEST_IFACE>` (the VF/vDPA), not a second NIC.

**Expected result:** Connectivity. Reverse direction if the tester
can initiate.

**Evidence:** ping/tcpdump on uplink PF (careful with volume);
interface used.

**Vendor-specific notes:** Do not use BMC/Redfish network as
`<WIRE_IP>`. NVIDIA Arm OVS uplink vs host PF — use the documented
uplink.

---

## FN-13 pod-to-pod connectivity

**Objective:** Two pods on DPU-capable nodes communicate on the
cluster network.

**Preconditions:** OVN-Kubernetes (or CNI under test) Ready. For
**DPU mode**, VF resource requested (see
`kubernetes/ovn-kubernetes/README.md`). Kind: veth simulation OK for
this ID **only as simulation**.

**Steps:**

1. Create two pods (same namespace, policy allowing).
2. `wget`/`ping`/`nc` pod IP to pod IP.

**Expected result:** Success. Hardware lab: `eth0` is VF not veth if
DPU mode claimed (else fail or skip DPU-mode claim).

**Evidence:** `kubectl exec` output; `ip link` in pod; node pairing
labels.

**Vendor-specific notes:** OVN-K8s DPU **likely** NVIDIA/Intel,
**future** Marvell, **discovery-required** AMD. Do not pass hardware
DPU-mode if Kind was used.

---

## FN-14 pod-to-service connectivity

**Objective:** Pod reaches a ClusterIP/Service backend.

**Preconditions:** FN-13 environment; a Service with endpoints.

**Steps:**

1. `kubectl get svc,ep`.
2. From a client pod, connect to `ClusterIP:port`.

**Expected result:** HTTP/TCP success. OVN LB/NAT may run on DPU or
host — record where `ovn-controller` runs.

**Evidence:** curl output; `kubectl describe svc`.

**Vendor-specific notes:** kube-proxy vs OVN-K8s — document. DPU
hairpin to host-network services may be **discovery-required**.

---

## FN-15 DPU-side network function

**Objective:** A DPU-resident `NetworkFunction` (or lab equivalent)
is running and in path **or** skip if NFs not implemented.

**Preconditions:** OPI adapter or vendor NF runtime. Image
`<NF_IMAGE>`. Default: **skip** until `apply_network_function` exists.

**Steps:**

1. Apply NF spec (OPI or kubectl).
2. Confirm process/container on DPU OS.
3. Send traffic that should hit the NF (ACL deny or NAT).

**Expected result:** `status.state=running` and observable effect
(drop/NAT) **or** skip. Host must not be required to keep the NF
alive (intent).

**Evidence:** DPU `ps`/crictl; OPI object JSON; before/after traffic.

**Vendor-specific notes:** Intel NF placement **discovery-required**.
NVIDIA: optional DOCA/Arm container. Marvell: DAO app may *be* the
NF. AMD: OPI contingency.

---

## FN-16 Telemetry collection

**Objective:** At least one honest `TelemetrySample` or checker/ethtool
dump; missing metrics omitted.

**Preconditions:** FN-03; optional OVS.

**Steps:**

1. `ovs_offload_checker.py --interface <IF> --format json -o <TEL>`
   and/or `dpu_inventory.py`.
2. If OPI `collect_telemetry` implemented, call it.

**Expected result:** JSON written. No crash. Empty highlighted
counters allowed.

**Evidence:** `<TEL>`; inventory JSON.

**Vendor-specific notes:** Do not require DOCA/DAO names in core
output. Fabricated zeros = fail (OPI-6).

---

## FN-17 Offload validation

**Objective:** After generating traffic, **evidence** of offload is
recorded; `unknown` is valid, fake `offloaded:yes` is fail.

**Preconditions:** FN-08 intent true **or** vendor backend. Traffic
from FN-11. Representor owner reachable.

**Steps:**

1. Generate traffic (≥ a few seconds).
2. `ovs_offload_checker.py` with representor + PF.
3. Optional: `tc filter show`, vendor `rte_flow` dump.

**Expected result:** Checker runs. If kernel TC path and hardware
works: some `probable_offloaded` **or** documented vendor hits. If
all `unknown`: **pass with null ratio**, not a performance win.
If `hw-offload=true`, representors exist, and traffic is software-only
when vendor docs say TC should hit: **fail**.

**Evidence:** checker JSON; `offload_evidence` fields from
performance schema if also benchmarking.

**Vendor-specific notes:** NVIDIA kernel: markers expected more often.
DAO/DOCA: kernel markers may be absent — require vendor dump. AMD:
skip unless FN-05/08 gates passed.

---

## FN-18 Failure recovery

**Objective:** After a **documented** restart, connectivity returns
**or** fail-open/fail-closed is recorded (not assumed fail-closed).

**Preconditions:** FN-11 or FN-13 working. Choose **one** fault:
`ovs-vswitchd` restart on datapath OS, DPU reboot, or host kubelet
stop (not all three in one case).

**Steps:**

1. Capture baseline ping.
2. Inject fault (lab-approved).
3. Watch ping during window; after recovery, ping again.
4. If kubelet-stop variant: check DPU ACLs still drop a forbidden
   flow (TM-2 / K8S-DPU-10) — may be a linked security test.

**Expected result:** Datapath restart: sessions recover within a
recorded bound **or** documented persistent drop. Policy during
outage: **recorded** fail-open vs fail-closed — neither is “wrong”
until vendor-readiness says otherwise. Silent loss of isolation
without record = fail.

**Evidence:** timestamps; ping loss; `ovs-vsctl show` after;
which fault was used.

**Vendor-specific notes:** DPU reboot blast radius is **all** VFs on
the card. Firmware upgrade is out of scope unless `<FW>` pinned.
Kind: only process restart, not PCIe.

---

## Suggested order and dependencies

```text
FN-01 → FN-03 → FN-02 (if DPU)
      → FN-04 → FN-06 → FN-05
      → FN-07 → FN-08
      → FN-09 and/or FN-10 → FN-11 → FN-12
      → FN-13 → FN-14
      → FN-16 → FN-17 (needs traffic)
      → FN-15 (optional)
      → FN-18 (destructive; last)
```

Do not run FN-18 on a shared lab without a window. Do not mark a
vendor `supported` in the capability matrix from these tests unless
`tests/vendor-certification/` cites the evidence files.
