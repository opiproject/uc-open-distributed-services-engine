# Intel IPU E2100 enablement (ODSE)

Vendor track for **Intel Infrastructure Processing Unit (IPU)** hardware
in the E2100 class (and only that class until a lab says otherwise).
This is ODSE Track C: **second** after NVIDIA, and it is **Kubernetes /
OPI-first**, not OVS-TC-first.

This document is **not** a certification, **not** an Intel product
guide, and **not** a claim that E2100 implements Linux `switchdev` the
same way `mlx5` does. Most datapath rows in
[`vendor-capability-matrix.md`](vendor-capability-matrix.md) for Intel
IPU are **`discovery-required`**. Management/OPI/OVN-Kubernetes DPU mode
are **`likely`** (public OpenShift DPU Operator + OPI-style daemon
material) and still **unvalidated by this project**. OpenStack off-path
is **`future`**. Security isolation is **`discovery-required`** like
every vendor.

**Do not copy firmware or Redfish payloads from blogs into git.** Pin
`<IPU_FW_BUNDLE>` / `<IPU_REDFISH_DOC_URL>` from Intel (or OEM) docs
for the exact SKU.

Ansible role:
[`ansible/roles/vendor_intel_ipu/README.md`](../../ansible/roles/vendor_intel_ipu/README.md).

Adapter stub: `kubernetes/opi/adapter/vendor_intel.py` (raises
`UnsupportedOperation` until `list_devices()` exists).

Implementation order (discovery → Redfish → OPI → DPU Operator, **not**
OVS-DOCA):
[`intel-ipu-implementation.md`](intel-ipu-implementation.md).

---

## Status legend used below

| Tag | Meaning here |
|---|---|
| **likely** | Named in Intel/Red Hat public IPU/OpenShift material; ODSE has not reproduced it |
| **discovery-required** | Insufficient public datapath detail, SKU-dependent, or untested |
| **future** | Not in Intel’s published IPU workstream as used by ODSE |
| **out of scope** | Explicitly not this track’s first milestone |

`ice` / `idpf` on a host NIC is **not** proof of IPU hardware
(`dpu_inventory.py` keeps that hint at **low** confidence). Require PCI
SKU / Redfish model / serial that matches E2100-class docs.

---

## Placeholders (fill per lab)

| Token | Meaning |
|---|---|
| `<IPU_SKU>` | Exact E2100 (or OEM) product string |
| `<IPU_FW_BUNDLE>` | Vendor firmware package id/version |
| `<IMC_ADDRESS>` | IMC / management-complex reachability (IP/FQDN/USB) |
| `<REDFISH_BASE>` | Redfish root, e.g. `https://<IMC_ADDRESS>/redfish/v1` |
| `<IPU_OS>` | Image on the Arm compute complex (RHEL, MicroShift node, other) |
| `<HOST_OS>` | Hypervisor/OpenShift worker OS |
| `<OPI_DAEMON>` | Process/container name for the IPU-side OPI-style API |
| `<DPU_OPERATOR_VERSION>` | OpenShift DPU Operator (or upstream equivalent) pin |
| `<INTEL_REPRESENTOR_OWNER>` | `ipu-os` \| `host` \| `unknown` |
| `<INTEL_SRIOV_RESOURCE_NAME>` | Device-plugin resource name if any |
| `<IPU_REDFISH_DOC_URL>` | Firmware/Redfish guide for this SKU |
| `<IPU_DPU_OPERATOR_DOC_URL>` | DPU Operator / OPI daemon guide |

---

## 1. Target hardware assumptions

Working assumption (override if the lab differs):

- One **Intel IPU E2100-class** adapter in a server that will be an
  OpenShift or Kubernetes worker (`host_base` still applies for KVM
  labs, but Intel’s published story is **OpenShift-centric**).
- Distinct **IMC (management complex)** vs **Arm compute complex** vs
  **host CPU**. Do not treat “can SSH to the host” as “can manage the
  IPU.”
- Host-visible PCI functions exist; whether they are SR-IOV PFs with
  kernel representors is **discovery-required**.

