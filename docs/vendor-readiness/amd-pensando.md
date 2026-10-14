# AMD Pensando DSC enablement (ODSE)

Vendor track for **AMD Pensando Distributed Services Card (DSC)**
hardware. This track is **discovery-first** (`open-source-vdse-equivalent-plan.md`
§7.4, Track E-style sequencing after NVIDIA/Intel/Marvell). Almost
every matrix row is **`discovery-required`**. That is the default, not
an insult to the product.

**Do not assume an OVS hardware-offload path exists.** AMD-6 in the
plan is *if representors exist, then attach and test*. AMD-7 is the
**fallback**: vendor APIs or OPI Network API — a contingency, not a
hidden second “supported” datapath.

This file is **not** a certification and **not** a Pensando/PSM
manual. Do not copy Naples-era blog commands into git. Pin
`<DSC_SKU>` / `<PENSANDO_DOC_URL>` from current AMD/Pensando docs for
**this** card and firmware.

Matrix:
[`vendor-capability-matrix.md`](vendor-capability-matrix.md) (AMD
column). Ansible:
[`ansible/roles/vendor_amd_pensando/README.md`](../../ansible/roles/vendor_amd_pensando/README.md).
Adapter stub: `kubernetes/opi/adapter/vendor_amd.py`.

Implementation (ionic discovery, RDMA optional, gap template):
[`amd-pensando-implementation.md`](amd-pensando-implementation.md).

What *is* reasonably evidenced without a DSC in this repo: the
in-tree Linux **`ionic`** Ethernet driver exists. That proves **host
NIC-class ethernet**, not DPU management, SR-IOV, switchdev, or OVS
offload.

---

## How to read ratings here

| Tag | Meaning |
|---|---|
| **in-tree / documented** | Mainline kernel or clearly named public driver (still not ODSE-certified) |
| **discovery-required** | Verify *whether* it exists on `<DSC_SKU>` + firmware; do not encode as true |
| **contingency** | Only if a prior discovery step failed (AMD-7) |
| **out of scope** | Not this milestone (e.g. copying NVIDIA playbooks) |

Every “pass” below is a **lab record** (`true` / `false` / `null` +
evidence command output), never a default `true` in Ansible.

---

## Placeholders

| Token | Meaning |
|---|---|
| `<DSC_SKU>` | Exact DSC / Naples-class / later SKU string |
| `<FW_VERSION>` | DSC firmware |
| `<HOST_OS>` | Hypervisor kernel (must match ionic support) |
| `<IONIC_MODULE>` | Usually `ionic`; confirm `modinfo` |
| `<IONIC_RDMA_MODULE>` | Often `ionic_rdma`; **may be absent** |
| `<PF_HOST>` | Host netdev bound to ionic |
| `<DSC_MGMT>` | DSC-side SSH / REST / PSM / BMC — **unknown until §5** |
| `<PENSANDO_DOC_URL>` | Vendor guide for this SKU |
| `<PENSANDO_SRIOV>` | `exposed` \| `not-exposed` \| `unknown` |
| `<PENSANDO_REPR>` | `kernel-switchdev` \| `vendor-api` \| `none` \| `unknown` |

---

## 1. Target hardware assumptions

Working assumption: one Pensando **DSC** in a Linux host that can load
`ionic`. Override if the lab is a SmartNIC-only or PSM-managed fleet.

**discovery-required** (do not assume):

- A programmable “DPU OS” on the card you can Ansible like BlueField
  Arm.
- Separate IMC/Redfish like Intel IPU.
- Same PCI VF/representor model as `mlx5`.

Confirm identity **before** any mutate: `lspci` vendor `1dd8`
(Pensando Systems) plus `<DSC_SKU>` from vendor inventory. `ionic`
without `1dd8` is not this track.

Out of scope until §5–7 complete: `ovs_setup` hw-offload, OVN-K8s DPU
mode, OpenStack off-path.

---

## 2. ionic Ethernet driver discovery

**Plan AMD-1.** Status: **in-tree / documented** for Ethernet; ODSE
still must **verify on this host**.

Record:

```text
modinfo <IONIC_MODULE>
ethtool -i <PF_HOST>     # driver should be ionic (or documented successor)
dmesg / journal for ionic probe
```

Pass: PF netdev exists, link/admin state documented, `ethtool -i`
driver name recorded. **Does not** pass SR-IOV, RDMA, or offload.

If `ionic` is missing: install/verify distro kernel module or vendor
package from `<PENSANDO_DOC_URL>` — command is a lab placeholder
(`<IONIC_INSTALL>`), not a git-hardcoded URL.

---

## 3. ionic_rdma discovery

**Plan AMD-8** (partial). Status: **discovery-required**.

`ionic_rdma` (or successor) may not load on all SKUs/firmware/kernels.

