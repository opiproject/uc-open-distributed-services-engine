# Open Distributed Services Engine: Open-Source Equivalent of vSphere vDSE/NSX DPU Offload

## Purpose

This document defines a practical open-source implementation plan for a vSphere Distributed Services Engine like architecture using KVM/QEMU, Open vSwitch, OVN, OVN-Kubernetes, OpenStack Neutron/Nova, SR-IOV, switchdev, representor ports, vDPA, DPDK, tc flower, vendor SDKs, and OPI-style APIs.

The target is to support four DPU/IPU vendor families:

- AMD Pensando
- NVIDIA BlueField
- Intel IPU
- Marvell OCTEON

The design goal is not to clone VMware vDSE as a single product. The goal is to create a vendor-neutral framework where the x86 host runs workloads, the DPU/IPU runs infrastructure services, and a common control-plane contract programs vendor-specific offload backends.

---

## 1. Mental Model

### 1.1 VMware vDSE model

```text
VM / container workload
    |
    v
vSphere virtual networking
    |
    v
NSX switching / routing / DFW / observability
    |
    v
DPU-backed datapath
    |
    v
Physical network
```

### 1.2 Open-source equivalent model

```text
VM / container workload
    |
    v
KVM/QEMU or Kubernetes pod
    |
    v
OVS / OVN / OVN-Kubernetes / OpenStack Neutron
    |
    v
VF / representor / vDPA / virtio / SR-IOV
    |
    v
DPU-side OVS/OVN agent or vendor offload engine
    |
    v
DPU hardware switch / packet pipeline
    |
    v
Physical network
```

### 1.3 Core principle

The common product is not one universal datapath. The common product is:

- A common architecture model
- A common topology and inventory model
- A common lifecycle model
- A common testing and validation framework
- Vendor plugins for AMD, NVIDIA, Intel, and Marvell

---

## 2. Scope

### 2.1 In scope

- VM offload using KVM/QEMU
- Kubernetes offload using OVN-Kubernetes DPU mode
- OpenStack off-path SmartNIC DPU model
- OVS hardware offload using SR-IOV, switchdev, and representors
- vDPA and virtio acceleration where supported
- OPI-style abstraction for DPU/IPU lifecycle and network APIs
- Per-vendor backend enablement
- Security, observability, and performance validation

### 2.2 Out of scope for first implementation

- Full vSphere/vCenter clone
- Full NSX clone
- Production-grade multi-tenant cloud platform
- Universal P4 pipeline compiler abstraction
- Guaranteed feature parity across all vendors
- Storage offload beyond discovery and future hooks

---

## 3. Reference Use Cases

### 3.1 VM datapath offload

```text
KVM/QEMU VM
    |
    v
VF passthrough or vhost-vDPA
    |
    v
DPU-side representor
    |
    v
OVS / OVN / vendor offload backend
    |
    v
DPU hardware fast path
```

Primary goals:

- Reduce host CPU usage for switching and policy enforcement
- Preserve VM control through libvirt/OpenStack
- Support VF passthrough and vDPA-based VM interfaces
- Validate offload with counters and traffic tests

### 3.2 Kubernetes datapath offload

```text
Host Kubernetes cluster
    |
    v
Workload pods
    |
    v
OVN-Kubernetes host-side components
    |
    v
DPU Kubernetes cluster
    |
    v
DPU-side OVN/OVS components
    |
    v
DPU hardware fast path
```

Primary goals:

- Move SDN control/data path from host into DPU where supported
- Isolate network control plane from host compromise
- Enable accelerated pod networking via VF and representor paths
- Validate pod-to-pod, pod-to-service, and pod-to-external paths

### 3.3 Network function offload

```text
Host workload
    |
    v
DPU/IPU service function
    |
    v
Reverse proxy / firewall / ACL / telemetry / policy function
    |
    v
External network
```

Primary goals:

- Run infrastructure functions on the DPU/IPU
- Use OPI-style APIs for vendor-neutral lifecycle
- Start with simple network functions such as proxy, ACL, NAT, telemetry, or packet filter

---

