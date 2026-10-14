# Intel IPU readiness (OPI / DPU Operator)

Intel is **not** NVIDIA OVS representor forwarding unless discovery proves
that path. Default profile: `intel-ipu-opi-dpu-operator`.

```text
python tools/preflight_readiness.py --config config/lab-intel-vm2vm.example.yaml --json
```

## Status values

| Status | Meaning |
|---|---|
| `INTEL_READY_FOR_OPI_DRY_RUN` | OPI configured and VM-to-VM proven in samples |
| `INTEL_OPI_ENDPOINT_REQUIRED` | `require_opi` and no `opi.endpoint` / `dpu.opi_endpoint` |
| `INTEL_DPU_OPERATOR_NOT_FOUND` | operator required and kubeconfig/install not found |
| `INTEL_IPU_MANAGEMENT_UNREACHABLE` | OPI/Redfish marked unreachable in samples |
| `INTEL_VM_TO_VM_DISCOVERY_REQUIRED` | OPI may be configured; VF L2 path not proven |
| `INTEL_NOT_READY` | no IPU device, or `allow_ovs_assumption: true` |

`allow_ovs_assumption: true` is refused. First practical path is
`labctl intel-nf`, not VM-to-VM OVS.

See `config/lab-intel-vm2vm.example.yaml` for `intel`, `opi`, `dpu_operator`,
and `ipu` fields. Probes are read-only (kubeconfig path existence only).
