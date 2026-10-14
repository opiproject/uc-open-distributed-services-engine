# Vendor Readiness

**Audience: contributors/maintainers.** These are roadmap and
gap-tracking documents — "what's proven, what's discovery-required, what
gaps remain" per vendor. They are **not** step-by-step runbooks. If you
are trying to actually run something, go to
[`../getting-started.md`](../getting-started.md) and its per-vendor
first-run docs under [`../operations/`](../operations/README.md) instead.
See [`../README.md`](../README.md) for the full documentation map.

Per-vendor readiness matrices tracking integration status for each supported
DPU/IPU backend: AMD Pensando, NVIDIA BlueField, Intel IPU, and Marvell
OCTEON.

Expected contents:

- One readiness matrix document per vendor (firmware/driver versions,
  switchdev and SR-IOV support, vDPA support, OPI API coverage, known gaps)
- Cross-vendor feature parity comparison
- Certification status linked to `tests/vendor-certification/`
  (process: [`../test-plan/vendor-certification.md`](../test-plan/vendor-certification.md))

Documents:

- [`vendor-capability-matrix.md`](vendor-capability-matrix.md) — cross-vendor ratings
- [`nvidia-bluefield.md`](nvidia-bluefield.md) — NVIDIA BlueField workstream (placeholders; no frozen DOCA commands)
- [`nvidia-bluefield-implementation.md`](nvidia-bluefield-implementation.md) — discover → switchdev → OVS-TC → optional OVS-DOCA flow
- [`intel-ipu-e2100.md`](intel-ipu-e2100.md) — Intel IPU E2100 workstream (OPI/OpenShift-first; datapath mostly discovery-required)
- [`intel-ipu-implementation.md`](intel-ipu-implementation.md) — discovery, Redfish, OPI daemon, DPU Operator (not OVS-DOCA)
- [`marvell-octeon.md`](marvell-octeon.md) — Marvell OCTEON / DAO workstream (DPDK representors vs kernel switchdev; generic OVS vs DAO split)
- [`marvell-octeon-implementation.md`](marvell-octeon-implementation.md) — DPDK representors, DAO OVS, first-packet vs post-offload, validation
- [`amd-pensando.md`](amd-pensando.md) — AMD Pensando DSC workstream (discovery-first; do not assume OVS offload)
- [`amd-pensando-implementation.md`](amd-pensando-implementation.md) — ionic/RDMA discovery and gap analysis template
- [`amd-pensando-forwarding-gap-template.md`](amd-pensando-forwarding-gap-template.md) — required gap-report fields and statuses
- [`sample-capability-report.md`](sample-capability-report.md) — dummy output shape for `tools/capability_report.py` (not lab evidence)
- Cross-vendor go/no-go: [`../operations/cross-vendor-readiness-matrix.md`](../operations/cross-vendor-readiness-matrix.md)