## 4. Proposed Repository Structure

```text
open-distributed-services-engine/
├── docs/
│   ├── architecture/
│   ├── vendor-readiness/
│   ├── threat-model/
│   ├── test-plan/
│   └── operations/
├── ansible/
│   ├── roles/
│   │   ├── host_base/
│   │   ├── dpu_base/
│   │   ├── sriov_setup/
│   │   ├── switchdev_setup/
│   │   ├── ovs_setup/
│   │   ├── ovn_setup/
│   │   ├── qemu_vdpa_setup/
│   │   ├── opi_services/
│   │   ├── vendor_amd_pensando/
│   │   ├── vendor_nvidia_bluefield/
│   │   ├── vendor_intel_ipu/
│   │   └── vendor_marvell_octeon/
├── kubernetes/
│   ├── ovn-kubernetes/
│   ├── dpu-operator/
│   ├── opi/
│   └── network-functions/
├── openstack/
│   ├── neutron-ovn-smartnic/
│   └── nova-vnic-remote-managed/
├── qemu/
│   ├── vdpa/
│   ├── vfio/
│   └── libvirt-xml/
├── tests/
│   ├── functional/
│   ├── performance/
│   ├── offload-validation/
│   ├── security/
│   └── vendor-certification/
├── tools/
│   ├── inventory/
│   ├── topology-discovery/
│   ├── offload-checker/
│   └── telemetry-collector/
└── ci/
    ├── lint/
    ├── molecule/
    ├── kind-simulated-dpu/
    └── hardware-regression/
```

---

## 5. Architecture Layers

### Layer 1: Common open datapath

Components:

- Linux kernel
- Open vSwitch
- OVN
- SR-IOV
- switchdev
- representor ports
- tc flower
- DPDK
- vDPA
- VFIO

Purpose:

- Provide the common datapath vocabulary.
- Provide baseline offload validation independent of orchestration layer.

### Layer 2: Open orchestration

Components:

- OpenStack Neutron and Nova for VM orchestration
- OVN-Kubernetes for container orchestration
- OPI for DPU/IPU abstraction
- Ansible for host and DPU lifecycle automation

Purpose:

- Provide control-plane integration.
- Map workloads to DPU/IPU datapath objects.

### Layer 3: Vendor backends

Vendor tracks:

- NVIDIA: BlueField, DOCA, OVS-DOCA, DOCA Flow
- Intel: IPU E2100, OpenShift DPU Operator, OPI-style daemon
- Marvell: OCTEON, DAO OVS Offload, DPDK graph, representors
- AMD: Pensando DSC, ionic drivers, ionic_rdma, vendor API discovery, OPI candidate path

Purpose:

- Implement common abstractions through vendor-specific methods.

---

## 6. Phased Activity Plan

## Phase 0: Define the reference use case

### Activity 0.1: Define VM and Kubernetes use cases

Create two initial reference use cases:

1. VM workload using KVM/QEMU
2. Kubernetes workload using OVN-Kubernetes

Deliverables:

```text
docs/architecture/usecase-vm-kvm.md
docs/architecture/usecase-k8s-ovn.md
docs/architecture/vdse-to-open-equivalent-mapping.md
```

Success criteria:

- Clear differentiation between VM-style and Kubernetes-style offload.
- Clear mapping from VMware vDSE concepts to open-source equivalents.

---

## Phase 1: Common DPU abstraction model

### Activity 1.1: Define roles

Define three roles:

```text
Host Node
  Runs workloads: VMs or pods

DPU Node
  Runs infrastructure networking and control services

Control Plane
  Programs topology, policy, and lifecycle
```

Deliverables:

```text
docs/architecture/roles.md
docs/architecture/host-dpu-trust-boundary.md
docs/architecture/control-plane-placement.md
```

### Activity 1.2: Define common APIs

Define these API categories:

```text
Inventory API
  Discover vendor, model, firmware, PCI mapping, PF/VF mapping

Provisioning API
  Prepare DPU OS, management IP, SSH/Redfish/API access

Network API
  Create VF, attach representor, configure OVS/OVN, install policy

Telemetry API
  Collect counters, offload stats, link state, errors

Lifecycle API
  Upgrade firmware, restart DPU service, reboot DPU, drain node
```

