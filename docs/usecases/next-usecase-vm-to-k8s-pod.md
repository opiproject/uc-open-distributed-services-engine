# Next Use Case to Add: VM to Kubernetes Pod Through One DPU/IPU

## Purpose

This document describes the recommended next use case to add after validating the VM-to-VM single-DPU workflow.

Recommended next use case:

```text
VM-to-Kubernetes-Pod through one selected DPU/IPU
```

This extends the current VM-to-VM model without jumping too far into full Kubernetes service networking, multi-node routing, or DPU-hosted network functions.

---

## 1. Why VM-to-Pod is the right next use case

The current use case is:

```text
VM1 VF0 -> DPU/IPU datapath -> VM2 VF1
```

The next use case becomes:

```text
VM1 VF0 -> DPU/IPU datapath -> Kubernetes Pod interface
```

The conceptual change is:

```text
Endpoint B is no longer a VM VF.
Endpoint B is now a Kubernetes pod interface.
```

This allows reuse of the existing forwarding model:

```text
ForwardingIntent
VendorBackend.configure_forwarding()
labctl.py as the single user-facing control point
```

---

## 2. Target topology

### High-level topology

```text
KVM/QEMU VM
  |
  | SR-IOV VF or vDPA-backed interface
  v
DPU/IPU forwarding entity
  |
  | VF / representor / CNI attachment / secondary network
  v
Kubernetes worker node
  |
  v
Pod interface
```

### More detailed topology

```text
Server
├── VM1
│   └── VF0 passed through to VM
│
├── Kubernetes worker node
│   └── Pod1
│       └── secondary interface or pod network interface
│
└── DPU/IPU
    ├── PF / uplink
    ├── VF0 for VM1
    ├── pod-facing VF or host-device interface
    ├── representor for VM endpoint
    ├── representor or attachment for pod endpoint
    └── vendor forwarding entity
```

---

## 3. Initial scope

### In scope

- One physical server
- One selected DPU/IPU at a time
- One KVM/QEMU VM
- One Kubernetes pod
- One VM-facing VF
- One pod-facing attachment
- L2 connectivity between VM and Pod
- ping and iperf3 traffic
- forwarding intent through `labctl.py`
- offload evidence collection where possible

### Out of scope for first VM-to-Pod version

- Kubernetes Service abstraction
- kube-proxy replacement
- multi-node Kubernetes routing
- NetworkPolicy offload
- service mesh
- DPU-hosted firewall or proxy
- external network routing
- multi-tenant isolation
- pod migration
- production OpenShift integration

---

## 4. Expected user-facing workflow

The user should still use `labctl.py` as the single control point.

Example command pattern:

```bash
python tools/labctl.py vm2pod validate-config \
  --config config/lab-nvidia-vm-to-pod.yaml \
  --json
```

```bash
python tools/labctl.py vm2pod discover \
  --config config/lab-nvidia-vm-to-pod.yaml \
  --json
```

```bash
python tools/labctl.py vm2pod configure-forwarding \
  --config config/lab-nvidia-vm-to-pod.yaml \
  --apply
```

```bash
python tools/labctl.py vm2pod run-traffic \
  --config config/lab-nvidia-vm-to-pod.yaml \
  --apply
```

The user should not directly call OVS, DAO, OPI, DOCA, DPU Operator, CNI tools, `tc`, `devlink`, or vendor SDKs as the primary control point.

---

## 5. Forwarding intent extension

The current VM-to-VM forwarding model should be extended to support endpoint types.

### Existing VM-to-VM style

```yaml
forwarding:
  name: vm1-to-vm2
  type: l2-vm-to-vm
  mode: bridge
  endpoints:
    - name: vm1
      type: vm
      vm: vm1
      vf_index: 0
    - name: vm2
      type: vm
      vm: vm2
      vf_index: 1
  policy:
    action: allow
    bidirectional: true
  offload:
    requested: true
    fallback_to_software: true
```

### New VM-to-Pod style

```yaml
forwarding:
  name: vm1-to-pod1
  type: l2-vm-to-pod
  mode: bridge
  endpoints:
    - name: vm1
      type: vm
      vm: vm1
      vf_index: 0
    - name: pod1
      type: pod
      namespace: dpu-test
      pod_selector:
        app: pod1
      network_attachment: dpu-net
  policy:
    action: allow
    bidirectional: true
  offload:
    requested: true
    fallback_to_software: true
```

---

## 6. New files to add

### Config files

```text
config/lab-vm-to-pod-single-dpu.schema.yaml
config/lab-nvidia-vm-to-pod.example.yaml
config/lab-marvell-vm-to-pod.example.yaml
config/lab-intel-vm-to-pod.example.yaml
config/lab-amd-vm-to-pod.example.yaml
```

### Kubernetes files