Out of scope for the first Intel milestone: OpenStack off-path,
assuming `devlink eswitch` == NVIDIA DPU mode, treating any Ice Lake
NIC as an IPU.

Confirm before mutate: `<IPU_SKU>`, `<IPU_FW_BUNDLE>`, Redfish
`Model`/`Serial` vs inventory. If those do not match E2100-class
docs, **stop** (discovery-required: wrong device class).

---

## 2. Firmware prerequisites

**Plan Intel-1.** Rating: **likely** that a vendor firmware bundle
exists; **discovery-required** which exact images, order, and whether
a host reboot/IMC reset is required.

Discover from `<IPU_REDFISH_DOC_URL>`:

- Image slots (IMC vs ACC vs Ethernet complex) — `<IPU_FW_SLOTS>`
- Signed-update vs recovery path
- Minimum host BIOS/ME/IOMMU settings — **do not guess**; Intel IPU
  IOMMU/ACS needs are **discovery-required** relative to generic
  `host_base` IOMMU checks

Ansible must not ship a firmware blob. `vendor_intel_ipu` may only
invoke an operator-supplied `<INTEL_FW_UPDATE_CMD>` when
`intel_ipu_apply_firmware: false` is flipped, default off.

---

## 3. Redfish and management access

**Plan Intel-2.** Rating: **likely** (IMC + Redfish called out in
OpenShift/IPU material). Unvalidated here.

Assumptions to **verify**, not encode as fact:

- `<REDFISH_BASE>` answers on an **OOB** network, not the tenant VF.
- Credentials are control-plane only (`APICredential` / vault), never
  in git.
- Inventory fields (`serialNumber`, `firmwareVersion`,
  `managementAddress`) can be filled from Redfish **or** stay `null`
  (OPI honesty). Do not invent serials.

Discover: auth method (session vs basic), TLS trust bundle, which
Collections map to `DPUDevice` / lifecycle. Tool names (`redfishtool`,
`fwupd`, vendor CLIs) are **placeholders** — `<INTEL_REDFISH_CLIENT>`.

If Redfish is unreachable, this entire track is blocked for
day-2 operations; do not fall back to guessing sysfs.

---

## 4. Arm compute complex role

Rating: **likely** that a programmable Arm complex exists; **discovery-required**
how many cores, NUMA, and whether it runs a general-purpose Linux you
can Ansible.

Expected ODSE mapping (to confirm):

| Plane | Role |
|---|---|
| Host x86 | Workloads (pods/VMs), VF consumption |
| IPU Arm ACC | Infrastructure: OPI daemon, ovnkube-dpu, NFs |
| IMC | Firmware, Redfish, secure boot |

Do **not** assume the ACC is a BlueField-like “full Ubuntu/BFB you
SSH and run distro OVS.” The OS may be **MicroShift**, a trimmed RHEL,
or a vendor appliance. **discovery-required:** SSH vs only operator
agents.

---

## 5. DPU-side OS or MicroShift model

**Plan Intel-3.** Rating: **likely** that Intel/Red Hat document a
DPU-side OS or MicroShift/RHEL profile; **discovery-required** which
ODSE will pin.

Options to record in the lab (pick one; do not mix):

1. **MicroShift (or equivalent) on the IPU** — second Kubernetes API
   (ODSE `dpu-cluster` analog).
2. **Full RHEL + kubelet** joining a DPU cluster.
3. **Appliance OS** with no generic kubelet — then ODSE Helm overlays
   may **not apply**; mark OVN-K8s DPU mode blocked until a path exists.

`ci/kind-simulated-dpu` does not emulate IMC/Redfish. It only tests
two kube-apiservers.

---

## 6. OPI-style daemon model

**Plan Intel-4.** Rating: **likely** — this is Intel’s strongest
public ODSE-adjacent story. Unvalidated.

Intended mapping:

```text
dpu-operator / Ansible  →  OPI daemon on IPU  →  vendor datapath
kubernetes/opi/adapter/vendor_intel.py wraps the daemon
(or speaks Redfish until the daemon exists)
```

Discover from `<IPU_DPU_OPERATOR_DOC_URL>`:

