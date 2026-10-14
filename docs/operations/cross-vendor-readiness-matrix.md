# Cross-vendor readiness matrix

Truthful go/no-go across NVIDIA, Marvell, Intel, and AMD. Unknown
capabilities stay discovery-required. Kind is not hardware certification.

```text
python tools/vendor_readiness_matrix.py --configs config/*.yaml --output tests/results/vendor-readiness-matrix.md
python tools/vendor_readiness_matrix.py --configs config/*.yaml --json --output tests/results/vendor-readiness-matrix.json
```

Before any `--apply` hardware workflow, generate and review this matrix.

## Row fields

vendor, profile, intended first use case, control model, OVS location,
representor location, operator/API dependency, VM-to-VM readiness,
VM-to-Pod readiness, offload validation readiness, cleanup readiness,
status, blockers, recommended next action.

## Status values

| Status | Meaning |
|---|---|
| `GO_FOR_HARDWARE_DRY_RUN` | Profile pinned; dry-run labctl is the next step (still not `--apply` by default) |
| `GO_FOR_DISCOVERY_ONLY` | Collect facts; do not claim forwarding |
| `BLOCKED_MISSING_CONTROL_PATH` | No representor / OPI / DAO control path |
| `BLOCKED_MISSING_REMOTE_EXECUTION` | NVIDIA DPU SSH/placement incomplete |
| `BLOCKED_MISSING_VENDOR_RUNTIME` | DAO/DPDK/OPI runtime missing |
| `NOT_READY` | Profile invalid or device missing |

## Expected defaults on example YAML

| Vendor | Typical status |
|---|---|
| NVIDIA BlueField DPU mode | `GO_FOR_HARDWARE_DRY_RUN` after R0–R10 |
| Marvell OCTEON DAO | `GO_FOR_DISCOVERY_ONLY` or `BLOCKED_MISSING_VENDOR_RUNTIME` until DAO/DPDK samples pass |
| Intel IPU | `GO_FOR_DISCOVERY_ONLY` (use `labctl intel-nf`; VM-to-VM unproven) |
| AMD Pensando | `GO_FOR_DISCOVERY_ONLY` or `BLOCKED_MISSING_CONTROL_PATH` until a control path is proven |
