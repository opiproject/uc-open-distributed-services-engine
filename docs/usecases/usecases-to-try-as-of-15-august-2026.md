# Use Cases to Try As of 15 August 2026

## Context

This document summarizes the use cases that can be tried immediately with the current Open Distributed Services Engine repository, after completing the base prompts, vendor implementation prompts, and lab use-case prompts.

Assumption: the repository now includes the following major pieces:

- `tools/labctl.py`
- `tools/vendorctl.py`
- `tools/capability_report.py`
- `tools/topology-discovery/vf_to_representor_map.py`
- `tools/offload-checker/ovs_offload_checker.py`
- `tools/telemetry-collector/dpu_telemetry_collector.py`
- `tools/report_generator.py`
- Vendor backend plugins for:
  - NVIDIA BlueField
  - Marvell OCTEON
  - Intel IPU
  - AMD Pensando
- Lab configuration schemas and examples
- Forwarding intent model
- VM-to-VM single-DPU workflow
- Cleanup and reporting workflow

The primary rule remains:

```text
Run one DPU/IPU at a time.
Start with discovery and dry-run.
Use --apply only after reviewing the planned changes.
```

---

## 1. Read-only DPU/IPU discovery across all vendors

### Goal

Confirm that the framework can detect all available DPU/IPU devices and classify them by vendor and capability.

### What to detect

- AMD Pensando
- NVIDIA BlueField
- Intel IPU
- Marvell OCTEON
- PFs
- PCI addresses
- active drivers
- management interfaces
- SR-IOV capability
- representor visibility
- OVS availability
- vendor-specific hints

### Example commands

```bash
python tools/vendorctl.py discover --vendor nvidia-bluefield --json
python tools/vendorctl.py discover --vendor marvell-octeon --json
python tools/vendorctl.py discover --vendor intel-ipu --json
python tools/vendorctl.py discover --vendor amd-pensando --json
```

Or through the lab workflow:

```bash
python tools/labctl.py vm2vm discover \
  --config config/lab-nvidia-vm2vm.yaml \
  --json
```

### What this proves

```text
The repo can see the hardware and classify what each vendor backend can attempt.
```

### Recommended first vendor

```text
NVIDIA BlueField
```

Reason: the NVIDIA path is likely the easiest first VM-to-VM experiment because the switchdev, E-Switch, representor, and OVS-style model is comparatively straightforward.

---

## 2. Capability report generation

### Goal

Generate a structured report describing what each detected DPU/IPU appears capable of supporting.

### Example command

```bash
python tools/capability_report.py \
  --format markdown \
  --output tests/results/capability-report.md
```

### Expected capability fields

- Vendor detected
- PFs detected
- SR-IOV availability
- switchdev availability
- representor availability
- OVS availability
- vDPA availability
- RDMA availability
- OPI availability
- telemetry availability
- recommended next step

### What this proves

```text
The same repo can compare all 4 DPU/IPU vendors using one common capability model.
```

This is especially important for AMD Pensando and Intel IPU, because those tracks should begin as discovery-first rather than assuming an NVIDIA-style representor path.

---

## 3. Lab configuration validation

### Goal

Validate the YAML configuration before touching hardware.

### Example command

```bash
python tools/labctl.py vm2vm validate-config \
  --config config/lab-nvidia-vm2vm.yaml \
  --json
```

### What should be validated

- Host section
- Vendor section
- DPU section
- OVS section
- VM section
- Forwarding section
- Traffic section
- Validation section
- Safety section

### What this proves

```text
The lab config is complete enough for the staged workflow.
```

---

## 4. Full dry-run of VM-to-VM single-DPU workflow

### Goal

Execute the complete workflow plan without changing the system.

### Example command

```bash
python tools/labctl.py vm2vm run-all-safe \
  --config config/lab-nvidia-vm2vm.yaml \
  --dry-run \
  --json
```

### Expected staged workflow

```text
discover
validate-config
prepare-host
prepare-vfs
prepare-switchdev
discover-representors
prepare-ovs
create-vms
attach-vfs
configure-forwarding
configure-vm-ips
run-traffic
validate-offload
collect-evidence
```

