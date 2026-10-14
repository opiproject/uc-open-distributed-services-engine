# Kind simulated DPU (CI)

Skeletons to stand up **two Kind clusters** that mimic OVN-Kubernetes
**DPU mode** (host workload plane + DPU datapath plane) **without a
physical DPU**. Chart values are not pinned; these scripts must not
`helm install` OVN-Kubernetes until
[`kubernetes/ovn-kubernetes/simulated/values.yaml`](../../kubernetes/ovn-kubernetes/simulated/values.yaml)
has real keys.

Architecture intent:
[`docs/architecture/overview.md`](../../docs/architecture/overview.md) §11
and [`kubernetes/ovn-kubernetes/README.md`](../../kubernetes/ovn-kubernetes/README.md)
§1 and §5.

| Script | Does |
|---|---|
| [`create-host-cluster.sh`](create-host-cluster.sh) | Kind cluster `odse-host` (override `ODSE_HOST_CLUSTER`) |
| [`create-dpu-cluster.sh`](create-dpu-cluster.sh) | Kind cluster `odse-dpu` (override `ODSE_DPU_CLUSTER`) |
| [`delete-clusters.sh`](delete-clusters.sh) | Deletes **only** those two names |

Scripts are **safe stubs**: they check tools, refuse to clobber a
different cluster name, and stop before CNI/Helm. They are meant to run
on Linux CI (or a Linux workstation with Docker + Kind), not on the DPU
itself.

---

## 1. Why simulated DPU mode matters

Physical DPU/IPU time is scarce, vendor-specific, and late in the
project plan. The **control-plane split** (two kube-apiservers, host vs
DPU ovnkube personalities, node pairing) can still be wrong in YAML and
CI would never see it if every job waited for BlueField/IPU/OCTEON
hardware.

Simulation exists so ODSE can:

- Iterate pairing labels, taints, and install **order** daily.
- Fail PR CI when a manifest assumes a single cluster.
- Keep `kubernetes/ovn-kubernetes/` honest: simulated overlay ≠ hardware
  overlay (`enableHwOffload` stays false here).
- Unblock M5-style work in
  `open-source-vdse-equivalent-plan.md` before vendor tracks finish
  firmware bring-up.

It is **not** a substitute for `ci/hardware-regression`. A green Kind job
does not mean TC offload or switchdev works.

---

## 2. What can be validated in simulation

Aligned with test IDs in `kubernetes/ovn-kubernetes/README.md` §9:

| Can validate | How (once Helm is wired — still TODO) |
|---|---|
| Two clusters become Ready | Kind + `kubectl get nodes` |
| Host vs DPU DaemonSet personalities | Overlay structure, not live chart yet |
| Pairing by **node name** (not PCI) | Labels/annotations on Kind nodes |
| CNI ADD/DEL against a **dummy** interface | veth / netdevsim pair (TODO in scripts) |
| OVN logical port create/delete in software | When a software OVN-K8s chart is pinned |
| NetworkPolicy / OVN ACL in the kernel datapath | Software OVS only |
| Install/delete idempotence of these scripts | `delete-clusters.sh` is name-scoped |

Also valid: shellcheck, “did we helm-install production hardware
values by mistake?” (the scripts must keep using `simulated/values.yaml`
only).

---

## 3. What cannot be validated without hardware

| Cannot validate here | Needs |
|---|---|
| SR-IOV VF creation / VFIO / vDPA | Real PF (`sriov_setup`) |
| Representors (`flavour pcivf`) | `switchdev_setup` on real eswitch |
| VF ↔ representor PCI mapping | `vf_to_representor_map.py` on DPU OS |
| `offloaded:yes` / TC `in_hw` | `ovs_offload_checker.py` + NIC |
| Vendor OVS (OVS-DOCA, etc.) | Vendor build + firmware |
| Host kubelet death vs DPU ACL persistence | Real split trust boundary |
| Performance vs VF passthrough | `tests/performance` on hardware |
| Firmware, BMC, OPI lifecycle | Vendor DPU OS |

`netdevsim` is **not** switchdev. Do not parse its sysfs as a vendor
capability matrix result.

---

## 4. How this CI maps to the hardware architecture

```text
 Hardware (off-path DPU)              This CI
 -------------------------            -------------------------
 Host kube-apiserver                  Kind cluster odse-host
   kubelet + host ovnkube               control-plane + worker
   SR-IOV VF in the pod                 TODO: veth end A (fake VF)
 DPU OS kube-apiserver                Kind cluster odse-dpu
   DPU kubelet + ovn-controller         control-plane + worker
   representor on br-int                TODO: veth end B (fake repr)
 PCIe VF channel                      Docker network + veth (no IOMMU)
 Shared OVN NB/SB                     TODO: how DB is shared across
                                      two Kind clusters (not designed yet)
```

Same **roles** as overview §11; different **transport**. Pairing in CI
uses Kind node names (`ODSE_HOST_NODE` / `ODSE_DPU_NODE` placeholders).
Hardware pairing uses DPU serial / PF PCI and
`kubernetes/dpu-operator`.

Bring-up order stays analogous:

```text
create-host-cluster.sh
create-dpu-cluster.sh
# TODO: apply pairing labels
# TODO: helm -f kubernetes/ovn-kubernetes/dpu-cluster/values.yaml \
#              -f kubernetes/ovn-kubernetes/simulated/values.yaml
# TODO: helm -f kubernetes/ovn-kubernetes/host-cluster/values.yaml \
#              -f kubernetes/ovn-kubernetes/simulated/values.yaml
# TODO: never pass kubernetes/ovn-kubernetes/hardware/values.yaml
```

Teardown: `delete-clusters.sh` only. It will not `kind delete clusters
--all`.

---

## Prerequisites

- Docker (or compatible) with permission to run Kind
- [`kind`](https://kind.sigs.k8s.io/) and `kubectl` on `PATH`
- Linux recommended (`netdevsim` TODO is Linux-only)

Windows/macOS can create the Kind clusters but cannot complete the
netdevsim TODO on the host kernel.

## Usage

From the repository root (or this directory):

```bash
cd ci/kind-simulated-dpu
./create-host-cluster.sh
./create-dpu-cluster.sh
kubectl --context kind-odse-host get nodes
kubectl --context kind-odse-dpu get nodes
./delete-clusters.sh
```

Environment (optional):

| Variable | Default | Meaning |
|---|---|---|
| `ODSE_HOST_CLUSTER` | `odse-host` | Kind cluster name (host role) |
| `ODSE_DPU_CLUSTER` | `odse-dpu` | Kind cluster name (DPU role) |
| `ODSE_KIND_IMAGE` | unset | Optional `kindest/node` image pin |
| `ODSE_CONFIRM_DELETE` | unset | Set to `yes` to skip the delete prompt when stdin is a TTY |

## Known limitations

- No OVN-Kubernetes install (Helm values are comments only).
- No fake VF/representor yet (TODOs in the create scripts).
- Two clusters do not yet share an OVN database.
- Not a GitHub Actions workflow file; wire these scripts from
  `.github/workflows` later.