Deliverables:

```text
docs/architecture/api-model.md
docs/architecture/opi-alignment.md
kubernetes/opi/
ansible/roles/opi_services/
```

---

## Phase 2: Host baseline

### Activity 2.1: Prepare Linux host

Host baseline:

```text
Enable IOMMU
Enable SR-IOV in BIOS
Enable VT-d or AMD-Vi
Install kernel with vendor NIC/DPU support
Install iproute2, ethtool, devlink, tc, ovs, ovn, qemu-kvm, libvirt
Configure hugepages
Configure CPU isolation for performance tests if needed
```

Deliverables:

```text
ansible/roles/host_base/
ansible/roles/sriov_setup/
docs/operations/host-prerequisites.md
```

### Activity 2.2: Implement DPU inventory collector

Collect:

```text
lspci -nn
devlink dev show
devlink port show
ip -d link show
ethtool -i <pf>
ethtool -S <pf>
ovs-vsctl show
ovs-appctl dpctl/show
```

Purpose:

```text
Map host PFs, VFs, representors, DPU-side interfaces, firmware, and PCI addresses.
```

Deliverables:

```text
tools/inventory/dpu-inventory.py
docs/vendor-readiness/inventory-schema.md
```

---

## Phase 3: OVS hardware offload baseline

### Activity 3.1: Configure SR-IOV, switchdev, and representors

Common baseline steps:

```text
Create VFs
Unbind VFs if required
Switch PF or embedded switch to switchdev mode where supported
Verify representor ports appear
Bind VFs back to host or guest
Attach representors to OVS bridge
Enable OVS hardware offload
```

Deliverables:

```text
ansible/roles/switchdev_setup/
ansible/roles/ovs_setup/
tests/offload-validation/test-switchdev-representors.sh
```

### Activity 3.2: Validate slow path and fast path

Validation logic:

```text
1. Start VM1 and VM2 with VFs.
2. Attach corresponding representors to OVS.
3. Generate traffic VM1 to VM2.
4. Confirm initial packets hit OVS/software path.
5. Confirm later traffic uses hardware offload where supported.
6. Capture OVS datapath flows and offload indicators.
7. Compare CPU usage with offload enabled vs disabled.
```

Deliverables:

```text
tests/offload-validation/test-ovs-offload-basic.md
tools/offload-checker/ovs-offload-checker.sh
```

---

## Phase 4: KVM/QEMU fast-path options

### Activity 4.1: VF passthrough mode

```text
VM gets SR-IOV VF through VFIO
DPU/NIC representor is attached to OVS
OVS/tc/vendor backend offloads eligible flows
```

Deliverables:

```text
qemu/vfio/
qemu/libvirt-xml/vf-passthrough.xml
tests/performance/vf-passthrough/
```

### Activity 4.2: vDPA mode

```text
Guest sees virtio-net
QEMU uses vhost-vDPA backend
DPU/NIC provides hardware or software vDPA datapath
Control path remains vendor-specific
```

Deliverables:

```text
qemu/vdpa/
qemu/libvirt-xml/vhost-vdpa.xml
tests/performance/vdpa/
docs/architecture/vfio-vs-vdpa.md
```

---

## Phase 5: OpenStack-style VM orchestration

### Activity 5.1: Implement OpenStack off-path DPU model

Architecture:

```text
Host:
  Nova compute
  QEMU VM
  VF assigned to VM

DPU:
  ovs-vswitchd
  ovn-controller
  br-int
  representor ports
  metadata agent if needed

Control plane:
  Neutron
  OVN northbound/southbound DB
  Nova scheduling and port binding
```

Deliverables:

```text
openstack/neutron-ovn-smartnic/
openstack/nova-vnic-remote-managed/
docs/architecture/openstack-offpath-dpu.md
```

### Activity 5.2: Implement port-binding metadata model

Mapping required:

```text
VM port
  -> VF PCI address on host
  -> physical DPU card serial number
  -> DPU hostname
  -> representor name on DPU
  -> OVN logical switch port
```

Deliverables:

```text
docs/architecture/port-binding-metadata.md
tools/topology-discovery/vf-to-representor-map.py
```

---

## Phase 6: OVN-Kubernetes DPU model

### Activity 6.1: Two-cluster Kubernetes layout

Deploy:

```text
Host Kubernetes cluster:
  Runs user workloads

DPU Kubernetes cluster:
  Runs DPU-side networking components
```

Deliverables:

```text
kubernetes/ovn-kubernetes/host-cluster/
kubernetes/ovn-kubernetes/dpu-cluster/
docs/architecture/k8s-two-cluster-dpu.md
```

### Activity 6.2: Simulated DPU mode for CI

Use simulated DPU mode for CI before physical hardware.

Deliverables:

```text
ci/kind-simulated-dpu/
tests/functional/k8s-dpu-sim/
```

### Activity 6.3: Hardware DPU mode

Steps:

```text
Configure SR-IOV on DPU host
Configure OVS hardware offload or vendor offload backend
Label DPU host nodes
Deploy OVN-Kubernetes host components
Deploy DPU-side ovnkube components
Validate pod networking through VF/representor path
```

Deliverables:

```text
kubernetes/ovn-kubernetes/hardware/
tests/functional/k8s-dpu-hardware/
```

---

## Phase 7: OPI abstraction layer

### Activity 7.1: Implement OPI alignment layer

Steps:

```text
1. Define vendor-neutral DPU object model.
2. Map OPI APIs to vendor backend actions.
3. Implement discovery, provisioning, network, telemetry, and lifecycle adapters.
4. Add per-vendor plugin backend.
5. Run common conformance tests across vendors.
```

Deliverables:

```text
kubernetes/opi/
ansible/roles/opi_services/
tests/vendor-certification/opi-conformance/
```

---

## Phase 8: Security and isolation

### Activity 8.1: Threat model

Validate:

```text
Host workload cannot access DPU management plane
DPU network control plane remains isolated from host workload compromise
Policy enforcement remains active if host workload is compromised
DPU-side ACL/security group behavior is auditable
Management and telemetry access paths are authenticated
```

Deliverables:

```text
docs/threat-model/
tests/security/
```

---

## Phase 9: Performance benchmarking

### Activity 9.1: Benchmark matrix

Measure:

```text
No-offload baseline
OVS hardware offload
VF passthrough
vDPA
DPU-side OVS/OVN
Vendor optimized backend
```

Metrics:

```text
Throughput
Packets per second
Latency
Tail latency
Jitter
CPU usage on host
CPU usage on DPU
Dropped packets
Offload hit ratio
Flow setup rate
```

Deliverables:

```text
tests/performance/
tools/telemetry-collector/
docs/test-plan/performance-methodology.md
```

---

## Phase 10: Vendor certification

### Activity 10.1: Certification matrix

| Capability | AMD Pensando | NVIDIA BlueField | Intel IPU | Marvell OCTEON |
|---|---|---|---|---|
| DPU OS access | Discovery required | Strong public DPU OS path | OpenShift/IPU path documented | DAO/OCTEON path documented |
| SR-IOV VF | Discovery required | Supported in vendor documentation | Used in DPU Operator/OpenShift context | Used in DAO OVS offload model |
| Switchdev/representor | Discovery required | Documented in OVS-DOCA setup | Validate per IPU stack | DPDK representors documented |
| OVS offload | Discovery required | OVS-Kernel, OVS-DPDK, OVS-DOCA | Validate through OPI/DPU Operator path | DAO OVS offload documented |
| vDPA | Discovery required | Documented option | Discovery required | Discovery required |
| OPI alignment | Candidate | Candidate | Strong public OPI/OpenShift story | Candidate |
| Best first milestone | Driver and capability discovery | OVS-DOCA acceleration | OpenShift DPU Operator and OPI | DAO OVS offload |

Deliverables:

