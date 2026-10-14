# Architecture Documentation

**Audience: contributors.** These are design/decision documents, not
runbooks. If you are trying to run the lab, go to
[`../getting-started.md`](../getting-started.md) instead. See
[`../README.md`](../README.md) for the full documentation map.

Design documents for the Open Distributed Services Engine (ODSE) reference
architecture: system context diagrams, control-plane/data-plane separation,
DPU/IPU offload models, and component interaction across Linux/KVM, OVS/OVN,
OVN-Kubernetes, OpenStack, and the OPI-style vendor API layer.

Expected contents:

- High-level system architecture and component diagrams
- Data plane designs (SR-IOV, switchdev, representor ports, vDPA)
- Control plane designs (OVN, OVN-Kubernetes, Neutron/Nova integration)
  ([`openstack-offpath-dpu.md`](openstack-offpath-dpu.md))
- OPI-style API contract and vendor backend plugin model
  ([`opi-alignment.md`](opi-alignment.md), [`api-model.md`](api-model.md),
  VM-to-VM adoption plan [`opi-tie-in-roadmap.md`](opi-tie-in-roadmap.md);
  meeting note [`../presentations/opi-technical-meeting-api-gaps.md`](../presentations/opi-technical-meeting-api-gaps.md))
- Design decisions and trade-off records (ADRs)
- NVIDIA BlueField DPU vs host-switchdev placement
  ([`nvidia-bluefield-control-placement.md`](nvidia-bluefield-control-placement.md))
- Intel IPU OPI / DPU Operator control model
  ([`intel-ipu-control-model.md`](intel-ipu-control-model.md))
