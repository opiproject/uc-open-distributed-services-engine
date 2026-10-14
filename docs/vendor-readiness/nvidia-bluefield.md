# NVIDIA BlueField enablement (ODSE)

Vendor track for **NVIDIA BlueField-2/3** (and, where the same `mlx5`
eswitch model applies, ConnectX used as an on-path NIC). This is ODSE’s
**first** hardware track (`open-source-vdse-equivalent-plan.md` Track B).
It is **not** a certification and **not** a restatement of any one DOCA
release.

DOCA Switching / OVS-DOCA / DOCA Platform are the **NVIDIA datapath and
provisioning layer**. ODSE is the multi-vendor control plane that calls
that layer through `ansible/roles/vendor_nvidia_bluefield` and
`kubernetes/opi/adapter/vendor_nvidia.py`. See
[`docs/openDSE.md`](../openDSE.md) for that split.

**Do not copy commands from an old blog or from this file into
production.** Anything that changes with DOCA, BFB, firmware, or
`MLNX_OFED` / `doca-ofed` packaging is a **placeholder**. Look up the
procedure in NVIDIA docs for **`<DOCA_VERSION>`** and record the URL in
the lab log.

Matrix ratings (this project, not NVIDIA marketing):
[`vendor-capability-matrix.md`](vendor-capability-matrix.md) — SR-IOV,
switchdev, representors, kernel OVS offload, DPDK, VFIO: `supported` in
the Linux/ecosystem sense; OVS-DOCA and vDPA: `likely`; OVN-Kubernetes
DPU mode and OpenStack off-path: `likely`, unvalidated here.

Implementation sequence (discover → switchdev → OVS-TC → optional OVS-DOCA):
[`nvidia-bluefield-implementation.md`](nvidia-bluefield-implementation.md).

Ansible role:
[`ansible/roles/vendor_nvidia_bluefield/README.md`](../../ansible/roles/vendor_nvidia_bluefield/README.md).

---

## Placeholders (fill per lab)

| Token | Meaning |
|---|---|
| `<BF_MODEL>` | e.g. BlueField-2, BlueField-3 |
| `<DOCA_VERSION>` | Pinned DOCA/BFB train for this lab |
| `<BFB_IMAGE>` | BFB filename/URL from NVIDIA for that train |
| `<FW_VERSION>` | NIC/DPU firmware reported by vendor tools |
| `<HOST_OS>` / `<DPU_OS>` | Distro+kernel on x86 host vs Arm DPU |
| `<RSHIM_DEV>` | rshim device node / BMC / OOB address |
| `<PF_HOST>` | Host-visible PF netdev (often **legacy** in DPU mode) |
| `<PF_ARM>` | Arm-side PF netdev (often **switchdev** in DPU mode) |
| `<VF_INDEX>` / `<VF_PCI>` / `<REPR>` | As in `qemu/vfio/vf-passthrough.md` |
| `<NVIDIA_DOCA_SWITCHING_URL>` | Switching/eswitch page for `<DOCA_VERSION>` |
| `<NVIDIA_OVS_DOCA_URL>` | OVS-DOCA / OVN-Kubernetes offload page |
| `<NVIDIA_DPU_MODE_URL>` | DPU vs NIC mode, host PF policy |

---

## 1. Target hardware assumptions

Assumed for this workstream (override in lab notes if different):

- One BlueField card in **DPU (embedded) mode**, not “NIC-only” mode,
  unless the lab is explicitly an on-path ConnectX experiment.
- Host is a Linux KVM/libvirt node (`ansible/roles/host_base`).
- Card appears as `mlx5` PFs on the **host** and a separate **Arm
  complex** with its own OS (`<DPU_OS>`).
- Management: rshim and/or BMC/Redfish at `<RSHIM_DEV>` — **discover
  which path `<DOCA_VERSION>` documents**; do not assume SSH-on-Arm is
  already up.

Out of scope until recorded: multi-host LAG, socket-direct, SNAP
storage, GPU-direct. ConnectX-only boxes may reuse kernel SR-IOV /
switchdev / VFIO sections; they do **not** get a DPU cluster or BFB
install.

Confirm before any Ansible mutate:

