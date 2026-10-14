# Marvell OCTEON enablement (ODSE)

Vendor track for **Marvell OCTEON** DPU/smart NIC class devices used
with the **DAO (Data Plane Accelerator)** / DPDK datapath. ODSE does
**not** assume this is the same as Linux kernel `switchdev` + TC flower
(the NVIDIA baseline).

This is **not** a certification and **not** a Marvell SDK manual.
Anything that changes with OCTEON SDK / DAO / DPDK version is a
**placeholder**. Look up procedures in Marvell docs for
**`<DAO_VERSION>`** / **`<OCTEON_SDK_VERSION>`**.

Matrix ([`vendor-capability-matrix.md`](vendor-capability-matrix.md)):

| Area | Rating |
|---|---|
| SR-IOV VFs, DPDK representors, OVS HW offload, DAO OVS, DPDK/`rte_flow`, telemetry | **likely** (named in Marvell DAO material; unvalidated here) |
| Kernel `switchdev` interoperability | **discovery-required** (open question — resolve early) |
| vDPA, VFIO, OVN, Redfish, Management API, OPI | **discovery-required** |
| OVN-Kubernetes DPU mode, OpenStack off-path | **future** |
| Security isolation | **discovery-required** (all vendors) |

Ansible:
[`ansible/roles/vendor_marvell_octeon/README.md`](../../ansible/roles/vendor_marvell_octeon/README.md).

Adapter stub: `kubernetes/opi/adapter/vendor_marvell.py`.

Implementation (DPDK representors, DAO OVS, first-packet vs hardware
path, validation):
[`marvell-octeon-implementation.md`](marvell-octeon-implementation.md).

---

## Generic OVS vs DAO (read this first)

Keep these layers distinct in lab notes, Ansible, and tests. Mixing
them produces false “offload” conclusions.

### Generic OVS / OVN (vendor-neutral)

These concepts are **the same** as on any Linux host:

- `ovs-vswitchd` + OVSDB; optional `ovn-controller`.
- Logical vs physical: OVN programs OpenFlow; OVS has a **slow path**
  (first packets, `upcall`) and a **datapath** cache.
- `ovs-appctl ofproto/trace` and `dpctl/dump-flows` observe **OVS**,
  not Marvell silicon.
- `other_config:hw-offload=true` is an **OVS** switch-wide hint that a
  **TC or vendor** offload provider may consume. It does not by itself
  program OCTEON.
- A “representor” in ODSE docs is an **attach point** the control plane
  binds to a bridge. In kernel switchdev that is a **netdev**. On
  OCTEON it may be a **DPDK port** with no `devlink flavour pcivf`.

ODSE tools: `ansible/roles/ovs_setup`, `ansible/roles/ovn_setup`,
`tools/offload-checker` (markers `offloaded:yes` / `dp:tc` / TC
`in_hw` are **kernel/TC-oriented**; they may stay `unknown` on DAO).

### DAO-specific (Marvell)

These are **not** generic OVS:

- **DAO** — Marvell datapath accelerator stack (packages, hugepages,
  cores, firmware) pinned as `<DAO_VERSION>`.
- **DPDK representors** — poll-mode ports representing VFs/PFs inside
  a DPDK process, not necessarily `/sys/class/net`.
- **DAO OVS offload application** — vendor process that may *look*
  like OVS (or sit beside it) and install **hardware** forwarding via
  **`rte_flow`** (or a DAO wrapper around it).
- **Post-offload path** — after classification, packets should **not**
  hairpin through the OVS userspace slow path; they should hit OCTEON
  forwarding. Proving that needs DAO/DPDK counters, not only
  `dpctl/dump-flows`.

If a lab only runs distro `openvswitch` with kernel datapath and never
starts DAO, it is **not** the Marvell track — it is generic OVS on
whatever netdevs the host has.

---

## Placeholders

| Token | Meaning |
|---|---|
| `<OCTEON_SKU>` | Exact SoC/card (e.g. family + board) |
| `<OCTEON_SDK_VERSION>` | SDK train |
| `<DAO_VERSION>` | DAO / offload-app train |
| `<DPDK_VERSION>` | DPDK paired with that DAO (do not mix) |
| `<PF_HOST>` / `<VF_PCI>` | Host-visible functions if SR-IOV exists |
| `<DPDK_REPR>` | DPDK representor port id/name inside the DAO process |
| `<DAO_OVS_APP>` | Vendor OVS-offload binary/service name |
| `<RTE_FLOW_DUMP_CMD>` | Vendor-documented `rte_flow` / DAO flow dump |
| `<MARVELL_DAO_DOC_URL>` | DAO guide for `<DAO_VERSION>` |
| `<MARVELL_REPR_DOC_URL>` | DPDK representor model page |

---

## 1. Target hardware assumptions

- One OCTEON-class DPU/NIC listed in Marvell DAO support for
  `<DAO_VERSION>`. Host is Linux + KVM (`host_base`).
- **discovery-required:** whether a separate DPU OS (second kernel)
  exists vs a host-driven DPDK app on x86 + OCTEON offload engine.