```text
docs/vendor-readiness/vendor-capability-matrix.md
tests/vendor-certification/
```

---

# 7. Vendor Workstreams

## 7.1 NVIDIA BlueField workstream

Activities:

```text
NVIDIA-1: Install BlueField OS or BFB image
NVIDIA-2: Enable embedded mode or DPU mode as required
NVIDIA-3: Configure SR-IOV VFs
NVIDIA-4: Switch eSwitch to switchdev
NVIDIA-5: Enable OVS-Kernel offload baseline
NVIDIA-6: Enable OVS-DPDK baseline
NVIDIA-7: Enable OVS-DOCA backend
NVIDIA-8: Validate VF passthrough
NVIDIA-9: Validate vDPA mode
NVIDIA-10: Validate OVN-Kubernetes DPU acceleration
NVIDIA-11: Validate OpenStack off-path DPU model
NVIDIA-12: Collect offload counters and performance delta
```

Artifacts:

```text
ansible/roles/vendor_nvidia_bluefield/
docs/vendor-readiness/nvidia-bluefield.md
tests/vendor/nvidia/
```

---

## 7.2 Intel IPU workstream

Activities:

```text
Intel-1: Install required Intel IPU firmware
Intel-2: Enable Redfish reachability
Intel-3: Install DPU-side OS or MicroShift/RHEL profile
Intel-4: Deploy OPI-compatible daemon on IPU
Intel-5: Deploy DPU Operator on OpenShift or upstream Kubernetes variant
Intel-6: Validate host-to-IPU management path
Intel-7: Validate network-function placement on IPU
Intel-8: Validate OVN-Kubernetes or ovn-kube lifecycle integration
Intel-9: Validate VF allocation and host workload connectivity
Intel-10: Validate reverse proxy or service function offload
Intel-11: Add OPI API compatibility tests
Intel-12: Add security boundary tests between host and IPU
```

Artifacts:

```text
ansible/roles/vendor_intel_ipu/
kubernetes/dpu-operator/intel/
docs/vendor-readiness/intel-ipu-e2100.md
tests/vendor/intel/
```

---

## 7.3 Marvell OCTEON workstream

Activities:

```text
Marvell-1: Install supported OCTEON SDK or DAO environment
Marvell-2: Enable host endpoint and MAC connectivity model
Marvell-3: Configure DPDK representors
Marvell-4: Deploy DAO OVS offload application
Marvell-5: Create VM VF path from host to OCTEON
Marvell-6: Validate first-packet OVS classification
Marvell-7: Validate post-offload hardware forwarding
Marvell-8: Validate wire-to-VM and VM-to-wire traffic
Marvell-9: Integrate with OVS/OVN control plane where feasible
Marvell-10: Add DPDK rte_flow validation
Marvell-11: Add performance tests for VM-to-VM and VM-to-wire
Marvell-12: Add telemetry hooks from DAO and DPDK counters
```

Artifacts:

```text
ansible/roles/vendor_marvell_octeon/
docs/vendor-readiness/marvell-octeon.md
tests/vendor/marvell/
```

---

## 7.4 AMD Pensando workstream

Activities:

```text
AMD-1: Install or verify ionic Ethernet driver
AMD-2: Verify Pensando DSC host-side device enumeration
AMD-3: Verify DSC-side driver and management path availability
AMD-4: Verify whether SR-IOV VF creation is exposed for the target card/firmware
AMD-5: Verify whether representor/switchdev model is exposed
AMD-6: If representors exist, attach them to OVS and test OVS hardware offload
AMD-7: If representors are not exposed, define alternative model using vendor APIs or OPI Network API
AMD-8: Validate RDMA/RoCE path if supported by target card and kernel
AMD-9: Validate VFIO/VF passthrough into KVM guest
AMD-10: Validate OVN integration feasibility
AMD-11: Validate telemetry path
AMD-12: Document gaps clearly against NVIDIA, Intel, and Marvell tracks
```

Artifacts:

```text
ansible/roles/vendor_amd_pensando/
docs/vendor-readiness/amd-pensando.md
tests/vendor/amd/
```

---