```text
<BF_MODEL>, <FW_VERSION>, <DOCA_VERSION>, DPU vs NIC mode
```

from vendor inventory tools named in `<NVIDIA_DPU_MODE_URL>` (names of
those tools change; do not freeze `mlxconfig` flags here).

---

## 2. DPU OS and management assumptions

- Arm OS comes from a **BFB** (`<BFB_IMAGE>`) matching `<DOCA_VERSION>`.
  Install/upgrade steps: vendor BFB chapter — not this repo.
- After boot, ODSE expects SSH (or equivalent) to `<DPU_OS>` so
  `ansible/roles/dpu_base` then this vendor role can run.
- **DPU mode vs NIC mode** changes who is allowed to set eswitch mode.
  Public DOCA Switching material has stated that in **DPU mode**,
  switchdev is set **on the Arm PFs**, must **not** be flipped casually,
  and the **host must not** put host PFs in switchdev (host stays
  **legacy**). **Re-read `<NVIDIA_DOCA_SWITCHING_URL>` for your train**
  before encoding that in Ansible. If your card is in NIC mode, the
  host *does* own switchdev — different playbook path.
- Time sync, packages (`iproute2`, `ethtool`, OVS) on Arm: `dpu_base`.
- OPI adapter: `opi_services_backend: nvidia` once
  `vendor_nvidia.py` implements `list_devices()`.

---

## 3. SR-IOV setup

**Prefer** [`ansible/roles/sriov_setup`](../../ansible/roles/sriov_setup/README.md)
on the OS that is allowed to create VFs for that PF.

Discover from vendor docs for `<DOCA_VERSION>`:

- Whether VFs are created on **host PF**, **Arm PF**, or both.
- Max VFs (`sriov_totalvfs`) and whether firmware `NUM_OF_VFS` (or
  successor) must be set **before** OS `sriov_numvfs`.
- Whether VF creation requires a **firmware reset / host reboot**.

Do **not** paste `mlxconfig -d … NUM_OF_VFS=…` here; the device
selector and key names move. Record the exact command from NVIDIA
firmware tools docs in the lab log as `<NVIDIA_SRIOV_FW_CMD>`.

Then apply OS VF count via `sriov_setup` (`pf_name: <PF_…>`,
`num_vfs: <N>`). Respect that role’s disruptive-reconfigure gate.

---

## 4. switchdev mode

**Prefer** [`ansible/roles/switchdev_setup`](../../ansible/roles/switchdev_setup/README.md)
with `devlink`, not vendor-only wrappers, when `devlink dev eswitch`
works.

Discover:

- Which PCI handle is the eswitch (`devlink dev show`) — host vs Arm.
- Required VF unbind before mode change (`require_vfs_unbound`).
- Whether DPU mode **forbids** host-side `eswitch set` (see §2).

If `devlink` is insufficient on that BFB, document the vendor command
from `<NVIDIA_DOCA_SWITCHING_URL>` as `<NVIDIA_ESWITCH_CMD>` and call it
only from this vendor role’s hook list (`switchdev_setup_vendor_hooks`),
not from the generic role’s core.

---

## 5. Representor discovery

Do **not** hardcode `eth0v0` / `pf0vf1` names.

1. `devlink port show` — `flavour pcivf`, `vfnum` == `<VF_INDEX>`.
2. `python3 tools/topology-discovery/vf_to_representor_map.py --pf <PF_ARM>`
3. `phys_port_name` as fallback only (parser in that tool).

SFs (Scalable Functions) are **NVIDIA-specific**. Core ODSE mapper does
not treat `pcisf` as a VF. If the lab uses SFs, implement the
`NvidiaSubfunctionPlugin` scaffold at
`tools/topology-discovery/vendor_plugins/nvidia_sf.py` (currently a
documented no-op — see its module docstring for the remaining TODOs).

---

## 6. OVS-Kernel offload path (baseline)

**This is the first datapath ODSE will validate on BlueField.**

On the **eswitch owner** (Arm in DPU mode):

- `ansible/roles/ovs_setup` with `ovs_enable_hw_offload: true`,
  `ovs_datapath_type` empty or `system`, representor
  `interface_type: system`.
