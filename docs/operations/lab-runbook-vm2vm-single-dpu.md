# Runbook: VM-to-VM single-DPU lab

New operators: [../getting-started.md](../getting-started.md). This page
is the 15-stage sequence after you know `labctl` is the control point.

Status: Draft  
Scope: End-to-end stage sequence for the first real hardware use case
(one server, one DPU/IPU, two SR-IOV VFs, two KVM/QEMU VMs, DPU-side
representors on OVS, VM-to-VM traffic, offload evidence).  
Companions: [`labctl-usage.md`](labctl-usage.md),
[`configure-forwarding.md`](configure-forwarding.md),
[`first-run-nvidia-bluefield-dpu-mode.md`](first-run-nvidia-bluefield-dpu-mode.md),
[`../../config/README-lab-vm2vm.md`](../../config/README-lab-vm2vm.md),
[`cleanup-and-rollback.md`](cleanup-and-rollback.md)

This is the canonical stage order for `tools/labctl.py vm2vm <stage>`,
including the forwarding-intent stage introduced alongside the
forwarding control point (see
[`../architecture/forwarding-intent-model.md`](../architecture/forwarding-intent-model.md)).
Every stage is independently runnable; run `run-all-safe` to execute
them all in order with dry-run defaults.

| # | Stage | Purpose | Typically destructive? |
|---|---|---|---|
| 1 | `discover` | Inventory the DPU/host via `tools/inventory/dpu_inventory.py` + vendor backend `discover()` | No |
| 2 | `validate-config` | Validate the VM2VM lab config against the schema | No |
| 3 | `prepare-host` | Baseline host packages/services (delegates to `ansible/roles/host_base` intent) | Yes (host packages/services) |
| 4 | `prepare-vfs` | Set `sriov_numvfs=2` on the selected PF via `backend.configure_sriov()` | Yes |
| 5 | `prepare-switchdev` | Request switchdev eswitch mode via `backend.configure_switchdev()` (skipped for vendors that don't use kernel switchdev) | Yes |
| 6 | `discover-representors` | Map the 2 VFs to representor netdevs via `backend.discover_representors()` + `tools/topology-discovery/vf_to_representor_map.py` | No |
| 7 | `prepare-ovs` | Create/verify the OVS bridge via `backend.configure_ovs_bridge()` | Yes |
| 8 | `create-vms` | Define the 2 libvirt domains from `qemu/libvirt-xml/vm-vfio-template.xml` | Yes |
| 9 | `verify-vms` | Confirm each VM is actually running (fresh `virsh domstate`) and SSH-reachable on its management NIC before attaching a VF | No (read-only; see "Why verify-vms is its own stage" below) |
| 10 | `attach-vfs` | Attach VF0/VF1 to VM1/VM2 via VFIO passthrough | Yes |
| 11 | `configure-forwarding` | Build a `ForwardingIntent` from `forwarding:` and call `backend.configure_forwarding()` — see [configure-forwarding.md](configure-forwarding.md) | Yes (vendor-dependent; most backends still plan-only) |
| 12 | `configure-vm-ips` | SSH into each guest and assign `vms[].ip_cidr` via `qemu/images/guest-network-setup.sh.template` | Not apply-readiness-gated (guest-only, reversible), but still needs `--apply` to actually SSH/mutate rather than plan-only |
| 13 | `run-traffic` | ping / iperf3 between the two guest IPs | Not apply-readiness-gated (guest-only, reversible), but still needs `--apply` to actually send traffic rather than plan-only |
| 14 | `validate-offload` | `tools/offload-checker/ovs_offload_checker.py --forwarding-result ...` (NVIDIA DPU mode: `--location dpu`) | No |
| 15 | `collect-evidence` | Save OVS/TC/ethtool/CPU/vendor telemetry snapshots | No |
| 16 | `cleanup` | Thin delegate to `tools/lab_cleanup.py` (10 teardown stages) | Yes |

`cleanup` is intentionally excluded from `run-all-safe` (see
`labctl-usage.md`) so a safe end-to-end dry-run never tears down a lab
it just finished setting up. Run it explicitly when you are done; full
stage-by-stage behavior, safety rules, and manual recovery are
documented separately in
[cleanup-and-rollback.md](cleanup-and-rollback.md).

## Why verify-vms is its own stage

`virsh start`'s result, captured by `create-vms`, is not sufficient
evidence that a VM is actually up: it can be stale (a domain left over
from a previous run under the same name), or it can be misreported if
`virsh list` races with the actual boot. `verify-vms` re-queries
`virsh domstate` fresh and independently probes SSH on the guest's
management NIC with the key already provisioned into the image (see
[`vm-image-preparation.md`](vm-image-preparation.md)) — real,
current-moment evidence instead of trusting a previously-captured
command result. It is read-only (no `--dry-run`/`--apply` distinction,
like `discover`/`validate-offload`) and deliberately a single check,
not a poll loop: re-run it if a VM is still booting. `attach-vfs
--apply`'s readiness gate requires `verify-vms.json` to exist for the
same run (not that it fully passed) before it will attach a VF.

## Why forwarding is its own stage

Stages 1–10 make the ingredients exist (VFs, representors, an OVS
bridge, VMs with VF passthrough, confirmed running and reachable).
Stage 11 is the only stage that actually connects VM1 to VM2 —
deliberately isolated so:

- It has one clear success/failure signal per vendor
  (`ForwardingResult.success`, `errors`), instead of being buried inside
  `prepare-ovs` or `create-vms`.
- `validate-offload` (14) and `collect-evidence` (15) can read
  `configure-forwarding.json` from the same run to know exactly which
  bridge/ports/flows to inspect, instead of re-discovering them.
- Re-running just this stage (e.g. after changing `explicit_flows` or
  `offload.requested` in the config) does not require repeating VF/VM
  setup.

## Minimal example (NVIDIA)

```bash
python tools/labctl.py vm2vm validate-config --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm discover --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm discover-representors --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm prepare-ovs --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm verify-vms --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm attach-vfs --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm configure-forwarding --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm configure-vm-ips --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm run-traffic --config config/lab-nvidia-vm2vm.yaml --apply
python tools/labctl.py vm2vm validate-offload --config config/lab-nvidia-vm2vm.yaml --json
python tools/labctl.py vm2vm collect-evidence --config config/lab-nvidia-vm2vm.yaml --json
python tools/report_generator.py --input-dir tests/results/nvidia-bluefield/<run-id>/ --output result-summary.md
```

See [`labctl-usage.md`](labctl-usage.md) for the full `run-all-safe`
shortcut and stage-chaining (`--run-id`) details.