- gRPC vs REST vs CRD-only (`<OPI_WIRE_PROTOCOL>`)
- How close it is to `odse.opi/v1alpha1` vs a different proto
- Whether ODSE objects (`NetworkAttachment`, `OffloadPolicy`) map 1:1
  or need translation

Until `IntelIpuBackend.list_devices()` is implemented, `opi_services`
must not start a public listener. Conformance: OPI-1…10 in
`kubernetes/opi/README.md` §8; Intel-specific extras in §11 below.

**P4Runtime/IPDK is not a fallback of this section, or vice versa.**
Intel IPUs also expose a lower-level, vendor-native control plane via
IPDK's `infrap4d` P4Runtime daemon, which historically predates the
OpenShift DPU Operator and does not require an OPI daemon to be present.
ODSE tracks it as an independent, alternate proof of VM-to-VM capability
(`p4_pipeline_configured` in
`plugins/intel_ipu/vm_to_vm_capability.py`) rather than treating OPI/DPU
Operator as the only path. See
[`intel-ipu-p4-ipdk.md`](intel-ipu-p4-ipdk.md) — mock-only in this repo
today; real `infrap4d` connectivity is discovery-required per lab.

---

## 7. OpenShift DPU Operator alignment

**Plan Intel-5 / Intel-6.** Rating: **likely**.

ODSE `kubernetes/dpu-operator` is a **placeholder**, not a fork of
Red Hat’s DPU Operator. Alignment means:

- Same **jobs**: discover IPU, label host, lifecycle of DPU-side
  components.
- **Not** assumed API compatibility with OpenShift CRs until a version
  is pinned (`<DPU_OPERATOR_VERSION>`).

On **upstream Kubernetes** (Kind, vanilla k8s) the OpenShift operator
may not run. Then ODSE uses Ansible + `opi_services` only.
**discovery-required:** operator on non-OpenShift.

Do not apply `kubernetes/ovn-kubernetes/hardware/values.yaml` NVIDIA
keys to Intel.

---

## 8. Network function placement

**Plan Intel-7 / Intel-10.** Rating: **discovery-required** for *how*
an NF is placed (ACC container, IPU programmable pipeline, or host).

ODSE `NetworkFunction` object: DPU-resident ACL/NAT/telemetry — see
`kubernetes/opi/README.md` §2. Intel labs should record:

- `<INTEL_NF_RUNTIME>`: `container-on-acc` | `ipu-pipeline` | `unknown`
- Whether OVN ACLs still apply in parallel (must not bypass
  trust-boundary: host must not disable IPU policy)

Reverse-proxy / service-function offload (plan Intel-10) stays
**discovery-required** until a concrete NF image runs and traffic
hits it.

---

## 9. VF allocation and host workload connectivity

**Plan Intel-9.** Rating: SR-IOV VF creation **likely** in the matrix
sense of “IPU products expose host VFs”; **discovery-required** for
kernel `sriov_numvfs`, representors, and VFIO.

Do **not** assume `ansible/roles/sriov_setup` + `switchdev_setup`
work unchanged. Sequence to discover:

1. Does the host PF expose `sriov_totalvfs`? If no → vendor/Redfish
   VF create (`<INTEL_VF_CREATE_PATH>`).
2. Where is the representor? `<INTEL_REPRESENTOR_OWNER>` = IPU OS vs
   host. Matrix representor/switchdev: **discovery-required**.
3. Workload attach: VFIO vs vDPA vs vendor virtio — all
   **discovery-required** (vDPA and VFIO rows are
   discovery-required for Intel in the matrix).

Connectivity pass: ping between two host workloads whose VFs map to
IPU-side attach points. Use
`tools/topology-discovery/vf_to_representor_map.py` **on the OS that
owns representors**. Empty `representor_netdev` is a valid discovery
result, not a tool bug.

Kernel OVS `hw-offload` / TC `in_hw`: **discovery-required**. Do not
fail the track solely because the NVIDIA checker markers are absent.

---

## 10. OVN / ovn-kube lifecycle integration

**Plan Intel-8.** Rating: **likely** for OVN-Kubernetes DPU mode
(flagship published use case); **unvalidated**. ovn-controller on IPU
vs host: **discovery-required**.

