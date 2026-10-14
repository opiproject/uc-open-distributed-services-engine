# Operations

**New to ODSE?** Start at [../getting-started.md](../getting-started.md)
(short) or [../user-guide.html](../user-guide.html) (full HTML). For the
full documentation map (which docs are for users vs. contributors), see
[`../README.md`](../README.md).

Audience note: most files here are **runbooks for users** (first-run
guides, readiness checks, mapping/cleanup how-tos). A few
(`milestone-backlog.md`, `vendor-implementation-roadmap.md`,
`vendor-maturity-execution-plan.md`, `first-implementation-plan.md`) are
**planning/roadmap docs for contributors** that live here for historical
reasons — they read status, not steps.

Day-2 operational documentation for running ODSE in the field.

Expected contents:

- Runbooks for DPU/host provisioning, upgrades, and rollback
- Troubleshooting guides (SR-IOV/switchdev, OVS/OVN, vDPA, OPI services)
- Monitoring and alerting guidance built on `tools/telemetry-collector`
- Capacity planning and firmware/driver lifecycle management

Documents:

- [`../getting-started.md`](../getting-started.md) — operator start-here
  (dry-run, vendor pick, when `--apply` is still no-go)
- [`user-doc-flow.md`](user-doc-flow.md) — conditional flowchart (use
  case → vendor → DPU/ACC ready?) plus what OPI this lab actually uses
- [`milestone-backlog.md`](milestone-backlog.md) — M0–M9 stories, acceptance,
  tests (skeletons in git ≠ milestone done)
- [`first-implementation-plan.md`](first-implementation-plan.md) — ordered
  steps 1–12 (docs → OVS → VFIO/vDPA → NVIDIA/AMD → Intel/Marvell docs);
  hardware optional
- [`vendor-implementation-roadmap.md`](vendor-implementation-roadmap.md) —
  skeleton plugins → working backends (seven phases per vendor + rules)
- [`vf-to-representor-mapping.md`](vf-to-representor-mapping.md) — using
  `tools/topology-discovery/vf_to_representor_map.py`: flags, diagnostic
  output, and per-vendor discovery gaps

VM-to-VM single-DPU lab use case (`tools/labctl.py`):

- [`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md) —
  end-to-end runbook, one server/one DPU/two VMs/two VFs
- [`labctl-usage.md`](labctl-usage.md) — `labctl.py` stages, dry-run/apply
  gating, run directories, reading stage JSON
- [`lab-state-contract.md`](lab-state-contract.md) — result directory
  filenames, atomic writes, `STAGE_MISSING_DEPENDENCY`
- [`vm-image-preparation.md`](vm-image-preparation.md) — building the two
  guest images `create-vms` boots
- [`vm-vfio-assignment.md`](vm-vfio-assignment.md) — libvirt define/start
  and VFIO VF attach (dry-run default)
- [`iommu-vfio-host-setup.md`](iommu-vfio-host-setup.md) — enabling
  IOMMU on the kernel command line (Debian/Ubuntu/RHEL-family) before
  `attach-vfs` will work
- [`configure-forwarding.md`](configure-forwarding.md) — the single
  forwarding control point (`labctl vm2vm configure-forwarding`)
- [`../test-plan/result-report-template.md`](../test-plan/result-report-template.md) /
  [`../../tests/results/README.md`](../../tests/results/README.md) —
  `tools/report_generator.py` result reports, section-by-section
- [`cleanup-and-rollback.md`](cleanup-and-rollback.md) — `tools/lab_cleanup.py`
  stages, safety rules, and manual recovery steps
- [`preflight-readiness.md`](preflight-readiness.md) — read-only audit before
  a hardware dry-run (`tools/preflight_readiness.py`)
- [`first-run-nvidia-bluefield-dpu-mode.md`](first-run-nvidia-bluefield-dpu-mode.md)
  — NVIDIA DPU-mode first hardware-adjacent dry-run
- [`nvidia-bluefield-dpu-bringup.md`](nvidia-bluefield-dpu-bringup.md) —
  preparing the BlueField Arm side (BFB/DOCA image, activating OVS-Kernel
  vs. OVS-DOCA) and confirming both datapath types are actually available,
  before any of the above can be filled in with real values
- [`marvell-dao-readiness.md`](marvell-dao-readiness.md) /
  [`marvell-dpdk-representor-mapping.md`](marvell-dpdk-representor-mapping.md) /
  [`marvell-dao-cleanup.md`](marvell-dao-cleanup.md) — Marvell DAO/DPDK gate
- [`intel-ipu-readiness.md`](intel-ipu-readiness.md) /
  [`intel-vm-to-vm-capability-detection.md`](intel-vm-to-vm-capability-detection.md) /
  [`first-run-intel-vm2vm.md`](first-run-intel-vm2vm.md) /
  [`first-run-intel-network-function-offload.md`](first-run-intel-network-function-offload.md)
  — Intel OPI/P4Runtime VM-to-VM path (discovery-first) and the separate,
  more mature `labctl intel-nf` network-function-offload path
- [`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) — preparing the
  IPU's ACC (installing/starting `infrap4d`/OVS, discovering real P4
  table/action and representor names) and validating it's ready, before
  any of the above can be filled in with real values
- [`intel-acc-dh4-triage-session.md`](intel-acc-dh4-triage-session.md) —
  hands-on procedure for the open "ACC never appears" issue on DH4
  (the step-by-step companion to the runbook's §6.1.2)
- [`amd-pensando-readiness.md`](amd-pensando-readiness.md) /
  [`amd-pensando-sriov-representor-switchdev-discovery.md`](amd-pensando-sriov-representor-switchdev-discovery.md)
  — AMD discovery-first (`labctl amd-discovery`)
- [`cross-vendor-readiness-matrix.md`](cross-vendor-readiness-matrix.md) —
  go/no-go report before `--apply`
- [`dpu-remote-execution.md`](dpu-remote-execution.md) — SSH to BlueField Arm
  OS for DPU-mode OVS (dry-run default)
- [`nvidia-dpu-side-ovs-validation.md`](nvidia-dpu-side-ovs-validation.md) —
  DPU-side OVS / OVS-DOCA checks before prepare-ovs
- [`dpu-side-vf-to-representor-mapping.md`](dpu-side-vf-to-representor-mapping.md)
  — map host VFs to DPU representors (`pf0vf0`, not host sysfs / `eth0v1`)
- [`vendor-maturity-execution-plan.md`](vendor-maturity-execution-plan.md) —
  Stage 0–9 maturity model per vendor (NVIDIA/Marvell/Intel/AMD) for this
  specific lab use case, plus the living status table