- Host PF driver names (`octeontx2-*`, `octeon_ep`, `rvu_nicvf`, …)
  are **hints** in `dpu_inventory.py`, not SKU proof. Record
  `<OCTEON_SKU>` from vendor inventory.

Out of scope until noted: OVN-Kubernetes DPU two-cluster mode
(**future**), OpenStack off-path (**future**), assuming `devlink
eswitch set mode switchdev` works like BlueField.

---

## 2. OCTEON SDK or DAO environment

**Plan Marvell-1.** Rating: **likely** that a supported SDK/DAO
environment is documented.

Discover from `<MARVELL_DAO_DOC_URL>` (do not freeze shell in git):

- Hugepages, isolated CPUs, IOMMU (`<DAO_HUGEPAGES>`, `<DAO_CPU_MASK>`)
- Firmware load onto OCTEON (`<OCTEON_FW_LOAD>`)
- Environment modules vs packages (`<DAO_INSTALL_METHOD>`)
- Whether DAO **replaces** distro `ovs-vswitchd` or **attaches** to it

Ansible default: **do not** install SDK. Operator supplies
`marvell_octeon_dao_install_cmd` from vendor docs when explicitly
enabled.

**Marvell-2** (host endpoint / MAC connectivity): **likely** named;
**discovery-required** exact MAC/VF trust and spoofcheck sysfs vs DPDK
API.

---

## 3. DPDK representor model

**Plan Marvell-3.** Rating: **likely** (DAO documents DPDK
representors). Kernel switchdev representors: **discovery-required**.

| | Kernel switchdev (generic ODSE) | DPDK representor (DAO) |
|---|---|---|
| Visible as | netdev + `devlink port flavour pcivf` | DPDK port in primary process |
| Mapped by | `vf_to_representor_map.py` | vendor CLI / DAO app (`<DPDK_REPR>`) |
| Attached with | `ovs-vsctl add-port` `type=system` | `type=dpdk` **or** DAO-specific port add |

`vf_to_representor_map.py` returning `representor_netdev: null` can
be **correct** if representors never appear in sysfs. Then a
`tools/topology-discovery` **VendorPlugin** must fill DPDK ids —
TODO; do not teach the core mapper vendor port names.

Confirm VF index ↔ `<DPDK_REPR>` from `<MARVELL_REPR_DOC_URL>`.

---

## 4. DAO OVS offload model

**Plan Marvell-4.** Rating: **likely**.

**Generic OVS:** control plane still OpenFlow/OVSDB (if DAO keeps that
ABI).

**DAO-specific:** `<DAO_OVS_APP>` implements the **fast path** and
talks to silicon. Discover:

- Is it `datapath_type=<DAO_DATAPATH_TYPE>` on a still-standard
  `ovs-vswitchd` (like OVS-DOCA), or a **separate** binary?
- How `hw-offload` maps — OVS key vs DAO config file
  (`<DAO_OFFLOAD_ENABLE>`).
- Failure mode if DAO dies: fail-open vs fail-closed —
  **discovery-required** (trust-boundary).

Do not set `ovs_datapath_type: doca` (NVIDIA). Use
`<DAO_DATAPATH_TYPE>` or leave empty until documented.

---

## 5. VM-to-VM packet flow

**Plan Marvell-5** (VF path host → OCTEON) plus VM-to-VM.

**Generic:** VM-A VF → (host PCIe) → representor-A → switch →
representor-B → VM-B VF. First packets classified by the **virtual
switch control plane**; later packets may be cached/offloaded.

**DAO-specific:** both representors are DPDK ports in `<DAO_OVS_APP>`.
Hairpin VM-to-VM may be entirely in OCTEON after offload (no host
kernel). **discovery-required:** whether VMs use VFIO VF, virtio +
vhost-user, or vendor virtio (`qemu/vdpa` is
**discovery-required** for Marvell).

Lab: two VMs, known IPs, iperf; compare host CPU vs DAO CPU.

---

## 6. VM-to-wire packet flow

**Plan Marvell-8.**

**Generic:** VM → representor → br-int/br-ex → uplink PF → wire.

**DAO-specific:** uplink is a DPDK physical port in the same process.
Offload rules must include **output to wire**, not only hairpin.
Validate both directions (wire-to-VM and VM-to-wire).

Do not use the uplink as the only SSH path.

---

## 7. First-packet OVS classification

**Plan Marvell-6.**

**Generic OVS:** packet misses datapath cache → upcall → userspace
classifier (`ofproto`) → flow install. `ofproto/trace` shows this
**logical** path even when hardware will later take over.

**DAO-specific:** first packet may still hit `<DAO_OVS_APP>` userspace
(DPDK PMD on Arm or host). That is **not** kernel `ovs_dp_process`.
Dump tools: OVS appctl **if** the ABI exists; otherwise vendor
trace (`<DAO_FIRST_PKT_TRACE>`).

Pass: first N packets visible in the **classifier**; not required
that `dp:tc` appear.

---