```text
kubernetes/network-functions/test-pods/iperf3-server-pod.yaml
kubernetes/network-functions/test-pods/ping-test-pod.yaml
kubernetes/network-functions/test-pods/nad-sriov-template.yaml
kubernetes/network-functions/test-pods/nad-host-device-template.yaml
```

### Tools

```text
tools/k8s_endpoint_resolver.py
```

### Documentation

```text
docs/architecture/vm-to-pod-forwarding.md
docs/operations/lab-runbook-vm-to-pod-single-dpu.md
docs/test-plan/vm-to-pod-validation.md
```

### Tests

```text
tests/unit/test_k8s_endpoint_resolver.py
tests/unit/test_vm_to_pod_forwarding_intent.py
tests/functional/test_vm_to_pod_workflow.py
```

---

## 7. Required implementation steps

## Step 1: Extend endpoint model

Add endpoint type support:

```text
vm
pod
host-interface
external
```

Update `ForwardingEndpoint` to include fields for pod endpoints:

```text
namespace
pod_name
pod_selector
network_attachment
pod_ip
node_name
container_interface
host_interface
representor
```

Expected result:

```text
The same ForwardingIntent model can represent VM-to-VM and VM-to-Pod.
```

---

## Step 2: Add VM-to-Pod lab config schema

Create:

```text
config/lab-vm-to-pod-single-dpu.schema.yaml
```

The schema should include:

```yaml
host:
  name:
  management_interface:

vendor:
  type:

dpu:
  pf_name:
  pf_pci:
  num_vfs:

kubernetes:
  kubeconfig:
  namespace:
  cni_mode: multus | ovn-kubernetes | sriov-cni | host-device
  network_attachment_definition:
  pod_image:
  pod_name:
  pod_ip_cidr:

vm:
  name:
  image:
  vf_index:
  ip_cidr:

forwarding:
  type: l2-vm-to-pod
  endpoints:
    - type: vm
    - type: pod
```

Expected result:

```text
A VM-to-Pod lab can be configured in one YAML file.
```

---

## Step 3: Add Kubernetes endpoint resolver

Create:

```text
tools/k8s_endpoint_resolver.py
```

Responsibilities:

```text
Read kubeconfig
Find pod by namespace/name/selector
Get pod IP
Get node name
Get network attachments
Get pod interface information where visible
Get SR-IOV assigned device info if visible
Map pod endpoint to host/DPU-side endpoint where possible
```

Expected output:

```json
{
  "namespace": "dpu-test",
  "pod": "pod1",
  "node": "worker-1",
  "pod_ip": "192.168.200.20",
  "network_attachment": "dpu-net",
  "host_interface": null,
  "representor": null,
  "confidence": "low|medium|high"
}
```

Expected result:

```text
The framework can resolve Kubernetes pod endpoints into forwarding endpoints.
```

---

## Step 4: Add test pod manifests

Create:

```text
kubernetes/network-functions/test-pods/iperf3-server-pod.yaml
kubernetes/network-functions/test-pods/ping-test-pod.yaml
```

The test pod image should include:

```text
iproute2
iputils-ping
iperf3
tcpdump
bash or sh
```

Create network attachment templates:

```text
kubernetes/network-functions/test-pods/nad-sriov-template.yaml
kubernetes/network-functions/test-pods/nad-host-device-template.yaml
```

Expected result:

```text
The repo can create a predictable pod endpoint for traffic testing.
```

---

## Step 5: Add `labctl.py vm2pod` workflow

Add command group:

```bash
python tools/labctl.py vm2pod <stage> --config <config-file>
```

Stages:

```text
discover
validate-config
prepare-host
prepare-vfs
prepare-switchdev
discover-representors
prepare-ovs
create-vm
attach-vf
prepare-k8s-namespace
create-network-attachment
create-test-pod
resolve-pod-endpoint
configure-forwarding
configure-vm-ip
run-traffic
validate-offload
collect-evidence
cleanup
```

Expected result:

```text
VM-to-Pod becomes a first-class lab workflow, not a manual side experiment.
```

---

## Step 6: Extend vendor backends for VM-to-Pod

Add method or extend existing method:

```python
def configure_forwarding(intent):
    ...
```

The backend should detect endpoint types:

```text
vm endpoint
pod endpoint
```

### NVIDIA expected implementation

```text
VM endpoint -> VF representor
Pod endpoint -> pod network attachment / host-device / SR-IOV CNI endpoint
Backend connects the two through bridge or vendor offload path.
```

### Marvell expected implementation

```text
VM endpoint -> DPDK representor
Pod endpoint -> DPDK representor or host-device endpoint where available
DAO OVS-offload path handles forwarding if supported.
```

### Intel expected implementation

