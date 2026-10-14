# Preflight readiness

Read-only audit before any VM-to-VM DPU/IPU hardware workflow.
It does **not** set eswitch, create VFs, define VMs, or SSH to the DPU.

```text
python tools/preflight_readiness.py --config config/lab-nvidia-vm2vm.example.yaml --json
python tools/preflight_readiness.py --config config/lab-nvidia-vm2vm.example.yaml --markdown
```

`labctl.py` remains the operator control point. This tool only answers
whether the **repository + config** are safe to dry-run.

## Status values

| Status | Meaning |
|---|---|
| `READY` | All twelve categories pass and `safety.dry_run_default` is false. Still requires `--apply` for mutations. |
| `READY_FOR_DRY_RUN_ONLY` | Config and safety gates are usable; DPU-mode, remote exec, placeholders, or `dry_run_default: true` block `--apply`. |
| `NOT_READY` | Invalid config or management-interface protection is off. Do not run labctl beyond `validate-config`. |

## Categories

1. Lab config validity (`odse-lab-vm2vm/v1alpha1`)
2. Vendor profile validity
3. NVIDIA BlueField DPU-mode pinning (`nvidia-bluefield-dpu-mode`)
4. DPU/IPU management reachability **fields** (no ping/SSH)
5. DPU-side command execution availability (`plugins/base/remote.py`)
6. DPU-side OVS/OVS-DOCA validation support (`plugins/nvidia_bluefield/dpu_ovs.py`)
7. VM image helpers and libvirt template
8. VF-to-representor mapping support
9. Stage output / state passing (`labctl` / `lab_state`)
10. Cleanup coverage
11. Evidence / report generation
12. Management interface safety (`forbid_management_interface_changes`)
13. Vendor-specific gate: Marvell DAO/DPDK, Intel OPI, or AMD ionic
    (`check_vendor_specific_readiness`)

Missing files from later readiness prompts are reported honestly
as blocking for `READY`, not as a fake pass. NVIDIA remains the first
hardware dry-run target; other vendors contribute discovery/DAO/OPI
status without pretending they are equivalent.

## Safety

- Default is dry-run reporting only.
- `--apply` is not a flag on this tool.
- Host `management_interface` is never modified.
