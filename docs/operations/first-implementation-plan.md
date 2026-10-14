# First implementation plan (ODSE)

A **practical order** to get from an empty idea to a defensible
baseline: docs → inventory → host/SR-IOV/switchdev/OVS → observe
offload → two VM attach methods → NVIDIA lab track → AMD discovery →
Intel/Marvell paperwork.

This is the near-term slice of
[`milestone-backlog.md`](milestone-backlog.md) (roughly **M0–M3 + M7
docs**). It does **not** include OpenStack, OVN-Kubernetes hardware,
or OPI Level 5.

**Hardware is not required to start.** Each step lists:

- **Without hardware** — docs, unit tests, Kind, Ansible `--check`,
  or skip-with-evidence
- **With hardware** — FN tests that actually bind VFs / eswitch

Many paths already have **skeletons in git**. A step is done when
**minimal acceptance** is true, not when a file exists.

Do not copy NVIDIA eswitch/OVS-TC onto AMD/Intel/Marvell. Do not
claim cert Level ≥ 1 from Kind.

---

## Order (do not skip downward without a written skip)

| # | Step | Milestone | Hardware? |
|---|---|---|---|
| 1 | Repo skeleton and architecture docs | M0 | No |
| 2 | Inventory collector | M1 | Optional (any Linux) |
| 3 | `host_base` role | M1 | Optional (a VM is enough) |
| 4 | `sriov_setup` role | M1 | Optional (skip if no `sriov_totalvfs`) |
| 5 | `switchdev_setup` role | M2 | Optional (`devlink` may N/A) |
| 6 | `ovs_setup` role | M2 | Optional (software OVS) |
| 7 | Offload checker | M2 | Optional (unknown markers OK) |
| 8 | VF passthrough example | M3 | Docs-only until IOMMU+VF |
| 9 | vDPA example | M3 | Docs-only until `vdpa mgmtdev` |
| 10 | NVIDIA BlueField backend | M7 | Docs first; lab when a card exists |
| 11 | AMD Pensando discovery | M7 | Docs + `ionic` host if present |
| 12 | Intel and Marvell enablement docs | M7 | Docs-only |

---

## 1. Create repo skeleton and architecture docs

**Why:** Stops four vendor tracks from inventing incompatible
layouts. Encodes “tests are the contract” and non-goals (no NSX
clone, no assumed switchdev).

**Files to create (or keep current):**

- `README.md`, `open-source-vdse-equivalent-plan.md`
- `docs/architecture/{overview,api-model,roles-and-trust-boundary,vdse-to-open-equivalent-mapping}.md`
- `docs/vendor-readiness/vendor-capability-matrix.md`
- Empty-or-stub trees: `ansible/roles/`, `tools/`, `tests/`, `ci/`,
  `qemu/`, `kubernetes/`

**Minimal acceptance:** A new reader can name host vs DPU vs control
plane and find the matrix. No `supported` cell without a cited
reason.

**Follow-up tests:** None (doc review). Later: `ci/lint`.

**Without hardware:** This step is entirely documentation.

---

## 2. Implement inventory collector

**Why:** Day-0 truth. Every later role and OPI `DPUDevice` should
round-trip the same PCI/netdev/devlink/OVS facts. Graceful
degradation proves the tool works on a laptop.

**Files:** `tools/inventory/dpu_inventory.py`, `tools/inventory/README.md`

**Minimal acceptance:** `python3 dpu_inventory.py --pretty` exits 0
on Linux **and** on a machine with no `lspci`/`devlink` (sections
`available: false`). No invented devices. Intel `ice` hint stays
**low** confidence.

**Follow-up tests:** FN-01. Cert **Level 0** on any Linux box
(including a cloud VM with no DPU).

**Without hardware:** Run on the developer workstation / CI image.

---

## 3. Implement `host_base` role

**Why:** One place for virt/IOMMU *checks*, KVM/libvirt/OVS/OVN
packages, optional hugepages. Later roles assume tools exist, not a
particular distro’s package names.

**Files:** `ansible/roles/host_base/` (`tasks/`, `defaults/`, `README.md`)