# 8. Recommended Execution Order

## Track A: Common architecture

```text
1. Write architecture documents.
2. Write vendor capability matrix.
3. Implement inventory collector.
4. Implement host baseline.
5. Implement common OVS offload tests.
```

## Track B: NVIDIA first

Use NVIDIA BlueField first because the public OVS-DOCA and OVN-Kubernetes DPU material is comparatively mature.

## Track C: Intel second

Use Intel IPU E2100 with OpenShift DPU Operator and OPI-style workflows.

## Track D: Marvell third

Use Marvell OCTEON DAO OVS offload as the vendor-specific datapath exercise.

## Track E: AMD fourth or parallel discovery

Run AMD Pensando as a discovery-first track because the public material is clearer for drivers and RDMA than for a full OVS-offload flow.

---

# 9. Initial Epics and Stories

## Epic 1: Open DPU Infrastructure Baseline

Stories:

```text
1. Create host and DPU inventory schema.
2. Implement inventory collector.
3. Create Ansible host baseline role.
4. Create SR-IOV setup role.
5. Create switchdev setup role.
6. Create OVS setup role.
7. Create offload validation script.
8. Document host-DPU trust model.
```

## Epic 2: KVM/QEMU vDSE-like Datapath

Stories:

```text
1. Create VM with VF passthrough.
2. Attach VF representor to OVS.
3. Enable OVS hardware offload.
4. Validate VM-to-VM offload.
5. Validate VM-to-wire offload.
6. Add vhost-vDPA VM profile.
7. Compare VFIO vs vDPA.
8. Add performance test harness.
```

## Epic 3: Kubernetes/OpenStack Control Plane

Stories:

```text
1. Deploy OVN-Kubernetes simulated DPU mode.
2. Deploy OVN-Kubernetes hardware DPU mode.
3. Deploy OpenStack Neutron OVN off-path model.
4. Implement VF-to-DPU mapping.
5. Implement DPU-side representor binding.
6. Add OPI API mapping document.
7. Add vendor plugin skeletons.
8. Add common conformance tests.
```

---

# 10. Definition of Done

A vendor backend is considered minimally functional when it can pass:

```text
1. Inventory discovery test
2. Host PF/VF discovery test
3. DPU management reachability test
4. VF creation test
5. Representor discovery test, if supported
6. OVS bridge attach test
7. VM-to-VM traffic test
8. VM-to-wire traffic test
9. Offload detection test
10. CPU reduction comparison test
11. Telemetry collection test
12. Security boundary test
```

A vendor backend is considered advanced when it can also pass:

```text
1. vDPA test
2. OVN-Kubernetes DPU mode test
3. OpenStack off-path DPU test
4. OPI compatibility test
5. DPU-side network function test
6. Failure and recovery test
7. Firmware/lifecycle update dry-run test
8. Multi-node performance test
```

---

# 11. Key Design Rules

1. Keep vendor logic behind plugin boundaries.
2. Do not assume all vendors expose switchdev or representors identically.
3. Do not assume all vendors support vDPA.
4. Do not claim full NSX equivalence.
5. Treat OPI as the long-term abstraction path, not necessarily the first working path for every vendor.
6. Start with measurable datapath offload before adding orchestration complexity.
7. Prioritize inventory and validation before automation.
8. Document gaps explicitly per vendor.
9. Keep KVM/QEMU and Kubernetes tracks separate but reusable.
10. Make tests the contract across vendors.

---

# 12. Immediate Next Steps

1. Create the repo structure.
2. Add this document under `docs/architecture/open-source-vdse-equivalent-plan.md`.
3. Create `docs/vendor-readiness/vendor-capability-matrix.md`.
4. Implement `tools/inventory/dpu-inventory.py`.
5. Implement `ansible/roles/host_base`.
6. Implement `ansible/roles/sriov_setup`.
7. Implement `ansible/roles/ovs_setup`.
8. Start NVIDIA BlueField as the first full implementation track.
9. Start AMD Pensando as a parallel discovery track.
10. Add CI simulation for OVN-Kubernetes DPU mode.

