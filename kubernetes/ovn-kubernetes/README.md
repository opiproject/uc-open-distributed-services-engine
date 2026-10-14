# OVN-Kubernetes DPU mode (ODSE)

This directory is how Open Distributed Services Engine will run
**OVN-Kubernetes** as the pod CNI with **DPU/IPU offload**: the host
cluster schedules workloads; the DPU cluster owns the OVN datapath and
representor bind. It matches
[`docs/architecture/overview.md`](../../docs/architecture/overview.md) §11.

This is **not** a fork of OVN-Kubernetes. ODSE supplies pairing
conventions, Helm value overlays, simulated/hardware profiles, and tests.
Chart versions and exact upstream keys are **not pinned yet** — Helm files
here are comment-only placeholders.

| Path | Purpose |
|---|---|
| [`host-cluster/`](host-cluster/) | Overlay for the **workload** Kubernetes cluster |
| [`dpu-cluster/`](dpu-cluster/) | Overlay for the **DPU OS** Kubernetes cluster |
| [`simulated/`](simulated/) | Kind / `netdevsim` CI profile (`ci/kind-simulated-dpu`) |
| [`hardware/`](hardware/) | Real DPU/IPU profile |

Related: [`kubernetes/dpu-operator`](../dpu-operator/README.md),
[`ansible/roles/sriov_setup`](../../ansible/roles/sriov_setup/README.md),
[`ansible/roles/switchdev_setup`](../../ansible/roles/switchdev_setup/README.md),
[`ansible/roles/ovs_setup`](../../ansible/roles/ovs_setup/README.md),
[`tools/offload-checker`](../../tools/offload-checker/README.md),
[`docs/vendor-readiness/vendor-capability-matrix.md`](../../docs/vendor-readiness/vendor-capability-matrix.md).

---

## 1. Two-cluster model

OVN-Kubernetes **DPU mode** splits CNI components across two Kubernetes
API servers:

```text
 Host cluster (workloads)                    DPU cluster (datapath)
 +---------------------------+               +---------------------------+
 | kube-apiserver            |               | kube-apiserver (DPU OS)   |
 | kube-scheduler, kubelet   |               | kubelet on DPU nodes      |
 | ovnkube (host-side)       |               | ovnkube (DPU-side)        |
 | SR-IOV device plugin      |               | ovn-controller            |
 | pods: eth0 = VF           |               | OVS + representors        |
 +-------------+-------------+               +-------------+-------------+
               |  PCIe VF / representor pair               |
               +-------------------------------------------+
               |         shared OVN NB/SB (logical state)  |
               +-------------------------------------------+
```

- **One logical OVN** (northbound/southbound). Two kube-apiservers.
- The **pod** never talks to the DPU API. It only requests a VF (or
  equivalent) resource and gets an `eth0`.
- The **representor** of that VF is a netdev on the DPU OS. DPU-side
  ovnkube binds the OVN logical port to that representor, not to a veth
  on the host.

Do not collapse this into a single cluster in hardware mode. Simulated
mode may run both roles as Kind clusters (or node pools) for CI; that is
an emulation of the same split, not a different architecture.

---

## 2. Host cluster role

The host cluster is the **workload plane**.

Responsibilities:

- Schedule pods onto DPU-capable worker nodes.
- Run **host-side** ovnkube-node (CNI binary, pod sandbox, VF allocation
  via SR-IOV device plugin / Network Attachment).
- **Not** run the full kernel OVS datapath for those offloaded pods
  (no host veth pair for the offloaded `eth0`).
- Label/taint nodes that have a paired DPU so only eligible pods land
  there.
- Keep a **management** network that does not depend on the offloaded VF.

Placeholder overlay: [`host-cluster/values.yaml`](host-cluster/values.yaml).

Host-side Ansible still applies: `host_base`, `sriov_setup` (VF count for
the device plugin). Switchdev/OVS for the **representor** belong on the
DPU OS, not on the host, in an off-path DPU model. On-path SmartNICs may
keep representors on the hypervisor — then “DPU cluster” may be a second
Kind/k8s control plane or a node role, but the **logical** split stays.

---

## 3. DPU cluster role

The DPU cluster runs on the **DPU operating system** (Arm cores on
BlueField-class cards, equivalent on other vendors).

Responsibilities:

- Run **DPU-side** ovnkube-node / ovn-controller.
- Own `ovs-vswitchd` on the eswitch / DPU datapath.
- Attach **representors** to the OVN integration bridge.
- Compile OVN logical ports to OVS flows; request TC / vendor offload
  where `ovs_setup` enabled `other_config:hw-offload`.
