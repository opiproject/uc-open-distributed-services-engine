# `labctl.py` usage

New operators: [../getting-started.md](../getting-started.md). This page
is the CLI reference.

Status: Draft
Scope: CLI reference for `tools/labctl.py`, the single user-facing
orchestrator for the [VM-to-VM single-DPU lab](lab-runbook-vm2vm-single-dpu.md).
Companions: [`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md),
[`configure-forwarding.md`](configure-forwarding.md),
[`../../config/README-lab-vm2vm.md`](../../config/README-lab-vm2vm.md)

## Why labctl

`labctl` is the one command a lab operator runs. It never invents PCI
addresses, representor names, or IPs that were not either written into
the `--config` file or discovered by a prior stage in the same run; it
shells out to the existing read-only tools
(`tools/inventory/dpu_inventory.py`,
`tools/topology-discovery/vf_to_representor_map.py`,
`tools/offload-checker/ovs_offload_checker.py`) and to each vendor's
`plugins.<vendor>.backend.VendorBackend` implementation instead of
re-implementing discovery or offload logic itself.

## Synopsis

```bash
python3 tools/labctl.py vm2vm <stage> --config <file> \
    [--dry-run | --apply] [--force] [--json] [--verbose] [--run-id ID]
python3 tools/labctl.py intel-nf <stage> --config <file>
python3 tools/labctl.py amd-discovery <stage> --config <file>
```

`<stage>` for `vm2vm` is one of the 15 stages in
[lab-runbook-vm2vm-single-dpu.md](lab-runbook-vm2vm-single-dpu.md), plus
`run-all-safe`. `intel-nf` is the Intel network-function-offload first-run
path (not VM-to-VM OVS). `amd-discovery` is AMD Pensando discovery-first
(no assumed representors/switchdev/OVS).

## Flags

| Flag | Meaning |
|---|---|
| `--config FILE` | Required. A `odse-lab-vm2vm/v1alpha1` YAML file (see `config/lab-*-vm2vm.example.yaml`). |
| `--dry-run` | Force read-only/plan-only behavior for this invocation. Always wins over `--apply`. |
| `--apply` | Allow a destructive stage to execute. Still requires the config's `safety.dry_run_default: false` **and** the apply readiness gate (config validated / hash unchanged, management interface protected, vendor profile valid, NVIDIA DPU placement + DPU SSH when applicable, prior stage outputs, a prior dry-run plan for this stage). Failure is `APPLY_BLOCKED_BY_READINESS_GATE`. |
| `--force` | Skip only the dry-run-plan check, and only for `cleanup`. Refused for forwarding / OVS setup. |
| `--json` | Print the full stage envelope as JSON (recommended for scripting/report_generator.py). Default is a short text summary. |
| `--run-id ID` | Pin the run directory to `tests/results/<vendor>/<run-id>/`. Default: reuse that vendor's most recent run (`.latest`), or start a new UTC-timestamp run if none exists yet. |
| `-v`, `--verbose` | DEBUG-level logging on stderr (all executed/planned commands, redacted). |

Every `--config` is validated (`validate_vm2vm_config` +
`validate_forwarding_section`) before any other stage runs; `labctl`
refuses to proceed on an invalid config except for the
`validate-config` stage itself, which always runs so you can see the
issues.

## Dry-run-first safety model

Three independent gates must all agree before a destructive stage
(`prepare-host`, `prepare-vfs`, `prepare-switchdev`, `prepare-ovs`,
`create-vms`, `attach-vfs`, `configure-forwarding`, `cleanup`) actually
executes anything:

1. CLI: you passed `--apply` (not `--dry-run`, which always wins).
2. Config: `safety.dry_run_default: false` in the loaded `--config` file.
3. Config: `safety.require_apply: true` and
   `safety.forbid_management_interface_changes: true` — `labctl` refuses
   to load a config that tries to relax either of these
   (`validate_vm2vm_config` rejects it).

If any gate says "stay safe," the stage still runs its full planning
logic and prints exactly what it *would* run, but does not execute it.
`labctl` also never touches `host.management_interface` or any netdev
listed in `host.preserve_interfaces` (see
`plugins.base.vm2vm_config.preserved_interfaces`), independent of
`--apply`.

### A fourth, non-labctl gate: OS file permissions

None of the three gates above are OS/Linux permissions — `labctl`
itself never elevates privileges or shells out to `sudo`. Host-side
sysfs writes (`plugins/base/commands.py`'s `write_file()`, used by
`prepare-vfs` to set `sriov_numvfs`, and by `prepare-switchdev` for
eswitch mode) call Python's `Path.write_text()` directly as whatever
user ran `labctl`. `/sys/class/net/<pf>/device/sriov_numvfs` and
similar attributes are normally root-owned, mode `0644` — writable only
by root. `--dry-run` never hits this because it never opens the file
for writing; the very next thing you do differently after
`dry_run_default: false` + `--apply` succeeds the readiness gate is the
first real `write_text()` call, which is exactly when a non-root
invocation fails with `PermissionError: [Errno 13] Permission denied`.

Run the `--apply` invocation of any stage that writes host sysfs
(`prepare-vfs`, `prepare-switchdev`) as root:

```bash
sudo python3 tools/labctl.py vm2vm prepare-vfs \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

If `labctl`/its dependencies live in a virtualenv, preserve `PATH` (and
therefore the venv's `python3`) across `sudo` instead of resolving the
system Python:

```bash
sudo -E env "PATH=$PATH" python3 tools/labctl.py vm2vm prepare-vfs \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

`create-vms`/`attach-vfs` shell out to `virsh` instead of raw sysfs, so
their permission model is libvirt's (root, or a user in the `libvirt`
group with the right polkit rule) rather than direct file ownership —
if those fail with an authentication/permission error instead of a
Python traceback, that's a libvirt/polkit issue, not this one.

## Run directories and stage chaining

Every stage writes its output to
`tests/results/<vendor>/<run-id>/` using the
[`lab-state-contract.md`](lab-state-contract.md) filenames (for example
`representor-map.json` for `discover-representors`). Underscore aliases
are still written. Later stages
read earlier stages' JSON from the *same run* automatically:

- `configure-forwarding` fills in `vf_pci`/`representor` for each
  forwarding endpoint from `discover-representors.json` if the config
  itself didn't already have them. NVIDIA DPU mode maps representors on
  the DPU (`--location dpu`); host sysfs mapping is not used unless the
  advanced host-switchdev profile is selected. `require_high_confidence`
  refuses low-confidence names.
- `validate-offload` reads `configure-forwarding.json` to know which
  bridge/ports to pass to `ovs_offload_checker.py --forwarding-result`.
- `collect-evidence` reads `validate-offload.json` for the offload
  report instead of re-running the checker.

This means you can run stages one at a time, in order, without ever
passing `--run-id` yourself:

```bash
python3 tools/labctl.py vm2vm discover --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm discover-representors --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm configure-forwarding --config config/lab-nvidia-vm2vm.yaml --apply
```

Pass `--run-id` explicitly when you want to resume/inspect a specific
past run instead of the latest one, or to keep two runs (e.g. two
vendors, or a before/after comparison) side by side.

## `run-all-safe`

```bash
python3 tools/labctl.py vm2vm run-all-safe --config config/lab-nvidia-vm2vm.yaml --json
```

Runs all 15 stages from [the runbook](lab-runbook-vm2vm-single-dpu.md)
back-to-back in a single new (or reused) run directory. `run-all-safe`
**always** forces dry-run for every stage, regardless of `--apply` or
the config's `safety.dry_run_default` — it exists to let you preview
the entire pipeline's plan end-to-end (including on a non-lab
development machine with none of the vendor tooling installed) before
touching real hardware one `--apply`'d stage at a time. It never calls
`cleanup`; run that stage explicitly.

## Stage-by-stage examples

```bash
# 1. Inventory the box (safe on any machine, including a laptop)
python3 tools/labctl.py vm2vm discover --config config/lab-nvidia-vm2vm.yaml --json

# 2. Validate the config on its own
python3 tools/labctl.py vm2vm validate-config --config config/lab-nvidia-vm2vm.yaml --json

# 3-10. Provisioning stages: preview, then apply one at a time
python3 tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --dry-run
python3 tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --apply
python3 tools/labctl.py vm2vm prepare-switchdev --config config/lab-nvidia-vm2vm.yaml --apply
python3 tools/labctl.py vm2vm discover-representors --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm prepare-ovs --config config/lab-nvidia-vm2vm.yaml --apply
python3 tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --apply
python3 tools/labctl.py vm2vm verify-vms --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm attach-vfs --config config/lab-nvidia-vm2vm.yaml --apply

# 11. The single forwarding control point (see configure-forwarding.md)
python3 tools/labctl.py vm2vm configure-forwarding --config config/lab-nvidia-vm2vm.yaml --apply

# 12-13. Guest-side steps
python3 tools/labctl.py vm2vm configure-vm-ips --config config/lab-nvidia-vm2vm.yaml --apply
python3 tools/labctl.py vm2vm run-traffic --config config/lab-nvidia-vm2vm.yaml --apply

# 14-15. Evidence
python3 tools/labctl.py vm2vm validate-offload --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm collect-evidence --config config/lab-nvidia-vm2vm.yaml --json

# 16. Cleanup (see docs/operations/cleanup-and-rollback.md), then a report
python3 tools/labctl.py vm2vm cleanup --config config/lab-nvidia-vm2vm.yaml --apply
python3 tools/report_generator.py --input-dir tests/results/nvidia-bluefield/<run-id>/
```

## Result reports

Once some or all of the stage JSON above exists in a run directory,
`tools/report_generator.py` turns it into one standardized report:

```bash
python3 tools/report_generator.py \
    --input-dir tests/results/nvidia-bluefield/<run-id>/ \
    --output result-summary.md
```

This writes `result-summary.md` and `result-summary.json` (into the run
directory by default) covering test metadata, server/vendor/firmware
details, the PF/VF/representor mapping, OVS bridge configuration, VM
definitions, the **Forwarding Configuration** section (user intent,
backend selected, control/forwarding entity, configured ports/flows,
offload requested/configured/confirmed-or-probable), traffic results,
OVS/TC/ethtool evidence, CPU comparison, the offload confidence
assessment, failures, cleanup result, a computed final verdict
(`PASS_FUNCTIONAL_ONLY` / `PASS_OFFLOAD_PROBABLE` /
`PASS_OFFLOAD_CONFIRMED` / `FAIL_CONNECTIVITY` / `FAIL_SETUP` /
`INCONCLUSIVE`), and next actions. See
[`../test-plan/result-report-template.md`](../test-plan/result-report-template.md)
for what every field means and
[`../../tests/results/README.md`](../../tests/results/README.md) for
the run-directory layout it reads.

## Reading stage output

With `--json`, every stage prints an envelope:

```json
{
  "cli": "odse-labctl/v1alpha1",
  "vendor": "nvidia-bluefield",
  "stage": "configure-forwarding",
  "run_id": "20260101T000000Z",
  "dry_run": true,
  "apply": false,
  "result": { "...": "stage-specific payload, also saved to the run directory" }
}
```

Without `--json`, `labctl` prints a short human summary (`stage`,
`dry_run`/`apply`, `run_id`, and `valid`/`success`/`message`/`error`
keys from the stage result when present).

## Errors and exit codes

`labctl` exits `1` if `--config` fails to load or fails validation
(except for the `validate-config` stage, which reports issues in its
JSON but still exits `0` so you can pipe/inspect the report), or if
`vendor.type` doesn't map to a known backend. Individual stage
functions never crash the process — an unexpected exception inside a
stage is captured into that stage's JSON as an `error` field so the
rest of a `run-all-safe` sequence can still complete and be inspected.

## How labctl reaches a guest over SSH

`verify-vms`, `configure-vm-ips`, and `run-traffic` all need an SSH
path into each guest that is independent of the VF address being
tested. `labctl` resolves the **host** per VM, in order, and never
guesses:

1. `vms[].management_ip` in the config, if set.
2. Otherwise, `virsh domifaddr <vm-name>` (the DHCP lease on the
   libvirt `default` NAT network from
   `qemu/libvirt-xml/vm-vfio-template.xml`).
3. Otherwise, the stage reports an error naming the VM instead of
   falling back to `vms[].ip_cidr` (which is the address *being
   assigned to the VF under test*, not a way to reach the guest).

Separately, the **key** used for every one of those ssh calls is
`vms[].ssh_key_path` if set, else ssh's own default identity resolution
(ssh-agent / `~/.ssh/config` / `~/.ssh/id_*`) — `labctl` never passes
`-i` unless `ssh_key_path` is set. If `ssh -i <path> user@host` works
by hand but `labctl` reports the guest unreachable, this is almost
always the reason: set `vms[].ssh_key_path` explicitly (see
[`vm-image-preparation.md`](vm-image-preparation.md#verify-vmsconfigure-vm-ipsrun-traffic-fail-with-permission-denied-publickey)).

See [`vm-image-preparation.md`](vm-image-preparation.md) for why every
lab VM has two NICs.

## What labctl does not do

- It does not run `ansible-playbook`; `prepare-host` only reports
  tool/package presence and points at `ansible/roles/host_base`.
- It does not build VM disk images or bootstrap first-boot guest
  networking; see [`vm-image-preparation.md`](vm-image-preparation.md).
- It does not perform teardown itself; `cleanup` delegates to
  `tools/lab_cleanup.py` (see
  [`cleanup-and-rollback.md`](cleanup-and-rollback.md)).
- It does not support more than the fixed 2-VM/2-VF VM-to-VM shape;
  `l2-segment` (3+ VMs) is validated but not yet executed end-to-end
  (see [`configure-forwarding.md`](configure-forwarding.md)).
