# Open Distributed Services Engine (ODSE)

**Start here:** [docs/getting-started.md](docs/getting-started.md)
(short). Full **What / Why / How** (HTML, open in a browser):
[docs/user-guide.html](docs/user-guide.html). Full documentation map
(users vs. contributors, every doc's audience):
[docs/README.md](docs/README.md).

`tools/labctl.py` is the control point. Do not start from `--apply`.

## 1. What this project is

Open Distributed Services Engine (ODSE) is an open-source blueprint and
automation toolkit for building a hardware-accelerated, DPU/IPU-offloaded
infrastructure stack on top of fully open components:

- **Linux** as the host and DPU operating system
- **KVM/QEMU** as the hypervisor
- **Open vSwitch (OVS)** and **OVN** for virtual networking and logical
  network abstraction
- **OVN-Kubernetes** as the Kubernetes CNI
- **OpenStack** (Neutron, Nova) as the IaaS control plane
- **SR-IOV**, **switchdev**, and **representor ports** for NIC hardware
  offload
- **vDPA** (vhost-vDPA) for virtio-compatible accelerated datapaths
- **OPI (Open Programmable Infrastructure)-style APIs** as the vendor-neutral
  management/control interface for DPUs and IPUs
- Pluggable **vendor backends** for AMD Pensando, NVIDIA BlueField, Intel
  IPU, and Marvell OCTEON DPUs/IPUs

The repository contains infrastructure-as-code (Ansible), Kubernetes
manifests/operators, OpenStack integration configuration, QEMU/libvirt
reference configs, test suites, operational tooling, and CI pipelines needed
to stand up, validate, and operate this stack end-to-end — from bare metal
and DPU provisioning through to workload-level network offload validation.

## 2. Why it exists

VMware's vSphere Distributed Services Engine (vDSE) demonstrated a
compelling model: offload networking, security, and storage services from
the host CPU onto a DPU/SmartNIC, freeing host cycles, improving isolation,
and giving operators a consistent management plane across heterogeneous
accelerator hardware. That model is compelling, but it is proprietary and
tied to the VMware stack.

Meanwhile, the open-source ecosystem already has every building block needed
to reproduce the same operational outcomes — SR-IOV, switchdev, OVS
hardware offload, OVN, vDPA, and an emerging vendor-neutral DPU management
API standard (OPI) — but no single project assembles them into a coherent,
documented, testable reference architecture. Operators who want a
non-proprietary, Linux/KVM/OpenStack/Kubernetes-based equivalent of vDSE
today have to stitch this together themselves, per vendor, from scratch.

ODSE exists to close that gap: to be the open, vendor-neutral reference
implementation and automation baseline for DPU/IPU-accelerated
infrastructure, so that adopting hardware offload does not require adopting
a proprietary hypervisor stack.

## 3. How this maps to VMware vDSE conceptually

| VMware vDSE concept | ODSE equivalent |
|---|---|
| ESXi host + DPU-offloaded networking/services | Linux host + KVM/QEMU, with datapath and services offloaded to a DPU |
| Distributed Switch (vDS) offloaded to DPU | OVS (with hardware offload) and OVN logical switching, offloaded via switchdev/representor ports |
| NSX network/security services on DPU | OVN logical routers/ACLs/security groups, offloaded via TC flower / OVS-TC / vendor accelerators |
| vSphere DPU management & lifecycle | Ansible roles + Kubernetes DPU operator for DPU provisioning, firmware, and lifecycle management |
| vCenter as the control plane | OpenStack (Nova/Neutron) and/or Kubernetes (OVN-Kubernetes) as the control plane, depending on workload type (VM vs. container) |
| DPU-native infrastructure services (vDPA-backed vNICs, etc.) | vDPA-backed virtio-net devices exposed to QEMU-based VMs; SR-IOV VFs for direct/PCI-passthrough-style acceleration |
| Vendor DPU partner ecosystem (Pensando, BlueField, Intel, others) | Vendor backend roles/adapters implementing a common OPI-style API surface for AMD Pensando, NVIDIA BlueField, Intel IPU, and Marvell OCTEON |
| Proprietary, single-vendor management API | Vendor-neutral, OPI-aligned gRPC/API contracts with vendor-specific backend implementations |

The intent is functional and architectural parity, not a literal port:
ODSE reproduces the *outcomes* of vDSE (host offload, consistent DPU
management, hardware-accelerated multi-tenant networking) using entirely
open technologies and open APIs.

## 4. Scope

### In scope

- Reference architecture and design documentation for DPU/IPU-offloaded
  compute and networking
- Ansible automation to provision hosts and DPUs: kernel/IOMMU tuning,
  SR-IOV, switchdev mode, OVS/OVN, vDPA, and OPI-style services
- Kubernetes integration via OVN-Kubernetes, a DPU operator, and OPI service
  manifests
- OpenStack integration via Neutron (ML2/OVN, SmartNIC ports) and Nova
  (`vnic_type=remote-managed` and related offload-aware scheduling/config)
- QEMU/libvirt reference configurations for vDPA- and VFIO/SR-IOV-backed
  VM networking
- A common, vendor-neutral OPI-style API/service abstraction with pluggable
  backends
- Vendor integration tracks for AMD Pensando, NVIDIA BlueField, Intel IPU,
  and Marvell OCTEON
- Functional, performance, offload-validation, security, and vendor
  certification test suites
- Operational tooling: inventory/topology discovery, offload state
  checking, telemetry collection
- CI: lint, Molecule-based role testing, simulated-DPU testing (KinD), and
  hardware-in-the-loop regression pipelines

### Out of scope

- Building a new hypervisor, container runtime, or Kubernetes distribution
  from scratch (we integrate existing upstream projects)
- Reimplementing OVS, OVN, OVN-Kubernetes, or OpenStack — this project
  configures, integrates, and validates them, and upstreams fixes where
  appropriate, rather than forking them
- Storage offload / DPU-based storage acceleration (NVMe-oF offload, etc.)
  — may be considered in a future phase, not in the initial scope
- Proprietary vendor SDKs or closed-source management planes; all
  vendor backends must integrate through documented, ideally upstreamable,
  open interfaces
- Bare-metal provisioning/PXE tooling itself (assumed as a prerequisite;
  ODSE automation starts from an installed base OS)
- Production support/SLAs — this is a reference implementation and test
  harness, not a commercially supported product

## 5. Initial supported vendor tracks

The project targets a common OPI-style API surface with the following
initial vendor backend tracks (each tracked independently under
`ansible/roles/vendor_*` and `docs/vendor-readiness/`):

1. **AMD Pensando** (Distributed Services Card / DSC-based DPUs)
2. **NVIDIA BlueField** (BlueField-2/3 DPUs, DOCA-based offload)
3. **Intel IPU** (Infrastructure Processing Unit platforms, e.g.
   Mount Evans-class devices)
4. **Marvell OCTEON** (OCTEON DPU family)

Each vendor track is expected to reach readiness independently and is
tracked via a per-vendor readiness matrix in `docs/vendor-readiness/`
covering firmware/driver versions, switchdev/SR-IOV support, vDPA support,
OPI API coverage, and known gaps. Feature parity across vendors is a goal,
not a starting assumption.

## 6. Milestones (current vs original vision)

The three bullets that used to live here were the **original product
vision** (simulated offload → one vendor e2e with vDPA/OPI →
Kubernetes/OpenStack). They were not a status board, and they had not
been updated as the lab tooling landed.

**What is true now (lab execution, 2026-08):**

| Track | Status |
|---|---|
| NVIDIA BlueField VM-to-VM (`labctl vm2vm`, DPU-mode) | **Dry-run GO.** Host discovery of BF-3 works. Hardware `--apply` is gated until a filled lab YAML (real PF/PCI, DPU SSH, VM images) and the apply readiness gate. |
| Intel IPU VM-to-VM (`labctl vm2vm`) | **Discovery-first / mock-first.** Real discovery and capability detection; `configure-forwarding` proceeds once an OPI or P4Runtime/IPDK control path is proven on your host, else returns discovery-required (not a fabricated success). See [`docs/operations/first-run-intel-vm2vm.md`](docs/operations/first-run-intel-vm2vm.md). |
| Intel IPU network-function offload (`labctl intel-nf`) | First-class Intel path, separate from VM-to-VM above. See [`docs/operations/first-run-intel-network-function-offload.md`](docs/operations/first-run-intel-network-function-offload.md). |
| AMD Pensando | `labctl amd-discovery` only. No assumed forwarding. |
| Marvell OCTEON | Blocked until DAO/DPDK is present. |
| Ansible host baseline, OVN, OVN-Kubernetes, OpenStack SmartNIC | Still the longer-term stack. **Not** the current first-run path. |

Operator start: [`docs/getting-started.md`](docs/getting-started.md) and
[`docs/user-guide.html`](docs/user-guide.html). Engineering backlog
(M0–M9): [`docs/operations/milestone-backlog.md`](docs/operations/milestone-backlog.md).

**Original vision (not claimed done):**

1. **Milestone 1 — Single-host reference (software-only / simulated).**
   Ansible + KVM/QEMU + OVS + OVN against `netdevsim` / mock switchdev,
   Molecule CI. Scaffolding exists; this is **not** the NVIDIA first-run.

2. **Milestone 2 — First real vendor DPU, end-to-end.**
   One physical DPU through switchdev/representors, OVS offload, a
   vDPA-backed VM path, and a first OPI-style service on the DPU, plus
   signed offload evidence. NVIDIA DPU-mode **dry-run** is the current
   slice of this; live VF create, DPU OVS mutation, VM attach, and
   confirmed offload are **not** done.

3. **Milestone 3 — Kubernetes and OpenStack control-plane integration.**
   OVN-Kubernetes DPU-offloaded pods and Neutron/Nova
   `vnic_type=remote-managed`. **Not started** as a working lab path.

## Repository layout

```
docs/                   Architecture, vendor readiness, threat model, test plan, and operations docs
plugins/                Vendor enablement backends (discover / configure / telemetry); common contract in plugins/base/
ansible/roles/          Provisioning and configuration roles (host, DPU, SR-IOV, switchdev, OVS, OVN, vDPA, OPI, vendor backends)
kubernetes/             OVN-Kubernetes, DPU operator, OPI services, and sample network functions
openstack/              Neutron OVN SmartNIC and Nova remote-managed vNIC integration
qemu/                   vDPA, VFIO, and libvirt XML reference configurations
tests/                  Functional, performance, offload-validation, security, vendor plugin fixtures, and vendor certification tests
tools/                  vendorctl, capability_report, inventory, topology, offload checking, and telemetry
config/                 Lab environment template (lab.example.yaml); copy to lab.yaml, do not commit secrets
ci/                     Lint, Molecule, simulated-DPU (KinD), and hardware regression pipelines
```

See `docs/architecture/` for design details and `docs/test-plan/` for how
these components are validated. Plugin roadmap:
[`docs/operations/vendor-implementation-roadmap.md`](docs/operations/vendor-implementation-roadmap.md).

## Vendor Backend Implementation

Vendor-specific enablement (PCI discovery, SR-IOV, eswitch, OVS, vDPA,
OPI/Redfish, ionic/RDMA) lives under **`plugins/`**, not in the OPI
Kubernetes adapter and not in `tools/*/plugins`. Each backend implements
`plugins.base.VendorBackend` and returns `OperationResult`. That keeps
NVIDIA BlueField, Intel IPU, Marvell OCTEON, and AMD Pensando on **separate
code paths** so switchdev/OVS-DOCA is never copied onto vendors that have
not proven it. Ansible `vendor_*` roles remain host provisioning; they do
not replace these plugins.

Commands below are **read-only**. Do not pass `--apply` unless you have a
written change window, `config/lab.yaml` with `dry_run: false`, and a
documented CLI. `config/lab.example.yaml` keeps `dry_run: true` and blocks
apply.

Before running any `--apply` hardware workflow, generate and review the
cross-vendor readiness matrix:

```text
python tools/vendor_readiness_matrix.py --configs config/*.yaml --output tests/results/vendor-readiness-matrix.md
```

See [`docs/operations/cross-vendor-readiness-matrix.md`](docs/operations/cross-vendor-readiness-matrix.md).
NVIDIA DPU-mode is the first dry-run target. Marvell needs DAO/DPDK.
Intel has two separate first-runs: `labctl intel-nf` (network-function
offload, first-class) and `labctl vm2vm` (VM-to-VM, discovery-first — see
[`docs/operations/first-run-intel-vm2vm.md`](docs/operations/first-run-intel-vm2vm.md)).
AMD is discovery-first until a control path is proven.

**NVIDIA default is BlueField DPU mode** (`nvidia-bluefield-dpu-mode`):
OVS/OVS-DOCA and representors run on the DPU; the host only runs
KVM/QEMU/libvirt and VF passthrough. Host OVS is not the forwarding owner.
See [`docs/architecture/nvidia-bluefield-control-placement.md`](docs/architecture/nvidia-bluefield-control-placement.md).
Host-switchdev is an advanced profile and must be selected explicitly.
DPU-mode representor mapping uses `--location dpu` (see
[`docs/operations/dpu-side-vf-to-representor-mapping.md`](docs/operations/dpu-side-vf-to-representor-mapping.md)).

### 1. List vendors

```text
python tools/vendorctl.py list-vendors
```

### 2. Discovery (dry-run / read-only)

`discover` only runs inventory probes. It does not set eswitch, numvfs, or
OVS. Default CLI policy is dry-run; `--json` only changes output shape.

```text
python tools/vendorctl.py discover --vendor nvidia-bluefield --json
```

Optional lab file (placeholders only; copy before filling real PFs):

```text
python tools/vendorctl.py discover --vendor intel-ipu --config config/lab.example.yaml
```

Also: `python tools/vendorctl.py validate --vendor amd-pensando`

### 3. Capability report

Runs `discover()` for every backend and prints honest flags (`null` =
unknown). Does not configure anything.

```text
python tools/capability_report.py --format markdown
```

JSON: `python tools/capability_report.py --format json`

Dummy table (not lab evidence):
[`docs/vendor-readiness/sample-capability-report.md`](docs/vendor-readiness/sample-capability-report.md).

### 4. Add a new backend

1. Implement `VendorBackend` under `plugins/<name>/` (discover first;
   configure methods skip or dry-run).
2. Register it in `plugins/__init__.py` (`BACKENDS` + aliases).
3. Add the CLI id to `tools/vendorctl.py` and `tools/capability_report.py`.
4. Add `tests/fixtures/<name>/` and `tests/vendor/<name>/` parser tests.
   Hardware is not required.
5. Document assumptions in `docs/vendor-readiness/` (unknown stays
   `discovery-required`).

Unknown devices, representors, or offload must stay `null`. Empty
telemetry is valid; fake zeros are not.

### 5. Real hardware tests (separate from unit tests)

| Suite | Hardware? |
|---|---|
| `tests/unit/`, `tests/vendor/` + fixtures | No |
| `tests/functional/`, `tests/offload-validation/` | Yes, when a card exists |
| `tests/vendor-certification/reports/` | Yes; Kind cannot award Level ≥ 1 |

Do not call live `devlink set` / `sriov_numvfs` from unit tests. Pin SKU
and firmware in the cert report. See
[`docs/test-plan/vendor-certification.md`](docs/test-plan/vendor-certification.md)
and [`config/README.md`](config/README.md).

### 6. Current status

| Vendor | Status |
|---|---|
| **NVIDIA BlueField** | Discovery (lspci/devlink/ip/ethtool/OVS show) and representor parsing. switchdev/OVS helpers exist but stay dry-run; OVS-DOCA only if `doca` is listed. Offload not claimed from `hw-offload=true` alone. |
| **Intel IPU** | Discovery (IPU/E2100/IDPF text hints and known PCI device IDs; ice/E810 ignored). OPI/Redfish from config; HTTP client does not send requests. VM-to-VM forwarding gated on OPI or P4Runtime/IPDK capability proof (mock/CLI-over-SSH); switchdev/OVS-DOCA skipped. |
| **Marvell OCTEON** | Discovery (PCI/DPDK bind/DAO env). Kernel switchdev not assumed. DAO apply without a frozen CLI returns error. Generic OVS ≠ DAO offload. |
| **AMD Pensando** | Discovery-first (ionic, optional RDMA). switchdev, representors, OVS hardware offload, and vDPA are discovery-required. ionic stats are not offload proof. |

Roadmap (seven phases per vendor):
[`docs/operations/vendor-implementation-roadmap.md`](docs/operations/vendor-implementation-roadmap.md).

## Single Forwarding Control Point

For the VM-to-VM single-DPU lab use case (one server, one DPU/IPU, two
SR-IOV VFs, two KVM/QEMU VMs), the user controls VM-to-VM forwarding
with exactly one command:

```text
python tools/labctl.py vm2vm configure-forwarding --config <config-file> --apply
```

`labctl` hides OVS, DAO, DPU Operator, OPI, DOCA, `tc`, `devlink`, and
vendor APIs behind the `VendorBackend.configure_forwarding()` interface
(`plugins/base/backend.py`). Do not call those tools directly to connect
the lab VMs — see
[`docs/architecture/forwarding-intent-model.md`](docs/architecture/forwarding-intent-model.md)
and [`docs/operations/configure-forwarding.md`](docs/operations/configure-forwarding.md)
for the full model and examples, and
[`config/README-lab-vm2vm.md`](config/README-lab-vm2vm.md) for the lab
config that feeds it. After a run, `python tools/report_generator.py
--input-dir tests/results/<vendor>/<run-id>/` turns the stage evidence
into one report (see
[`docs/test-plan/result-report-template.md`](docs/test-plan/result-report-template.md)).
Bringing up a specific vendor track from scratch:
[`docs/operations/vendor-maturity-execution-plan.md`](docs/operations/vendor-maturity-execution-plan.md).

## Status

NVIDIA BlueField DPU-mode **dry-run** (inventory, preflight, `labctl
vm2vm`) is the current first-run. Hardware `--apply`, confirmed
offload, Kubernetes, and OpenStack are **not** first-run. Interfaces
are still evolving. See section 6 above.
