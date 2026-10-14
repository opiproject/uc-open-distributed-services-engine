# Configuring VM-to-VM forwarding

Status: Draft  
Scope: How to use the single forwarding control point for the VM-to-VM
single-DPU lab use case.  
Companions: [`../architecture/forwarding-intent-model.md`](../architecture/forwarding-intent-model.md),
[`labctl-usage.md`](labctl-usage.md), [`../../config/README-lab-vm2vm.md`](../../config/README-lab-vm2vm.md)

Call `tools/labctl.py`, not vendor-specific tools directly, for this use
case. Do not run `ovs-vsctl`, `ovs-ofctl`, a DAO CLI, `devlink`, `tc`, or
an OPI client by hand to connect the two lab VMs — see
[forwarding-intent-model.md](../architecture/forwarding-intent-model.md)
for why.

## VM1 to VM2 bridge forwarding

Every example config (`config/lab-*-vm2vm.example.yaml`) ships a
`forwarding:` block with `type: l2-vm-to-vm`, `mode: bridge`, and two
endpoints. Once you have copied one to a real lab file (see
`config/README-lab-vm2vm.md`) and completed the earlier stages
(`prepare-vfs`, `prepare-switchdev`, `discover-representors`,
`prepare-ovs`, `create-vms`, `attach-vfs`):

```bash
# dry-run
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-nvidia-vm2vm.yaml --dry-run

# apply (only takes effect if safety.dry_run_default: false in your config)
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-nvidia-vm2vm.yaml --apply
```

This resolves `vf_pci`/`representor` from the most recent
`discover-representors` result if they are still `null` in your config,
builds a `ForwardingIntent`, and calls
`VendorBackend.configure_forwarding(intent)`. Inspect
`tests/results/<vendor>/<run-id>/configure-forwarding.json` for the full
`ForwardingResult` (bridge, ports, flows, offload flags, warnings,
errors).

## VM1 to VM2 to VM3 L2 segment forwarding

Not yet supported end to end (see
[forwarding-intent-model.md §5](../architecture/forwarding-intent-model.md#5-how-vm1-to-vm2-to-vm3-is-represented-later)).
`ForwardingIntent.type: l2-segment` is modeled in
`plugins/base/models.py` and the schema
(`config/lab-vm2vm-single-dpu.schema.yaml`) documents it, but this lab's
config schema (`vms:` list, `dpu.num_vfs: 2`) is hard-limited to exactly
2 VMs — a 3-VM use case needs its own config schema variant and
per-backend validation before `configure-forwarding` can accept an
`l2-segment` intent.

## Validate offload and collect evidence

```bash
python tools/labctl.py vm2vm validate-offload \
  --config config/lab-nvidia-vm2vm.yaml --json

python tools/labctl.py vm2vm collect-evidence \
  --config config/lab-nvidia-vm2vm.yaml --json
```

`validate-offload` reads the prior `configure-forwarding.json` from the
same run so its report can state which bridge/ports/flows it is
checking, rather than re-guessing them
(see [`forwarding-validation.md`](../test-plan/forwarding-validation.md)).

## Cleanup

```bash
python tools/lab_cleanup.py --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/lab_cleanup.py --config config/lab-nvidia-vm2vm.yaml --apply
```

See [`cleanup-and-rollback.md`](cleanup-and-rollback.md).

## Inspecting just the forwarding intent (no backend call)

To see the exact `ForwardingIntent` that `labctl` would build, without
calling any backend or touching `tests/results/`:

```bash
python tools/forwarding_intent.py --config config/lab-nvidia-vm2vm.example.yaml --pretty
```