### What this proves

```text
The orchestration flow is wired end-to-end before applying any change.
```

---

## 5. Create two VFs on one selected DPU

### Goal

Create exactly two VFs from one selected PF.

### Dry-run first

```bash
python tools/labctl.py vm2vm prepare-vfs \
  --config config/lab-nvidia-vm2vm.yaml \
  --dry-run
```

### Apply after review

```bash
python tools/labctl.py vm2vm prepare-vfs \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### What this proves

```text
The repo can safely create the VF resources required for VM1 and VM2.
```

### Safety rule

Do not use a PF that carries the management interface or SSH access.

---

## 6. VF-to-representor mapping

### Goal

Map VM-facing VFs to DPU/NIC-side representor ports.

Expected mapping:

```text
VF0 -> representor0
VF1 -> representor1
```

### Example through labctl

```bash
python tools/labctl.py vm2vm discover-representors \
  --config config/lab-nvidia-vm2vm.yaml \
  --json
```

### Direct mapping tool

```bash
python tools/topology-discovery/vf_to_representor_map.py \
  --pf <PF_NAME> \
  --vendor nvidia-bluefield \
  --pretty
```

### What this proves

```text
The framework knows which software-visible representor corresponds to each VM VF.
```

This is the critical bridge between VM assignment and DPU-side forwarding control.

---

## 7. Prepare OVS bridge for the DPU experiment

### Goal

Create a test OVS bridge and attach the discovered representors.

### Dry-run

```bash
python tools/labctl.py vm2vm prepare-ovs \
  --config config/lab-nvidia-vm2vm.yaml \
  --dry-run
```

### Apply

```bash
python tools/labctl.py vm2vm prepare-ovs \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### What this proves

```text
The software control-plane side of VM-to-VM switching is ready.
```

Expected behavior by vendor:

- NVIDIA: OVS or OVS-DOCA style path
- Marvell: DAO / DPDK representor path where supported
- Intel: may return discovery-required
- AMD: may return discovery-required

---

## 8. Create two VMs and assign one VF to each

### Goal

Create two KVM/QEMU VMs and pass one VF into each VM.

Expected assignment:

```text
VM1 -> VF0
VM2 -> VF1
```

### Example commands

```bash
python tools/labctl.py vm2vm create-vms \
  --config config/lab-nvidia-vm2vm.yaml \
  --dry-run
```

```bash
python tools/labctl.py vm2vm create-vms \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

```bash
python tools/labctl.py vm2vm attach-vfs \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### What this proves

```text
The host can expose DPU-created VFs into KVM/QEMU guests.
```

At this point the VMs are traffic endpoints.

---

## 9. Configure forwarding intent

### Goal

Use one user-facing control point to express the intent:

```text
Connect VM1 to VM2.
Allow bidirectional L2 traffic.
Prefer hardware offload if available.
```

### User-facing command

```bash
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-nvidia-vm2vm.yaml \
  --dry-run
```

```bash
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### What this proves

```text
labctl.py is the single user-facing forwarding control point.
```

Internal flow:

```text
labctl.py
  -> ForwardingIntent
  -> VendorBackend.configure_forwarding()
  -> NVIDIA / Marvell / Intel / AMD backend translation
  -> actual DPU/IPU forwarding entity
```

The user should not directly call OVS, DAO, OPI, DOCA, DPU Operator, `tc`, `devlink`, or vendor SDKs for this use case.

---

## 10. VM1-to-VM2 traffic test

### Goal

Send traffic in both directions:

```text
VM1 -> VM2
VM2 -> VM1
```

### Configure VM IPs

```bash
python tools/labctl.py vm2vm configure-vm-ips \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### Run traffic

```bash
python tools/labctl.py vm2vm run-traffic \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### Traffic types

- ping
- iperf3
- optional tcpdump

### What this proves

```text
Functional VM-to-VM communication through the selected DPU path works.
```

Important:

```text
Traffic works does not automatically mean hardware offload is confirmed.
```

---

## 11. Offload validation

### Goal

Determine whether traffic was switched in software or likely/actually offloaded.

### Example command

```bash
python tools/labctl.py vm2vm validate-offload \
  --config config/lab-nvidia-vm2vm.yaml \
  --json