**Minimal acceptance:** Role runs on a throwaway VM: packages
toggleable; IOMMU/virt **warn** by default, fail only if
`host_base_fail_on_missing_*` is true. README lists distro coverage
gaps.

**Follow-up tests:** Molecule (when added); manual `ansible-playbook`
`--check`. No DPU required.

**Without hardware:** Nested virt may be absent — that is a **warning**,
not a failed milestone if the fail-on flags stay false.

---

## 4. Implement `sriov_setup` role

**Why:** VFs are the host-visible handle for VFIO, vDPA parents, and
device-plugin resources. Unsafe `numvfs` changes take down guests.

**Files:** `ansible/roles/sriov_setup/` (validate PF, `totalvfs`,
`numvfs`, discover VFs, optional persist)

**Minimal acceptance:** Idempotent when `numvfs` already matches.
**Refuses** non-zero→different-non-zero unless
`sriov_setup_allow_disruptive_reconfigure`. README safety notes
present.

**Follow-up tests:** FN-03, FN-04. If `/sys/.../sriov_totalvfs`
missing: **skip** FN-04 and record N/A (valid on a laptop).

**Without hardware:** Unit/logic review + skip evidence. Do not fake
sysfs in CI as a cert Level 1 pass.

---

## 5. Implement `switchdev_setup` role

**Why:** Kernel representors appear after eswitch mode `switchdev`.
Wrong OS (host vs DPU Arm) is a common NVIDIA DPU-mode footgun.

**Files:** `ansible/roles/switchdev_setup/` (`devlink` detect, show,
set, verify `flavour pcivf`, optional unbind, vendor **hooks as
data**)

**Minimal acceptance:** Vendor-neutral tasks (no `if nvidia` in
core). Per-device error if `devlink` cannot set mode. Facts file
written. Fail-fast optional.

**Follow-up tests:** FN-06. Without `devlink eswitch`: skip, do not
fail the vendor.

**Without hardware:** `--check` / mock is enough for the skeleton.
Kind `netdevsim` does **not** equal switchdev (see
`ci/kind-simulated-dpu` README).

---

## 6. Implement `ovs_setup` role

**Why:** Bridges, representor ports, `hw-offload`, `datapath_type`
are the common control for kernel, netdev, and later vendor
datapaths.

**Files:** `ansible/roles/ovs_setup/` + README examples (kernel,
userspace, vendor placeholder)

**Minimal acceptance:** Service check + `ovs-vsctl show` dump.
Idempotent add-br/add-port. Empty `ovs_datapath_type` leaves OVS
default. Does not install packages (`host_base` does).

**Follow-up tests:** FN-07, FN-08 on a VM with distro OVS (software
datapath). FN-08 `true` without representors is **config only**, not
offload proof.

**Without hardware:** Create `br-int`, add a veth, set hw-offload
false/true and read it back.

---

## 7. Implement offload checker

**Why:** Separates “we set the knob” (FN-08) from “flows look
offloaded” (FN-17). Core must stay vendor-neutral; `unknown` is a
valid classification.

**Files:** `tools/offload-checker/ovs_offload_checker.py`, plugins
ABC, README. (Telemetry collector is adjacent, not a substitute.)

**Minimal acceptance:** JSON+text; `dpctl/dump-flows`; optional
`--flow` / `--interface`. Parser unit tests if present. No crash when
OVS/tc/ethtool missing.

**Follow-up tests:** FN-16, FN-17. Without TC `in_hw`: pass with
`unknown` / null ratio — **not** a Level 2 award.

**Without hardware:** Run against software OVS; expect software/unknown
markers.

---

## 8. Implement VF passthrough example

**Why:** VMDirectPath analog: guest vendor NIC, representor stays on
the eswitch. Documents IOMMU bind without a live card.

**Files:** `qemu/vfio/vf-passthrough.md`,
`qemu/libvirt-xml/vf-passthrough.xml.template`

**Minimal acceptance:** Placeholders only (`<VF_PCI>`, …). Ten
sections from the VFIO prompt (prereq through risks). No lab-specific
BDF copied as if universal.

**Follow-up tests:** FN-09, FN-11 **when** IOMMU+VF exist. Until then:
template review; `virsh` dry-define optional.

