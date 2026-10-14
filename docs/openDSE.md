Is it same as this - https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching? If no, what's the difference?

No, it is **not the same** as the open-source vDSE-equivalent plan we discussed. DOCA Switching - NVIDIA Docs is best understood as the **NVIDIA BlueField/ConnectX low-level switching and offload implementation layer**. It is one important backend component you would use for the **NVIDIA track** of our plan, not the whole multi-vendor framework. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

**Short answer**

**DOCA Switching is a vendor-specific datapath/offload guide.**

**Our open-source vDSE-equivalent plan is a multi-layer, multi-vendor architecture.**

So the relationship is:

Our Open Distributed Services Engine plan

├── Common architecture

├── Inventory and lifecycle framework

├── KVM/QEMU VM path

├── OVN-Kubernetes path

├── OpenStack off-path DPU path

├── OPI-style abstraction

├── Vendor backends

│ ├── NVIDIA backend

│ │ ├── DOCA Switching

│ │ ├── OVS-DOCA

│ │ └── DOCA Platform / DPF

│ ├── Intel backend

│ ├── Marvell backend

│ └── AMD backend

└── Tests, certification, security, telemetry

**What DOCA Switching - NVIDIA Docs actually covers**

DOCA Switching - NVIDIA Docs says it provides a guide to **hardware-accelerated switching capabilities** of NVIDIA BlueField and ConnectX platforms, including **switchdev mode**, **representor models**, **vDPA**, **bridge offload**, and **link aggregation**. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

The same document says BlueField and ConnectX platforms use **hardware-based switching offloads** to deliver high-performance network virtualization with minimal CPU overhead, and that complex switching logic is offloaded to the **E-Switch**. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

It also defines **switchdev mode**: the physical function operates as a virtual switch, software can control VF/SF traffic, intercept incoming/outgoing VF/SF traffic, and configure steering rules that offload traffic. Once configured, the PF is considered an E-Switch and additional ports are created for each VF/SF as **port representors**. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

For BlueField in DPU mode, DOCA Switching - NVIDIA Docs says switchdev mode is configured by default for all PFs on the Arm side, that this configuration must not be changed, and that the host cannot configure PFs into switchdev mode; host PFs operate in legacy mode only. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

That is very relevant to our plan, but it is specifically the **NVIDIA BlueField switching substrate**.

**Difference between our plan and DOCA Switching - NVIDIA Docs**

| **Area** | **DOCA Switching - NVIDIA Docs** | **Our open-source vDSE-equivalent plan** |
| --- | --- | --- |
| Vendor scope | NVIDIA BlueField and ConnectX only. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching) | AMD Pensando, NVIDIA BlueField, Intel IPU, Marvell OCTEON. |
| Layer | Low-level switching/offload layer: switchdev, representors, E-Switch, vDPA, bridge offload, LAG. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching) | Full architecture: inventory, lifecycle, KVM/QEMU, OpenStack, OVN-Kubernetes, OPI, vendor plugins, tests. |
| Control plane | Explains NVIDIA switching capabilities and configuration concepts. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching) | Defines how OpenStack, OVN-Kubernetes, OPI-style APIs, Ansible, and test frameworks coordinate the system. |
| Workload model | Focuses on virtualized network resources and switching features on NVIDIA platforms. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching) | Covers VM workloads, Kubernetes workloads, and network functions across multiple vendors. |
| Multi-vendor abstraction | Not the purpose of this NVIDIA document. | Central purpose of the proposed framework. |
| vSphere vDSE equivalence | Provides part of the datapath mechanism for NVIDIA. | Tries to recreate the vDSE use case in open-source form across vendors. |

**The most accurate mapping**

For your repo, DOCA Switching - NVIDIA Docs should map to these modules:

ansible/roles/vendor_nvidia_bluefield/

ansible/roles/switchdev_setup/

ansible/roles/ovs_setup/

ansible/roles/qemu_vdpa_setup/

docs/vendor-readiness/nvidia-bluefield.md

tests/vendor/nvidia/

tests/offload-validation/

In other words, it belongs inside the **NVIDIA backend**, not at the top-level architecture.

**Is it equivalent to the “common datapath layer” in our plan?**

**Partially, yes.**

It corresponds strongly to the common datapath concepts we listed:

- switchdev
- E-Switch
- VF/SF traffic steering
- representor ports
- vDPA
- bridge offload
- hardware switching offload

Those are all central to the lower-level datapath. DOCA Switching - NVIDIA Docs explicitly covers these NVIDIA-specific switching capabilities. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

But our common datapath layer must abstract similar concepts across vendors:

NVIDIA BlueField:

DOCA Switching, OVS-DOCA, DOCA Flow

Intel IPU:

IPU/OPI/DPU Operator model, vendor IPU SDK path

Marvell OCTEON:

DAO OVS offload, DPDK graph, DPDK representors

AMD Pensando:

ionic/ionic_rdma discovery, VF/SR-IOV/representor/OPI feasibility

So DOCA Switching - NVIDIA Docs is a **concrete NVIDIA realization** of the lower-level concepts.

**Is it equivalent to OVS-DOCA?**

Not exactly. It is related, but not the same.

DOCA Switching - NVIDIA Docs is broader foundational switching documentation: switchdev, representor model, E-Switch behavior, vDPA, bridge offload, and link aggregation. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