- Must **not** schedule tenant application pods (taints/tolerations).

Placeholder overlay: [`dpu-cluster/values.yaml`](dpu-cluster/values.yaml).

DPU OS bootstrap: `ansible/roles/dpu_base` plus vendor roles, then
`switchdev_setup` and `ovs_setup` before ovnkube can bind ports.

---

## 4. DPU node association with host

Each **host worker** that offloads pods must map 1:1 (or 1:N if
documented) to a **DPU node** object in the DPU cluster.

Association is **explicit configuration**, not inferred from PCI:

| Fact | Where it lives |
|---|---|
| Host node name | Host kubelet node `.metadata.name` |
| DPU node name | DPU cluster node `.metadata.name` |
| Pairing | Labels/annotations on **both** sides (keys TBD when the OVN-Kubernetes chart version is pinned) |
| VF ↔ representor | `tools/topology-discovery/vf_to_representor_map.py` on the **representor owner** |

Until Helm keys are pinned, treat pairing as:

```text
host node  <PF_PCI> / <DPU_SERIAL>
     maps to
dpu node   <DPU_K8S_NODE_NAME>
```

`kubernetes/dpu-operator` is the intended reconciler for that pairing
(discover DPU, label host, enroll DPU node). Do not hand-edit production
pairings once the operator exists.

Wrong pairing (host VF index ≠ DPU representor `vfnum`) yields a pod
that is “Running” with a dead `eth0`. Validate mapping before scale
tests.

---

## 5. Simulated DPU mode for CI

Goal: exercise the **two-role split and manifests** without a physical
DPU. See [`ci/kind-simulated-dpu`](../../ci/kind-simulated-dpu/README.md)
and [`simulated/values.yaml`](simulated/values.yaml).

Typical shape (scripts not assumed finished):

- Kind **host** cluster + Kind **dpu** cluster (or one Kind with two
  node roles — still two ovnkube personalities).
- Fake “VF” / representor pair via `netdevsim`, veth pairs, or mock
  SR-IOV. Packets stay in software.
- Same Helm overlay *structure* as hardware, with offload and device
  plugin flags disabled or stubbed.

**In scope for simulation:** install order, pairing labels, CNI ADD/DEL
against a dummy device, OVN logical port create/delete, CI of this
directory.

**Out of scope:** TC `in_hw`, vendor OVS datapath, real VFIO, firmware,
performance, and anything in
`tools/offload-checker` that needs hardware markers.

Do not copy simulated values onto a physical node.

---

## 6. Hardware DPU mode

[`hardware/values.yaml`](hardware/values.yaml) is the overlay for real
cards. Prerequisites per node pair:

1. DPU firmware/OS reachable; DPU cluster kubelet healthy.
2. Host: IOMMU, SR-IOV VFs, SR-IOV device plugin advertising resources.
3. DPU: switchdev (or vendor eswitch), representors present, OVS running.
4. Pairing labels applied (operator or documented manual bootstrap).
5. OVN DB reachable from DPU-side ovn-controller.

Bring-up order:

```text
host_base / dpu_base → sriov_setup → switchdev_setup → ovs_setup
  → DPU cluster CNI (this overlay) → host cluster CNI (this overlay)
  → SR-IOV device plugin → test pod with VF resource request
```

Hardware mode is **vendor-track specific**. NVIDIA BlueField is the first
intended track (`vendor-capability-matrix`: OVN-Kubernetes DPU mode
`likely`). Other vendors stay `discovery-required` or `future` until a
track says otherwise.

---

## 7. SR-IOV and representor requirements

| Requirement | Host | DPU / eswitch owner |
|---|---|---|
| PF in SR-IOV | `sriov_numvfs` ≥ pods you will place | Usually already created from host or DPU firmware policy |
| VF visible to kubelet | SR-IOV device plugin resource (name TBD per vendor) | VF not used as a DPU-local netdev for the pod |
| Representor | Often **absent** on host in off-path DPU mode | `devlink` `flavour pcivf` for each VF |
| Mapping | VF PCI / index | Same index ↔ representor (`vf_to_representor_map.py`) |

Pods that need offload must **request the VF resource**. A default
OVN-Kubernetes Geneve/veth pod on the same node is the software path and
does not prove DPU mode.

If representors are missing, stop. ovnkube cannot bind a logical port to
a netdev that does not exist. Fix `switchdev_setup` first.

---

## 8. OVS offload backend requirements

On the DPU (or on-path NIC host):