- Traffic: VM/pod VF on host → representor on Arm → `br-int`.
- Observe: `tools/offload-checker/ovs_offload_checker.py`
  (`offloaded:yes`, `dp:tc`, TC `in_hw`).

Kernel TC-flower offload via `mlx5` is matrix **`supported`**. Still
confirm with `<DOCA_VERSION>` that `other_config:hw-offload=true` is the
documented enablement (OVS version on the BFB matters).

---

## 7. OVS-DPDK path

Userspace datapath (`datapath_type: netdev`, `interface_type: dpdk`,
`options.dpdk-devargs`). See `ansible/roles/ovs_setup` README
(userspace example).

Discover from NVIDIA/DPDK docs for the **same** OFED/DOCA train:

- Whether representors are DPDK ports (`representor=[…]` syntax) or
  still kernel representors.
- Hugepages, CPU isolation on **Arm vs host**.
- PMD: `mlx5` (in-tree DPDK) vs a DOCA-bundled DPDK — **do not mix
  undocumented**.

ODSE treats this as optional. Do not enable in Kind simulation.

---

## 8. OVS-DOCA path

Vendor OVS backend (matrix **`likely`**). OVS control plane unchanged;
datapath uses **DOCA Flow** instead of kernel TC or classic DPDK
`rte_flow`. Upstream overview (version-agnostic pointer):
OVN-Kubernetes “OVS-DOCA” feature docs — pin
`<NVIDIA_OVS_DOCA_URL>` to the page that matches `<DOCA_VERSION>`.

Discover:

- Package set that replaces distro OVS (`<OVS_DOCA_PACKAGES>`).
- Bridge `datapath_type` string (`<OVS_DOCA_DATAPATH_TYPE>` — often
  documented as `doca`; **confirm**, do not assume).
- Whether kernel `hw-offload` is N/A or still required.
- Interaction with OVN-Kubernetes DPU mode.

`kubernetes/opi` `OffloadPolicy.datapathType` may carry that string
later. Until then, keep kernel OVS as default in `ovs_setup`.

**For the hands-on "how do I actually get both OVS-Kernel and
OVS-DOCA available (and confirm which is really active) on a real
card" steps this section deliberately excludes**, see
[`../operations/nvidia-bluefield-dpu-bringup.md`](../operations/nvidia-bluefield-dpu-bringup.md)
— a runbook, not a code-scope description like this file.

---

## 9. vDPA path

Matrix **`likely`** (`mlx5_vdpa`). Procedure:
[`qemu/vdpa/vhost-vdpa.md`](../../qemu/vdpa/vhost-vdpa.md).

Discover from `<NVIDIA_DOCA_SWITCHING_URL>` / vDPA chapter:

- Parent mgmtdev name (`vdpa mgmtdev show`) — `<NVIDIA_VDPA_MGMTDEV>`.
- VF vs SF as parent.
- Whether the VF must stay bound to `mlx5_core`.

Guest must see **virtio-net**, not `mlx5_core`. Offload visibility
stays on the **representor**, not in the guest.

---

## 10. VF passthrough path

Matrix **`supported`** for VFIO VF passthrough as a Linux mechanism.
Procedure: [`qemu/vfio/vf-passthrough.md`](../../qemu/vfio/vf-passthrough.md).

In **DPU mode**, the VF is typically created/seen on the **host**
(legacy PF) while the representor lives on **Arm**. Confirm that split
in `<NVIDIA_DOCA_SWITCHING_URL>`. Do not bind the Arm representor to
`vfio-pci`.

Guest loads **mlx5** (or distro `mlx5_core`), not virtio-net.

---

## 11. OVN-Kubernetes DPU mode

Matrix **`likely`**. ODSE layout:
[`kubernetes/ovn-kubernetes/README.md`](../../kubernetes/ovn-kubernetes/README.md).

Hardware overlay placeholders:
`kubernetes/ovn-kubernetes/hardware/values.yaml` (`nvidia:` block).

Discover from NVIDIA + OVN-Kubernetes docs for the chosen chart:

- Host cluster vs DPU (Arm) cluster kube-apiservers.
- SR-IOV device plugin resource name:
  `<NVIDIA_SRIOV_RESOURCE_NAME>`.