## 8. Post-offload hardware forwarding

**Plan Marvell-7.**

**Generic:** after install, hits increment on a datapath flow;
`offloaded:yes` if the provider sets it.

**DAO-specific:** proof is **hardware/DAO counters** moving while
**userspace PPS** on the classifier thread drops. `rte_flow` query
shows a hit count (`<RTE_FLOW_DUMP_CMD>`). If only OVS userspace
stats grow, offload is **not** proven.

`ovs_offload_checker.py` may classify `unknown` — use a Marvell
**VendorPlugin** for DAO counters (TODO). Do not fake
`offloaded:yes`.

---

## 9. DPDK rte_flow validation

**Plan Marvell-10.** Rating: **likely**.

`rte_flow` is a **DPDK** API (generic industry), **not** an OVS
feature. DAO **uses** it (or wraps it) to program OCTEON.

Validate (from `<MARVELL_DAO_DOC_URL>`):

- Create/list/destroy a known flow (`<RTE_FLOW_DUMP_CMD>`)
- Match fields vs the OVS/OpenFlow rule that triggered it
- Hits increase under iperf
- Destroy → traffic returns to slow path or drops (document which)

Mismatch OVS rule vs `rte_flow` match = control-plane bug, not “OVS
is broken.”

---

## 10. OVS/OVN integration options

**Plan Marvell-9.** Rating: **discovery-required** for OVN;
Kubernetes/OpenStack DPU modes **future**.

Options (pick after lab; do not implement all):

| Option | Generic OVS part | DAO part |
|---|---|---|
| A. Distro OVS kernel + OCTEON unused | Full generic | None — not this track |
| B. Distro OVSDB/OpenFlow + DAO datapath | `ovs-vsctl`, OVN | `<DAO_DATAPATH_TYPE>` / sidecar |
| C. DAO app replaces vswitchd | OVN may still work if OpenFlow remains | Entire datapath proprietary |
| D. No OVS, DAO-only classifier | None | Out of ODSE baseline |

Record `<MARVELL_OVS_INTEGRATION>` = `B` \| `C` \| `unknown`.

OVN: `ovn-controller` chassis on whoever runs the datapath. Two
kube-apiservers (OVN-K8s DPU mode) are **future** — do not copy
`kubernetes/ovn-kubernetes/hardware` NVIDIA keys.

vhost-user vs `/dev/vhost-vdpa-*`: see
`qemu/vdpa/vhost-vdpa.md` §10 Marvell placeholders.

---

## 11. Telemetry collection

**Plan Marvell-12.** Rating: **likely** that DAO/DPDK counters exist.

**Generic:** `TelemetrySample` envelope; `ethtool -S` if a kernel
netdev exists; offload-checker JSON.

**DAO-specific:** PMD stats, `rte_flow` hits, DAO HW counters —
names from vendor docs (`<DAO_COUNTER_NAMES>`). Plugin on
`tools/offload-checker` / `vendor_marvell.py` `collect_telemetry`.
Omit missing metrics (OPI-6). No synthetic zeros.

---

## 12. Performance tests

**Plan Marvell-11.** Home (planned): `tests/performance/` +
`tests/vendor/marvell/`.

Compare on the **same** VMs/frames:

1. Generic software OVS (if a kernel/veth baseline exists)
2. DAO path **before** claiming offload (first-packet / flow miss)
3. DAO path **after** `rte_flow` hits (post-offload)

Metrics: throughput, PPS, guest CPU, **host** CPU, **DAO/OCTEON**
CPU if exposed. VM-to-VM **and** VM-to-wire (§5–6).

Kind simulation cannot run this. Do not publish numbers without
`<DAO_VERSION>` / `<OCTEON_SKU>` in the result file.

---

## 13. Known risks and TODOs

- **False kernel path:** running `switchdev_setup` on OCTEON because
  NVIDIA does — may no-op or break the PF. Probe `devlink` first;
  if absent, stay on DPDK representors.
- **OVS ABI fork:** option C (replace vswitchd) breaks
  `ovs_setup` / offload-checker assumptions.
- **Checker blindness:** `unknown` flows ≠ failure.
- **SDK/DPDK skew:** mixing `<DPDK_VERSION>` and `<DAO_VERSION>` is a
  common brick.
- **Control plane gap:** OVN-K8s / OpenStack **future**; do not block
  datapath labs on Helm.
- **vDPA/VFIO** unproven; guest driver story **discovery-required**.
- **Trust:** DAO crash behaviour undocumented here.

TODOs:

1. Pin SDK/DAO vars; install only on explicit boolean.
2. Topology plugin for DPDK representor ids.
3. Offload-checker Marvell plugin (`rte_flow` / DAO stats).
4. Implement `MarvellOcteonBackend.list_devices()`.
5. Answer switchdev interoperability **before** sharing NVIDIA OVS
   playbooks.
6. `tests/vendor/marvell/` for first-packet vs post-offload vs
   rte_flow.
7. Do not raise matrix `switchdev` above discovery-required without
   `devlink` evidence.