Follow [`kubernetes/ovn-kubernetes/README.md`](../../kubernetes/ovn-kubernetes/README.md)
and `hardware/values.yaml` `intel:` placeholders.

Discover:

- Two-cluster vs operator-managed DPU node
- `<INTEL_SRIOV_RESOURCE_NAME>`
- Whether Geneve is terminated on IPU or host
- Chart keys — still comments only; do not helm-install from guesses

OpenStack Neutron off-path: **future**. Do not staff it on this track.

---

## 11. OPI conformance tests

Map to `tests/vendor-certification/opi-conformance/` (planned) and
`kubernetes/opi/README.md` §8.

| ID | Intel-specific note |
|---|---|
| OPI-1 | Envelope from daemon or adapter translation |
| OPI-2 | `switchdevSupported` / `vdpaSupported` stay **false** until proven |
| OPI-3 | Strip `vendorExtensions` (Intel Redfish extras) |
| OPI-4 | Attachment errors if representor owner unknown |
| OPI-5 | `hwOffload: true` **rejected** while offload is discovery-required |
| OPI-6 | No fake `ethtool` zeros |
| OPI-7 | Lifecycle `dryRun` via Redfish: reject if unsupported |
| OPI-8 | Idempotent VF/NF apply |
| OPI-9 | `simulated` backend in Kind — **not** this hardware doc |
| OPI-10 | Daemon not on tenant VF network |

Intel extra (plan Intel-11): **OPI-I-1** daemon vs `odse.opi/v1alpha1`
field mapping table in the lab log (required before claiming
conformance).

---

## 12. Security boundary validation

**Plan Intel-12.** Matrix security isolation: **discovery-required**.

Must test (see `docs/architecture/roles-and-trust-boundary.md`):

- Tenant pod/VM cannot reach `<REDFISH_BASE>` or `<OPI_DAEMON>`.
- Compromised host kubelet cannot turn off IPU-enforced policy (same
  intent as K8S-DPU-10).
- IMC credentials not in workload images.

**discovery-required:** whether the host VF is a control primitive
into IMC. Do not claim “IPU is a trust boundary” until OPI-10 and
this section have a passing `tests/security` case.

---

## 13. Telemetry and lifecycle

Telemetry: **discovery-required** metric names. Prefer
`tools/offload-checker` + Redfish `TelemetryService` if present.
`TelemetrySample.metricName` stays vendor-documented; no forced
taxonomy.

Lifecycle (`LifecycleOperation`): **likely** that Redfish supports
reboot/firmware; **discovery-required** for `drain` fail-open vs
fail-closed. Unsupported `rollbackOnFailure` → explicit error
(OPI-7).

`firmwareUpgrade` must use `<IPU_FW_BUNDLE>` from §2, never “latest.”

---

## 14. Known risks and TODOs

- **Wrong device:** Ice/E810-class NICs look similar in `lspci`;
  require SKU/Redfish model.
- **Operator lock-in:** OpenShift DPU Operator may not run upstream;
  Kind CI will not prove Intel hardware.
- **Datapath gap:** copying NVIDIA `switchdev_setup`/`ovs_setup` playbooks
  onto IPU without `devlink` evidence will fail or misconfigure.
- **Representor owner:** if representors are only on IPU OS, host
  `vf_to_representor_map.py` correctly returns nulls.
- **OpenStack:** future — do not block Kubernetes work on it.
- **OPI proto mismatch:** Intel daemon ≠ `odse.opi/v1alpha1` until a
  mapping table exists.
- **Firmware bricks:** default Ansible off for updates.

TODOs (`vendor_intel_ipu` + adapter):

1. Inventory via Redfish → `DPUDevice` (null-safe).
2. Variables: IMC URL, FW bundle, DPU OS type, representor owner.
3. Implement `IntelIpuBackend.list_devices()` then attach/lifecycle.
4. Document `<INTEL_VF_CREATE_PATH>` after first lab.
5. Pin DPU Operator **or** explicitly document Ansible-only path.
6. Security tests against IMC/OPI listen addresses.
7. Do not upgrade matrix datapath rows without
   `tests/vendor-certification` evidence.
