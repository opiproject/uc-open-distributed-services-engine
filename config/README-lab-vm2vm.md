# VM-to-VM single-DPU lab configuration

This describes the config files for the first real hardware use case:

```text
One server
One selected DPU/IPU at a time
Two SR-IOV VFs
Two KVM/QEMU VMs (VF0 -> VM1, VF1 -> VM2)
DPU-side representors attached to OVS
Traffic between VM1 and VM2
Offload evidence collected from OVS, TC, ethtool, and vendor telemetry
```

Files:

| File | Purpose |
|---|---|
| `lab-vm2vm-single-dpu.schema.yaml` | Reference schema — field names, types, required/optional. Not meant to be copied directly. |
| `lab-nvidia-vm2vm.example.yaml` | Copy-this template for an NVIDIA BlueField lab. |
| `lab-marvell-vm2vm.example.yaml` | Copy-this template for a Marvell OCTEON lab. |
| `lab-intel-vm2vm.example.yaml` | Copy-this template for an Intel IPU lab. |
| `lab-intel-network-function-offload.example.yaml` | Intel first-run `labctl intel-nf` (not VM-to-VM OVS). |
| `lab-amd-vm2vm.example.yaml` | Copy-this template for an AMD Pensando lab. |

This is a **separate, narrower** config from `config/lab.example.yaml`
(schema `odse-lab-config/v1alpha1`, used by `tools/vendorctl.py` for
general multi-host/multi-vendor discovery). The VM2VM schema
(`odse-lab-vm2vm/v1alpha1`) describes exactly one server, one DPU, and
two VMs, and is consumed by `tools/labctl.py` (see
`docs/operations/labctl-usage.md`). The two schemas do not need to agree
on field names for the same underlying host — treat them independently.

## 1. Copy an example into a real lab file

```text
cp config/lab-nvidia-vm2vm.example.yaml config/lab-nvidia-vm2vm.yaml
```

`config/lab-*-vm2vm.yaml` (without `.example`) is gitignored — never
commit a real lab file. Only the `.example.yaml` files (placeholders
only) are tracked.

## 2. Identify PF, PCI address, VFs, and representors

Do this **before** editing the config, using read-only discovery tools
already in this repo — do not guess or copy values from the example:

```text
python3 tools/inventory/dpu_inventory.py --pretty
python3 tools/vendorctl.py discover --vendor <vendor> --json
python3 tools/topology-discovery/vf_to_representor_map.py --pf <pf-name> --pretty
```

Fill in:

- `dpu.pf_name` / `dpu.pf_pci` — from `dpu_inventory.py` netdevs / PCI
  device listing for the PF you intend to dedicate to this lab.
- `dpu.num_vfs` — must be `2` for this use case (labctl refuses other
  values at `validate-config`).
- `vms[].vf_index` — `0` and `1`; the actual `vf_pci` /
  `forwarding.endpoints[].representor` values can be left `null` and are
  populated automatically by `labctl vm2vm discover-representors` and
  `labctl vm2vm configure-forwarding` once the VFs exist — do not
  hand-guess representor netdev names.
- `host.management_interface` — the netdev you are SSHed in over, or
  otherwise cannot afford to lose. See the safety rule below.

## 3. Why the management interface must be separate

`host.management_interface` (and any `host.preserve_interfaces`) must be
a **different** netdev than `dpu.pf_name`/`dpu.uplink_name`. Every ODSE
tool that can mutate host networking (`sriov_setup`, `switchdev_setup`,
`ovs_setup`, `labctl`) treats the management interface as off-limits —
`labctl` reads `safety.forbid_management_interface_changes` and refuses
any stage that would touch it, regardless of `--apply`. If your only
network path to the host **is** the PF you want to experiment with, do
not run this lab without a second (out-of-band, IPMI/BMC, or serial)
access path first.

## 4. How each vendor config differs

| Field | NVIDIA | Marvell | Intel | AMD |
|---|---|---|---|---|
| `dpu.enable_switchdev` | `true` | `false` (DPDK representor model, not kernel switchdev) | `false` | `false` |
| `dpu.preferred_datapath` | `system` | `dao` | `discovery` | `discovery` |
| `forwarding.offload.requested` | `true` | `true` | `false` | `false` |
| Expected `configure-forwarding` outcome | Real dry-run OVS bridge/port plan | May return `error=DAO_NOT_AVAILABLE` / `REPRESENTOR_MAPPING_REQUIRED` | Likely `discovery-required` / `error=OPI_ENDPOINT_REQUIRED` | Likely `discovery-required` / `error=REPRESENTOR_OR_CONTROL_PATH_REQUIRED` |

Intel's `lab-intel-vm2vm.example.yaml` also ships a `p4:` block
(`mode`, `endpoint`, `device_id`, `p4info_path`) alongside `opi:` —
these are **alternate** control planes for the same capability check,
not layered requirements. Configuring `opi.endpoint` *or* `p4.endpoint`
is enough to avoid `OPI_ENDPOINT_REQUIRED`; see
`docs/vendor-readiness/intel-ipu-p4-ipdk.md` and
`docs/operations/intel-vm-to-vm-capability-detection.md`.

See `docs/vendor-readiness/vendor-capability-matrix.md` and each
vendor's `docs/vendor-readiness/<vendor>.md` for why these differ — this
is a direct reflection of what has and has not been discovered/confirmed
for each backend, not an arbitrary choice.

## 5. How dry-run and `--apply` should be used

Every example file ships with:

```yaml
safety:
  dry_run_default: true
  require_apply: true
  forbid_management_interface_changes: true
  cleanup_on_failure: false
```

`labctl` honors these the same way `vendorctl` honors `config/lab.yaml`'s
top-level `dry_run`:

- **`--dry-run` (CLI) always wins**, regardless of config or `--apply`.
- With no CLI flag, a stage runs in dry-run if `safety.dry_run_default`
  is `true` (default in every example) **or** if `--apply` was not
  passed.
- Only `--apply` **and** `safety.dry_run_default: false` in your real
  (copied) config together allow a destructive stage to execute.
- `safety.require_apply` and `safety.forbid_management_interface_changes`
  must both be `true` in every config `labctl` accepts — it refuses to
  load a config that sets either to `false` (see
  `plugins/base/vm2vm_config.py`). There is intentionally no config
  switch to relax these two; only the per-stage `--apply` CLI flag can
  advance past dry-run.

Typical flow:

```text
python3 tools/labctl.py vm2vm validate-config --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm discover --config config/lab-nvidia-vm2vm.yaml --json
python3 tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --dry-run
python3 tools/labctl.py vm2vm prepare-vfs --config config/lab-nvidia-vm2vm.yaml --apply
```

See `docs/operations/labctl-usage.md` for the full stage list.

## What not to commit

| File | Commit? |
|---|---|
| `config/lab-vm2vm-single-dpu.schema.yaml` | Yes |
| `config/lab-*-vm2vm.example.yaml` | Yes (placeholders only) |
| `config/lab-*-vm2vm.yaml` (no `.example`) | No |
| `tests/results/**` run output | No (see `tests/results/README.md`) |
| Any file with passwords, tokens, or Redfish/BMC credentials | No |