**Without hardware:** Documentation-only complete for this step.
Do not file cert Level 3.

---

## 9. Implement vDPA example

**Why:** UPTv2 analog: virtio-net + possible migration story. Must
not be confused with VFIO or vhost-user.

**Files:** `qemu/vdpa/vhost-vdpa.md`,
`qemu/libvirt-xml/vhost-vdpa.xml.template`

**Minimal acceptance:** Guest verification says **virtio_net**.
Vendor placeholders for NVIDIA/Intel/AMD/Marvell. Skip path if
`vdpa mgmtdev show` empty.

**Follow-up tests:** FN-10. Empty mgmtdev → skip, not fail.

**Without hardware:** Docs-only. Same as step 8 for certification.

---

## 10. Start NVIDIA BlueField backend

**Why:** First hardware track (plan Track B): public switchdev +
kernel OVS-TC evidence is strongest. Still **not** certified until
M9 reports.

**Files:** `docs/vendor-readiness/nvidia-bluefield.md`,
`ansible/roles/vendor_nvidia_bluefield/README.md` (discover-first;
no frozen DOCA CLI)

**Minimal acceptance:** Placeholders `<DOCA_VERSION>`, DPU vs NIC
mode, host-legacy warning. Role defaults to **no** BFB/firmware
write. Kernel OVS called out as first datapath; OVS-DOCA optional.

**Follow-up tests:** On a card: FN-01–12, FN-17 (Level 2–3).
**Without a card:** doc complete = this step’s implementation-plan
bar; matrix stays as-is (`supported` only where already cited as
ecosystem evidence, not ODSE lab).

**Without hardware:** Enablement **document** is the deliverable.
Do not invent mlxconfig lines.

---

## 11. Start AMD Pensando discovery backend

**Why:** Parallel **discovery** track (plan §7.4). `ionic` is
in-tree; SR-IOV/representors/OVS offload are *verify whether*, not
*confirm that*.

**Files:** `docs/vendor-readiness/amd-pensando.md`,
`ansible/roles/vendor_amd_pensando/README.md`

**Minimal acceptance:** Explicit **no assumed OVS offload**. Gates
for `amd_pensando_allow_sriov` / `try_ovs_offload` default false.
Gap table vs NVIDIA/Intel/Marvell. AMD-7 OPI fallback described.

**Follow-up tests:** FN-01 on any host with `ionic` (or skip). FN-04/05
only if sysfs shows VFs/representors. Never FN-17 as L2 without FN-05.

**Without hardware:** Docs-only is enough to “start” the backend.
If a generic NIC is present, still do not call it a DSC.

---

## 12. Add Intel and Marvell enablement documents

**Why:** Locks sequencing: Intel = OPI/OpenShift-first, datapath
discovery-required; Marvell = DAO/DPDK representors, kernel
switchdev an open question. Prevents copy-paste of steps 5–7.

**Files:**

- `docs/vendor-readiness/intel-ipu-e2100.md`
- `docs/vendor-readiness/marvell-octeon.md`
- `ansible/roles/vendor_intel_ipu/README.md`
- `ansible/roles/vendor_marvell_octeon/README.md`

**Minimal acceptance:** Conservative ratings match the matrix.
Marvell doc splits **generic OVS vs DAO**. Intel: Redfish/IMC;
`ice` ≠ IPU. No hardcoded SDK/Redfish payloads.

**Follow-up tests:** None required for this step. Later labs: Intel
FN-02 Redfish; Marvell FN-17 via `rte_flow` plugin, not `dp:tc`.

**Without hardware:** Documentation-only **is** completion for step
12.

---

## What this plan deliberately leaves out

- M4 OpenStack off-path, M5 hardware OVN-K8s (Kind scripts can
  proceed **in parallel** after step 1; they do not gate 1–12).
- M6 OPI `list_devices()` implementations.
- M8/M9 signed cert reports and perf N≥3.

After step 12, the next hardware-shaped slice is: **one NVIDIA (or
any card that actually has representors) through steps 4–8 and
FN-17**, plus AMD inventory if a DSC appears. Kind remains for
OVN-K8s **control-plane** rehearsal only.