Record: `modinfo ionic_rdma` (or `<IONIC_RDMA_MODULE>`), `ibv_devinfo`
if `libibverbs` exists, whether `rdma link` shows a device.

If the module is absent: **RDMA/RoCE is not available** for this lab
(`null`), not “try OVS instead.” Do not load random `rdma_cm` success
as Pensando hardware offload.

---

## 4. Host-side device enumeration

**Plan AMD-2.** Status: **discovery-required** beyond “a netdev
exists.”

Use `tools/inventory/dpu_inventory.py` (PCI `1dd8`, driver `ionic`).
Also: `lspci -nn -d 1dd8:`, `/sys/class/net/<PF_HOST>/device`.

Record: PF PCI, NUMA, MAC, `sriov_totalvfs` **if the file exists**.
Missing `sriov_totalvfs` ⇒ SR-IOV **not exposed in sysfs** (feeds
§6). Do not create the file.

Vendor hint confidence: PCI `1dd8` is **high**; driver `ionic` is
**medium**. Neither implies DSC management.

---

## 5. DSC-side management path discovery

**Plan AMD-3.** Status: **discovery-required**. This is the largest
unknown vs NVIDIA (rshim/BFB SSH) and Intel (Redfish IMC).

Questions to answer in the lab log (yes/no/null + evidence):

- Can you reach a **DSC CPU/OS** (SSH, serial, vendor CLI)?
- Is there **PSM / cloud controller** instead of on-card SSH?
- Redfish/BMC? (`<DSC_MGMT>` protocol)
- Does management share the experiment PF? (unsafe)

If **no** management path: stop before firmware writes. Host `ionic`
alone is not a DPU track. Ansible `dpu_base` does not apply until a
target OS exists.

Do not assume Pensando “always has a DPU OS like BlueField.”

---

## 6. SR-IOV VF discovery

**Plan AMD-4.** Status: **discovery-required** — *verify whether*
exposed.

Checks (sysfs first, vendor API only if sysfs is silent):

- `/sys/class/net/<PF_HOST>/device/sriov_totalvfs`
- `sriov_numvfs` (read-only until a human sets
  `amd_pensando_allow_sriov: true`)
- `virtfn*` PCI addresses

If totalvfs is missing or zero: `<PENSANDO_SRIOV>=not-exposed`.
**Do not** run `sriov_setup` as if it were NVIDIA. Vendor firmware
knobs (`<PENSANDO_SRIOV_FW_CMD>`) only from `<PENSANDO_DOC_URL>` when
that doc exists for this SKU.

---

## 7. Representor and switchdev discovery

**Plan AMD-5.** Status: **discovery-required**.

Checks:

- `devlink dev show` / `devlink port show` for this PCI
- `flavour pcivf` after VFs exist
- `vf_to_representor_map.py --pf <PF_HOST>`

Outcomes:

| Result | `<PENSANDO_REPR>` | Next |
|---|---|---|
| `devlink` pcivf + netdev | `kernel-switchdev` | AMD-6 *may* proceed |
| VFs but no representor | `none` or vendor-specific | AMD-7 contingency |
| No VFs | `unknown` | finish §6 first |
| Vendor-only port objects | `vendor-api` | AMD-7 |

**Do not** copy `switchdev_setup` defaults from BlueField. If
`devlink eswitch` is unsupported, that is a valid discovery
(`switchdevSupported: false` in OPI).

---

## 8. OVS hardware offload feasibility

**Plan AMD-6.** Status: **discovery-required** and **conditional**.

**Gate:** `<PENSANDO_REPR>=kernel-switchdev` (or a documented vendor
offload equivalent). Otherwise **skip this section**. There is **no**
default OVS-TC path.

If the gate passes:

- `ovs_setup` with representor as `system` port
- `ovs_enable_hw_offload: true` only if vendor docs say TC offload
  exists for `ionic` on this FW
- `ovs_offload_checker.py` — `unknown` is allowed; `offloaded:yes`
  is the only strong pass

If the gate fails: **do not** set hw-offload. Go to §12 (AMD-7).
Claiming “Pensando OVS offload” without representors + checker or
vendor doc is a process failure.

---

## 9. RDMA/RoCE validation

**Plan AMD-8.** Status: **discovery-required**; depends on §3.

If `ionic_rdma` present: pair two ports/VFs, `ibv_devinfo`, a
**documented** RDMA ping/bandwidth tool from vendor or distro
(`<RDMA_TEST_CMD>`). Record GID/RoCE version.

If absent: mark **not applicable** for this SKU. RDMA success is
**not** OVS offload and **not** VF representor proof.

---

## 10. VFIO passthrough validation

**Plan AMD-9.** Status: **discovery-required** (matrix VFIO:
discovery-required).