```

### Direct offload checker

```bash
python tools/offload-checker/ovs_offload_checker.py \
  --forwarding-result tests/results/nvidia-bluefield/<timestamp>/configure-forwarding.json \
  --json
```

### Evidence categories

- OVS flows
- TC filters
- ethtool counters
- interface counters
- CPU delta
- vendor telemetry if available

### What this proves

```text
The framework can separate functional connectivity from probable or confirmed hardware offload.
```

---

## 12. Evidence collection and report generation

### Goal

Generate a result report for the lab run.

### Collect evidence

```bash
python tools/labctl.py vm2vm collect-evidence \
  --config config/lab-nvidia-vm2vm.yaml \
  --json
```

### Generate report

```bash
python tools/report_generator.py \
  --input-dir tests/results/nvidia-bluefield/<timestamp>/ \
  --output tests/results/nvidia-bluefield/<timestamp>/result-summary.md
```

### Possible final verdicts

```text
PASS_FUNCTIONAL_ONLY
PASS_OFFLOAD_PROBABLE
PASS_OFFLOAD_CONFIRMED
FAIL_CONNECTIVITY
FAIL_SETUP
INCONCLUSIVE
```

### What this proves

```text
The lab can produce comparable evidence across vendors.
```

---

## 13. Cleanup and rollback

### Goal

Undo the lab state safely.

### Dry-run

```bash
python tools/labctl.py vm2vm cleanup \
  --config config/lab-nvidia-vm2vm.yaml \
  --dry-run
```

### Apply

```bash
python tools/labctl.py vm2vm cleanup \
  --config config/lab-nvidia-vm2vm.yaml \
  --apply
```

### Cleanup should handle

- Stop VMs
- Detach VFs
- Delete OVS bridge
- Remove OVS ports
- Reset `sriov_numvfs` if configured
- Optionally restore switchdev/legacy mode
- Collect final state

### What this proves

```text
The lab can be repeated safely.
```

---

# Recommended first hardware sequence

Start with NVIDIA BlueField:

```bash
python tools/labctl.py vm2vm validate-config --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm discover --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm discover-representors --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm prepare-ovs --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/labctl.py vm2vm prepare-ovs --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm attach-vfs --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm configure-forwarding --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/labctl.py vm2vm configure-forwarding --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm configure-vm-ips --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm run-traffic --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm validate-offload --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm collect-evidence --config config/lab-nvidia-vm2vm.yaml --json
python tools/report_generator.py --input-dir tests/results/nvidia-bluefield/<timestamp>/ --output tests/results/nvidia-bluefield/<timestamp>/result-summary.md
```

---

# Vendor execution order

## First

```text
NVIDIA BlueField
```

Primary target:

```text
VM1 <-> VM2 through E-Switch / representor / OVS or OVS-DOCA path
```

## Second

```text
Marvell OCTEON
```

Primary target:

```text
VM1 <-> VM2 through DAO / DPDK representor / OCTEON hardware path
```

## Third

```text
Intel IPU
```

Primary target:

```text
Discovery, OPI/DPU Operator capability, then decide whether VM-to-VM VF switching is supported
```

## Fourth or parallel discovery

```text
AMD Pensando
```

Primary target:

```text
ionic / ionic_rdma / VF / representor / switchdev / OVS feasibility discovery
```

---

# Final summary

As of 15 August 2026, the repository should be able to attempt:

```text
1. Discover all DPUs/IPUs.
2. Generate capability reports.
3. Validate lab config.
4. Dry-run the complete VM-to-VM workflow.
5. Create VFs on one selected DPU.
6. Discover VF-to-representor mapping.
7. Prepare OVS bridge.
8. Create two VMs.
9. Attach one VF to each VM.
10. Configure forwarding intent using labctl.py.
11. Run VM1 <-> VM2 traffic.
12. Validate offload evidence.
13. Generate result report.
14. Cleanup and repeat for another vendor.
```