- OVS running; integration bridge owned by OVN (`br-int` or chart
  equivalent).
- `other_config:hw-offload=true` when the kernel/vendor datapath
  supports TC offload (`ansible/roles/ovs_setup`).
- Representors as `system` (kernel) ports, or vendor/DPDK types only when
  that OVS build exists (`ovs_datapath_type` / `interface_type`).
- ovn-controller on the **same** chassis as those ports.

Offload is **not** “DPU mode is installed.” After a traffic-generating
pod, run `tools/offload-checker/ovs_offload_checker.py` against the
representor. Simulated mode will not show `offloaded:yes`.

Vendor OVS backends (OVS-DOCA, etc.) are optional overlays, not the ODSE
baseline. Baseline is kernel OVS + TC flower where the NIC supports it.

---

## 9. Test scenarios

Track these under `tests/functional` and `tests/offload-validation` as
they grow. Minimum set for DPU mode:

| ID | Scenario | Simulated | Hardware |
|---|---|---|---|
| K8S-DPU-1 | Both clusters Ready; ovnkube host + DPU DaemonSets | yes | yes |
| K8S-DPU-2 | Host node paired with exactly one DPU node | yes (fake ids) | yes |
| K8S-DPU-3 | Pod with VF resource: `eth0` is VF, not veth | partial | yes |
| K8S-DPU-4 | Representor appears on DPU; mapped to that VF | no | yes |
| K8S-DPU-5 | Pod-to-pod L3 on same logical switch | yes (veth) | yes (VF) |
| K8S-DPU-6 | Pod to external / egress gateway | optional | yes |
| K8S-DPU-7 | NetworkPolicy / OVN ACL still enforced on DPU | yes (sw) | yes |
| K8S-DPU-8 | CNI DEL: VF and representor released | yes | yes |
| K8S-DPU-9 | Offload markers on representor after iperf | no | yes |
| K8S-DPU-10 | Host kubelet kill does not remove DPU ACLs | no | yes (trust-boundary) |

K8S-DPU-10 is a **security** check from
[`docs/architecture/roles-and-trust-boundary.md`](../../docs/architecture/roles-and-trust-boundary.md):
offloaded policy must not depend on a healthy host workload.

---

## 10. Vendor-specific considerations

Ratings: `docs/vendor-readiness/vendor-capability-matrix.md` row
**OVN-Kubernetes DPU mode**. Fill placeholders only after a lab on that
card. Do not copy NVIDIA values onto another vendor.

### NVIDIA BlueField

- Matrix: **likely** (public OVN-Kubernetes / DOCA DPU material; not
  ODSE-certified).
- Often: DPU mode + switchdev **on the Arm OS**; host PF may stay legacy.
  Confirm against current NVIDIA docs (`<NVIDIA_OVN_K8S_DOC_URL>`).
- Optional datapath: OVS-DOCA (`<NVIDIA_OVS_DOCA_ENABLED>` true/false).
  ODSE baseline remains kernel OVS until a track enables DOCA.
- Resource name for SR-IOV plugin: `<NVIDIA_SRIOV_RESOURCE_NAME>`.

### Intel IPU

- Matrix: **likely** (OpenShift/IPU lifecycle mentions; DPU-mode CNI
  not independently verified here).
- Confirm whether representors land on the **IPU OS** or the host:
  `<INTEL_REPRESENTOR_OWNER>`.
- Chart keys / device plugin: `<INTEL_OVN_K8S_VALUES_NOTES>`.

### AMD Pensando

- Matrix: **discovery-required**.
- Confirm SR-IOV + representors exist at all before any Helm overlay:
  `<PENSANDO_SRIOV_REPRESENTOR_AVAILABLE>`.
- If not, this directory is not applicable; use OPI/vendor API fallback
  from the project plan.

### Marvell OCTEON

- Matrix: **future** for OVN-Kubernetes DPU mode.
- May expose vendor virtio/vhost-user instead of in-kernel representors:
  `<MARVELL_CNI_BACKEND>` (`sriov-representor` | `vhost-user` | `none`).
- Do not assume a second Kubernetes cluster on the DPU OS until
  `<MARVELL_DPU_K8S_AVAILABLE>` is confirmed.

---

## What is deliberately not in this tree yet

- Pinned OVN-Kubernetes chart version or copied upstream templates.
- Live Helm key names presented as facts.
- Vendor-specific DeviceClass YAML as if certified.

Next concrete work: pin a chart in `simulated/`, wire
`ci/kind-simulated-dpu` to these overlays, then clone the same keys into
`hardware/` with offload enabled.
