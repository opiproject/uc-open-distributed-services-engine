# vendor-certification

Process and **evidence tree** for certifying AMD Pensando, NVIDIA
BlueField, Intel IPU, and Marvell OCTEON backends against ODSE.

This is **not** a legal certification mark. Levels and pass/fail:
[`docs/test-plan/vendor-certification.md`](../../docs/test-plan/vendor-certification.md).

| Level | Meaning |
|---|---|
| 0 | Inventory only |
| 1 | VF + basic connectivity |
| 2 | OVS/DAO offload baseline (honest evidence) |
| 3 | VM VFIO and/or vDPA |
| 4 | OVN (OpenStack or OVN-Kubernetes) |
| 5 | OPI lifecycle + telemetry (+ L4 for full award) |

Levels are **cumulative** and pinned to SKU/firmware. Kind/simulation
cannot award Level ≥ 1. Capability-matrix `supported` requires a
report directory below plus a maintainer signature.

## Layout (as reports appear)

```text
tests/vendor-certification/
  README.md
  reports/
    nvidia/<YYYYMMDD>-<sku>/REPORT.md
    intel/...
    marvell/...
    amd/...
```

Use the template in `docs/test-plan/vendor-certification.md`. Put
`identity.json`, FN artifacts, checker JSON, and optional
`odse-perf-result` files beside `REPORT.md`. No credentials.

## Wrappers

Automated drivers that call `tests/functional`,
`tests/offload-validation`, and `tests/performance` per vendor are
**TODO**. Until they exist, run the FN/OPI/TM ids by hand and attach
outputs.

OPI conformance notes (planned): `opi-conformance/` — see
`kubernetes/opi/README.md` §8.