```text
Use DPU Operator / OPI-style path where available.
If pod endpoint maps to DPU-managed network attachment, configure through that path.
Otherwise return discovery-required.
```

### AMD expected implementation

```text
Discovery-first.
If representors or supported control path exist, configure through common bridge path.
Otherwise return discovery-required.
```

Expected result:

```text
The same VM-to-Pod intent can be attempted across vendors, even if some return discovery-required.
```

---

## Step 7: Add VM-to-Pod traffic runner

Extend `labctl.py vm2pod run-traffic`.

Traffic directions:

```text
VM -> Pod ping
Pod -> VM ping
VM -> Pod iperf3
Pod -> VM iperf3
```

Required capabilities:

```text
SSH into VM
kubectl exec into pod
start iperf3 server on one side
run iperf3 client on the other side
collect logs
```

Expected result:

```text
The framework can prove basic VM-to-Pod connectivity.
```

---

## Step 8: Add VM-to-Pod offload validation

Collect evidence from:

```text
VM interface counters
Pod interface counters
Kubernetes node counters
OVS flows
TC filters
ethtool counters
DPU telemetry
Kubernetes pod logs
```

Output:

```text
tests/results/<vendor>/<timestamp>/vm-to-pod-offload-validation.json
tests/results/<vendor>/<timestamp>/vm-to-pod-offload-validation.md
```

Expected result:

```text
The framework can distinguish VM-to-Pod connectivity from DPU/IPU offload evidence.
```

---

## Step 9: Add VM-to-Pod cleanup

Cleanup should handle:

```text
Stop traffic
Delete test pod
Delete NetworkAttachmentDefinition if created by lab
Detach VM VF
Stop/undefine VM if configured
Delete OVS ports
Delete OVS bridge if lab-created
Reset VFs if configured
Collect final state
```

Expected result:

```text
The VM-to-Pod lab is repeatable.
```

---

## Step 10: Add VM-to-Pod result reporting

Extend `tools/report_generator.py` to support:

```text
vm-to-pod-result-summary.md
vm-to-pod-result-summary.json
```

Report sections:

```text
Test metadata
VM endpoint
Pod endpoint
Kubernetes namespace
Network attachment
DPU vendor
PF/VF mapping
Representor mapping
Forwarding intent
Traffic results
Offload evidence
Pod logs
VM logs
Final verdict
Next actions
```

Verdict values:

```text
PASS_FUNCTIONAL_ONLY
PASS_OFFLOAD_PROBABLE
PASS_OFFLOAD_CONFIRMED
FAIL_VM_TO_POD_CONNECTIVITY
FAIL_POD_TO_VM_CONNECTIVITY
FAIL_SETUP
INCONCLUSIVE
```

Expected result:

```text
VM-to-Pod results can be compared with VM-to-VM results.
```

---

# New prompt set to create

Create a new file:

```text
cursor-prompts-vm-to-pod-usecase.md
```

Suggested prompts:

```text
K1. Add VM-to-Pod config schema
K2. Extend ForwardingIntent endpoint model
K3. Add Kubernetes endpoint resolver
K4. Add test pod manifests
K5. Add labctl vm2pod workflow
K6. Add vendor backend VM-to-Pod support
K7. Add VM-to-Pod traffic runner
K8. Add VM-to-Pod offload validation
K9. Add VM-to-Pod cleanup
K10. Add VM-to-Pod result reporting
```

---

# Recommended execution order for next use case

Do not add VM-to-Pod until at least one VM-to-VM run is successful.

Recommended order:

```text
1. VM-to-VM on NVIDIA BlueField
2. Generate full VM-to-VM evidence report
3. Repeat VM-to-VM for Marvell if available
4. Run Intel and AMD discovery-first reports
5. Add VM-to-Pod use case
6. Run VM-to-Pod on NVIDIA first
7. Extend to Marvell, Intel, AMD as capabilities allow
```

---

# Future use cases after VM-to-Pod

After VM-to-Pod, the next logical progression is:

```text
1. Pod-to-Pod through DPU
2. VM-to-Kubernetes-Service
3. VM-to-Pod with ACL/firewall policy
4. VM-to-Pod through DPU-hosted network function
5. Multi-node DPU path
6. Multi-vendor comparative report
```

---

# Final recommendation

The immediate next use case should be:

```text
VM-to-Kubernetes-Pod through one selected DPU/IPU
```

But only after this is true:

```text
VM-to-VM works at least once.
VM-to-VM report is generated.
Forwarding intent is proven through labctl.py.
Cleanup works.
```

Reason:

```text
VM-to-VM proves the core primitives:
  VF creation
  VF assignment
  representor mapping
  forwarding intent
  traffic testing
  offload validation
  cleanup

VM-to-Pod adds Kubernetes endpoint resolution and CNI complexity on top.
```