Gate: VFs exist (§6) and IOMMU groups are usable
(`qemu/vfio/vf-passthrough.md`). Guest should see **ionic** (or
vendor VF id), not virtio, unless you chose a different backend.

If no VFs: skip. If VFs exist but no representor: passthrough may
still work as a **plain NIC VF** without ODSE eswitch offload —
record that distinction; it is **not** a vDSE-equivalent datapath.

vDPA: **discovery-required** (do not follow NVIDIA `mlx5_vdpa`
steps).

---

## 11. OVN integration feasibility

**Plan AMD-10.** Status: **discovery-required**. OVN-Kubernetes DPU
mode and OpenStack off-path are **discovery-required** / not “likely”
for AMD in the matrix.

Feasible **only if** some attach point exists (kernel representor,
vendor port, or OPI NetworkAttachment). Otherwise OVN has nothing to
bind.

Do not helm-install `kubernetes/ovn-kubernetes/hardware` NVIDIA or
Intel blocks onto Pensando.

---

## 12. OPI Network API candidate path

**Plan AMD-7.** Status: **contingency** + OPI alignment
**discovery-required**.

Use when representors/switchdev are **not** exposed:

```text
Control plane → kubernetes/opi NetworkAttachment / NetworkFunction
             → AmdPensandoBackend
             → vendor API / PSM / undocumented DSC pipeline
```

This path is **not** implemented (`UnsupportedOperation`). It is the
**documented fallback**, not a promise that a Pensando Network API
matches `odse.opi/v1alpha1`.

Lab: write a mapping table (ODSE field → vendor call) or explicitly
`unsupported`. Do not pretend `ovs-vsctl add-port` against a missing
netdev.

---

## 13. Telemetry path discovery

**Plan AMD-11.** Status: **discovery-required**.

Probe in order:

1. `ethtool -S <PF_HOST>` (generic checker)
2. `devlink` health/port stats if present
3. DSC-side / PSM telemetry if §5 succeeded
4. OPI `TelemetrySample` via adapter — TODO

Missing source ⇒ omit metrics (OPI-6). `ionic` stats ≠ hardware
flow offload counters.

---

## 14. Gap analysis against NVIDIA, Intel, and Marvell

| Topic | NVIDIA BlueField | Intel IPU E2100 | Marvell OCTEON | AMD Pensando (this track) |
|---|---|---|---|---|
| Public datapath story | Kernel switchdev + OVS-TC (`supported`); OVS-DOCA `likely` | Datapath **discovery-required**; OPI/OpenShift **likely** | DAO + DPDK representors + `rte_flow` **likely**; kernel switchdev **discovery-required** | **ionic Ethernet only** well evidenced; rest **discovery-required** |
| Management | rshim / DPU OS **supported**/documented | Redfish IMC **likely** | SDK/DAO env **likely**; Redfish **discovery-required** | DSC OS / PSM / Redfish **all discovery-required** |
| Representors | Kernel `pcivf` **supported** | Owner IPU vs host **discovery-required** | DPDK repr **likely** | **Verify whether any exist** |
| OVS offload | Baseline yes (kernel) | Do not assume | DAO, not TC | **Do not assume** |
| K8s DPU mode | **likely** | **likely** | **future** | **discovery-required** |
| OPI | wrap DOCA later | flagship **likely** | candidate **discovery-required** | **fallback if no representors** |
| First ODSE move | `switchdev` + `ovs_setup` | Redfish + operator | DAO install + rte_flow | **AMD-1…5 only** |

Pensando is **not** “NVIDIA but `ionic`.” Copying BlueField Ansible
order is the main process risk.

---

## 15. Known risks and TODOs

- **False offload:** enabling `ovs_enable_hw_offload` without
  representors or vendor TC support.
- **Wrong device class:** any `1dd8` or `ionic` host NIC treated as a
  full DSC DPU.
- **Management void:** no SSH/API to the card ⇒ no firmware, no OPI,
  no DPU cluster.
- **SR-IOV surprise:** sysfs missing ⇒ firmware-only VF create —
  undocumented here.
- **RDMA ≠ vDSE:** RoCE passing does not unlock OVN-K8s.
- **OPI vapor:** AMD-7 without a vendor API mapping table is a
  document, not a product.

TODOs (`vendor_amd_pensando`):

1. Read-only inventory play: ionic, PCI `1dd8`, sriov sysfs,
   `devlink`, management probe — all facts, no writes.
2. Booleans default false: `amd_pensando_allow_sriov`,
   `amd_pensando_try_ovs_offload` (gated on representor fact).
3. Implement `AmdPensandoBackend.list_devices()` from inventory JSON.
4. If §7 = none: draft OPI mapping (AMD-7) instead of OVS tasks.
5. `tests/vendor/amd/` for AMD-1…5 evidence files.
6. Do not raise matrix rows without those tests.