- Whether OVS-DOCA is required or kernel OVS is enough.
- Node pairing labels (`<HOST_TO_DPU_ANNOTATION_KEY>`).

Kind (`ci/kind-simulated-dpu`) does **not** prove this path.

---

## 12. OpenStack off-path mode

Matrix **`likely`**. Intended mapping: Nova VF on host, Neutron/OVN
logical port bound to **Arm representor**, `ovn-controller` on DPU
chassis. See `openstack/neutron-ovn-smartnic/` (when filled) and
`docs/architecture/overview.md` §10.1.

Discover from NVIDIA + OpenStack Neutron “smart NIC” / DPU binding
docs for the OpenStack release you pin (`<OPENSTACK_RELEASE>`):

- `binding:vnic_type` / `profile` values (`<NEUTRON_VNIC_TYPE>`).
- Whether OVN chassis is the DPU hostname.
- Nova PCI passthrough whitelist vs SR-IOV.

Unvalidated by ODSE until `tests/` exist for this vendor.

---

## 13. Telemetry and counters

Sources (vendor-neutral first):

- `ethtool -S <PF_ARM>` / representor (checker highlights generic names).
- `ovs-appctl dpctl/dump-flows -m`.
- `devlink` port/trap stats if present.

NVIDIA extras (DOCA telemetry, `mst`, `mlx5` hw counters) belong in
`VendorPlugin` / `vendor_nvidia.py` `collect_telemetry`. **Do not**
put DOCA metric names in core `TelemetrySample` taxonomy.

Never synthesize zeros for missing DOCA metrics (OPI-6).

---

## 14. Validation checklist

Lab record: `<BF_MODEL>` `<DOCA_VERSION>` `<FW_VERSION>` date.

| Step | Pass |
|---|---|
| Inventory | `dpu_inventory.py` sees `15b3` / `mlx5_core`; serial/FW filled or explicitly null |
| Management | SSH/rshim to Arm; `dpu_base` facts |
| SR-IOV | `sriov_numvfs` as requested; VFs in sysfs |
| switchdev | `devlink eswitch` mode matches vendor DPU/NIC policy |
| Representors | `vf_to_representor_map.py` `mapping_method=devlink_pcivf` |
| OVS-kernel | `ovs_setup` + checker `probable_offloaded` after traffic |
| VFIO VM | Guest `lspci` shows mlx5 VF; ping via representor/OVS |
| vDPA VM | Guest `virtio_net` (optional this milestone) |
| OVS-DPDK / OVS-DOCA | Optional; documented `<DATAPATH>` |
| OVN-K8s DPU | Optional; two clusters + VF pod |
| OpenStack off-path | Optional |
| OPI stub | `load_backend('nvidia').list_devices()` still TODO until implemented |
| Trust | OPI/API not on tenant VF network |

---

## 15. Known risks and TODOs

- **DOCA/BFB drift:** flags and package names change every train. Pin
  `<DOCA_VERSION>` per lab; do not “latest”.
- **Wrong mode:** running host `switchdev_setup` in DPU mode can
  violate NVIDIA’s host-legacy rule — confirm docs first.
- **`sriov_numvfs` reset** destroys VFs, VMs, representors (sriov_setup
  safety notes).
- **OVS-DOCA vs distro OVS:** mixing packages yields mysterious
  `datapath_type` errors.
- **vDPA** less proven than VFIO on this track.
- **Security isolation** remains `discovery-required` in the matrix
  even for NVIDIA.
- **Ansible role** is documentation-first: no live `mlxconfig` tasks
  until commands are parameterized from vendor docs.

TODOs for `vendor_nvidia_bluefield`:

1. Variables for `<DOCA_VERSION>`, BFB path, DPU vs NIC mode.
2. Hook-only firmware VF enable (`<NVIDIA_SRIOV_FW_CMD>` from vars).
3. Implement `NvidiaBlueFieldBackend.list_devices()`.
4. Implement the SF plugin logic in `tools/topology-discovery/vendor_plugins/nvidia_sf.py` (scaffold exists; annotation is still a no-op).
5. Hardware regression entry in `ci/hardware-regression` once a card is
   available.