OVS Acceleration with DOCA datapath - OVN-Kubernetes is more specifically about **OVS-DOCA**, where OVS keeps its usual control interfaces while the datapath uses DOCA Flow instead of DPDK rte_flow, with stated motivations such as performance, scalability, connection tracking scale, optimized OVS datapath offloads, and better use of the DOCA platform. [\[ovn-kubernetes.io\]](https://ovn-kubernetes.io/features/hardware-offload/ovs-doca/)

So the NVIDIA stack is more like:

DOCA Switching

\= Foundational switchdev / representor / E-Switch / vDPA / bridge offload model

OVS-DOCA

\= OVS datapath implementation that uses DOCA Flow for NVIDIA offloads

DOCA Platform / DPF

\= Higher-level provisioning and service orchestration for BlueField DPUs

I also found GitHub - NVIDIA/doca-platform, which describes NVIDIA DOCA Platform as managing provisioning and service orchestration for BlueField DPUs. [\[github.com\]](https://github.com/NVIDIA/doca-platform)

**Why this distinction matters for your project**

If you build only with DOCA Switching - NVIDIA Docs, you will likely get a strong **NVIDIA BlueField offload demonstration**, but you will not yet have:

- AMD Pensando support
- Intel IPU support
- Marvell OCTEON support
- a common inventory model
- an OPI-style abstraction layer
- OpenStack off-path SmartNIC support
- OVN-Kubernetes two-cluster DPU mode across vendors
- common certification tests
- common telemetry and lifecycle contracts

That is why I would position it like this:

DOCA Switching is not the project.

DOCA Switching is the NVIDIA datapath backend for the project.

**Practical recommendation for your Cursor repo**

In the plan file we created, I would add a new section under the NVIDIA workstream:

NVIDIA backend mapping:

1\. DOCA Switching

\- switchdev mode

\- E-Switch

\- PF/VF/SF representors

\- vDPA

\- bridge offload

\- LAG

2\. OVS-DOCA

\- OVS datapath acceleration

\- DOCA Flow backend

\- OpenFlow-compatible control interface

3\. DOCA Platform / DPF

\- provisioning

\- service orchestration

\- BlueField lifecycle integration

Then keep the top-level project architecture vendor-neutral.

**Final answer**

**No, it is not the same.** DOCA Switching - NVIDIA Docs is a **NVIDIA-specific hardware switching/offload foundation** for BlueField and ConnectX. It is highly relevant and should be used as the **NVIDIA backend implementation guide**. Our open-source vDSE-equivalent plan is broader: it defines a multi-vendor framework using KVM/QEMU, OVS/OVN, OVN-Kubernetes, OpenStack, OPI-style APIs, and separate backend tracks for AMD, NVIDIA, Intel, and Marvell.

So, its still the same "usecase" but with single vendor, right? The use-case of DPU is still the same, right?

Yes, **you are right**: it is broadly the **same DPU use case**, but **implemented for a single vendor ecosystem**, NVIDIA BlueField/ConnectX.

The common use case is:

Move infrastructure networking work away from the host CPU

and run/accelerate it through the DPU or SmartNIC datapath.

That includes:

VMs / containers

|

v

VF / SF / virtio / vDPA interface

|

v

representor port

|

v

OVS / Linux bridge / control software

|

v

DPU E-Switch / hardware steering / packet pipeline

|

v

physical network

For NVIDIA, **DOCA Switching** is NVIDIA’s implementation of that DPU switching/offload use case. It explains switchdev mode, the E-Switch, PF/VF/SF representors, vDPA, bridge offload, and LAG for BlueField and ConnectX platforms. The NVIDIA documentation explicitly says BlueField and ConnectX use hardware-based switching offloads and that complex switching logic is offloaded to the E-Switch for high-performance network virtualization with low CPU overhead. [\[networking...nvidia.com\]](https://networking-docs.nvidia.com/doca/archive/3-4-0/doca-switching)

So your statement is correct if phrased like this:

**The DPU use case is the same: offload virtual networking/switching from the host CPU to the DPU. DOCA Switching is NVIDIA’s single-vendor implementation of that use case. Our open-source vDSE-equivalent plan generalizes the same use case across NVIDIA, AMD, Intel, and Marvell.**

The difference is **scope**, not the fundamental use case.

Same use case:

DPU-assisted infrastructure networking offload

Different scope:

DOCA Switching = NVIDIA-only implementation substrate

Open Distributed Services Engine = multi-vendor framework

A more precise comparison:

DOCA Switching

\= NVIDIA BlueField/ConnectX way of doing:

\- switchdev

\- E-Switch

\- representors

\- VF/SF traffic steering

\- vDPA

\- bridge offload

\- hardware switching acceleration

Open-source vDSE-equivalent framework

\= vendor-neutral architecture that wants to support:

\- NVIDIA through DOCA Switching / OVS-DOCA

\- Intel through IPU / OPI / DPU Operator style integration

\- Marvell through OCTEON / DAO OVS offload

\- AMD through Pensando driver/RDMA/discovery and possible OPI or OVS integration

One important nuance: **DOCA Switching itself is lower-level than VMware vDSE/NSX**. VMware vDSE includes lifecycle, vCenter/vLCM integration, ESXi-on-DPU, NSX policy, routing, DFW, and observability. DOCA Switching focuses on the NVIDIA datapath mechanics: how the DPU/SmartNIC switching model works. For a fuller NVIDIA-side equivalent, you would combine:

DOCA Switching

\+ OVS-DOCA

\+ DOCA Platform / DPF

\+ OVN-Kubernetes or OpenStack integration

OVS-DOCA is the piece that brings OVS datapath acceleration using DOCA Flow while preserving OVS-style control interfaces. NVIDIA DOCA Platform is the higher-level BlueField provisioning and service orchestration layer. [\[ovn-kubernetes.io\]](https://ovn-kubernetes.io/features/hardware-offload/ovs-doca/) [\[github.com\]](https://github.com/NVIDIA/doca-platform)

So the clean final answer is:

**Yes, same broad DPU use case. But DOCA Switching is NVIDIA-specific and mostly datapath-focused. Our plan is to take that same DPU infrastructure-offload use case and make a multi-vendor, open framework around it.**