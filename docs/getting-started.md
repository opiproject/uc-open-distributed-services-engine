# Getting started with ODSE

This is the short operator **start-here**. For the full **What, Why, and
How** (first activities through advanced vendor paths), open
[`user-guide.html`](user-guide.html) in a browser. For a map of *all*
docs, including contributor/roadmap docs, see [`README.md`](README.md).

The control point is `tools/labctl.py`. You do not drive the lab by
calling `ovs-vsctl`, `devlink`, DAO, OPI, P4Runtime, or vendor CLIs by
hand.

**Conditional documentation flow** (use case → vendor → “is the DPU/ACC
up?”): [`operations/user-doc-flow.md`](operations/user-doc-flow.md).

**OPI:** this lab is OPI-shaped, not OPI-speaking. `labctl vm2vm` does
not call an OPI gRPC service on any vendor. Details on that same page.

## Pick a use case, then a vendor

Answer two questions, in order: **(1) what do you want to do**, and
**(2) what hardware do you have.** Then follow exactly one link. Do not
start from a vendor's readiness/implementation doc under
`vendor-readiness/` — those are contributor roadmap docs, not runbooks
(see [`README.md`](README.md)).

### Use case 1: VM-to-VM lab (`labctl vm2vm`)

Two SR-IOV VFs, two KVM/QEMU VMs, traffic forwarded through the DPU/IPU.

| Vendor | Maturity | Start here |
|---|---|---|
| **NVIDIA BlueField** | Dry-run GO. Real switchdev/OVS-DOCA representor path. | [operations/first-run-nvidia-bluefield-dpu-mode.md](operations/first-run-nvidia-bluefield-dpu-mode.md) |
| **Intel IPU** | Discovery-first / mock-first. Forwarding proceeds once OPI or P4Runtime/IPDK is proven on your host; otherwise it correctly returns discovery-required. | [operations/first-run-intel-vm2vm.md](operations/first-run-intel-vm2vm.md) |
| **AMD Pensando** | Discovery-only. No control path proven yet. | [operations/amd-pensando-readiness.md](operations/amd-pensando-readiness.md) |
| **Marvell OCTEON** | Gated until DAO/DPDK is present. | [operations/marvell-dao-readiness.md](operations/marvell-dao-readiness.md) |

Run **one DPU/IPU at a time**. Do not copy NVIDIA OVS-DOCA or host
switchdev onto Intel, AMD, or Marvell.

### Use case 2: Network-function offload (`labctl intel-nf`)

A network function (proxy/firewall/packet-filter) placed on the IPU
itself. This is **Intel-only** today and is a **different, more mature**
Intel path than VM-to-VM above — do not confuse the two.

| Vendor | Start here |
|---|---|
| **Intel IPU** | [operations/first-run-intel-network-function-offload.md](operations/first-run-intel-network-function-offload.md) |

### Use case 3: Just discovery / a capability report (no lab, no hardware changes)

Read-only inventory across any vendor, with or without a config file:

```bash
python tools/vendorctl.py discover --vendor nvidia-bluefield --json
python tools/vendorctl.py discover --vendor intel-ipu --json
python tools/vendorctl.py discover --vendor amd-pensando --json
python tools/vendorctl.py discover --vendor marvell-octeon --json
python tools/capability_report.py --format markdown
```

If `discover` reports "no hints" for hardware you know is physically
present, see
[Troubleshooting: discover finds nothing](#troubleshooting-discover-finds-nothing)
below before assuming the device is unsupported.

## What is safe today

| Activity | Status |
|---|---|
| Clone, unit tests, preflight, `validate-config` | Safe |
| Dry-run discovery and `run-all-safe` | Safe |
| Hardware `--apply` (create VFs, mutate DPU OVS, define VMs) | **Not** until a filled lab YAML replaces placeholders and preflight is no longer `READY_FOR_DRY_RUN_ONLY` |

Default is dry-run. `--apply` also needs `safety.dry_run_default: false`
and the apply readiness gate. The example configs keep dry-run on
purpose.

Unknown facts stay unknown. Empty probes are not `offloaded:yes`. This
applies to every vendor equally — a vendor reporting `discovery-required`
is the tool being honest, not the tool being broken.

## Prerequisites

- Python 3.7+ (stdlib is enough; PyYAML is optional)
- This repository checked out
- For dry-run: no DPU, no `ovs-vsctl`, no libvirt required
- For later `--apply`: one Linux host, one DPU/IPU, SSH to the DPU when
  using NVIDIA DPU mode, KVM/QEMU/libvirt, two guest images

From the repo root:

```text
python tools/vendorctl.py list-vendors
```

## First hour: dry-run on the NVIDIA example

NVIDIA BlueField **DPU mode** is the most mature VM-to-VM dry-run target
and the best first exposure to `labctl` if you do not yet have hardware.
OVS/OVS-DOCA and representors live on the DPU. Host OVS is not the
forwarding owner.

You can run these on a laptop. Commands are planned, not executed.

```text
python tools/preflight_readiness.py --config config/lab-nvidia-vm2vm.example.yaml --markdown

python tools/labctl.py vm2vm validate-config --config config/lab-nvidia-vm2vm.example.yaml --json

python tools/labctl.py vm2vm run-all-safe --config config/lab-nvidia-vm2vm.example.yaml --dry-run --json

python tools/vendor_readiness_matrix.py --configs config/*.yaml --output tests/results/vendor-readiness-matrix.md
```

What "good" looks like:

- Preflight: `READY_FOR_DRY_RUN_ONLY` (placeholders and dry-run default)
- `validate-config`: `valid: true`; DPU OVS checks are **SSH plans**, not
  host `ovs-vsctl add-br`
- `run-all-safe`: every destructive stage stays `dry_run: true` /
  `executed: false`
- Matrix: NVIDIA `GO_FOR_HARDWARE_DRY_RUN`; other vendors are discovery
  or blocked (see below)

Stage JSON lands in `tests/results/nvidia-bluefield/<run-id>/`.

Optional read-only inventory (no labctl):

```text
python tools/vendorctl.py discover --vendor nvidia-bluefield --json
python tools/capability_report.py --format markdown
```

Then go to
[operations/first-run-nvidia-bluefield-dpu-mode.md](operations/first-run-nvidia-bluefield-dpu-mode.md)
for the hardware-adjacent runbook.

## First hour: dry-run on the Intel IPU example

Intel IPU's story is **discovery-first**, not representor-first — do not
expect the same shape as NVIDIA above. Start with raw discovery, then
readiness, before touching a lab YAML:

```text
python tools/vendorctl.py discover --vendor intel-ipu --json
python tools/preflight_readiness.py --config config/lab-intel-vm2vm.example.yaml --json
python tools/labctl.py vm2vm validate-config --config config/lab-intel-vm2vm.example.yaml --json
```

What "good" looks like on the unfilled example: readiness reports
`INTEL_VM_TO_VM_DISCOVERY_REQUIRED` or `INTEL_NOT_READY`, not an error —
that is expected until you fill in real values and prove an OPI or
P4Runtime/IPDK control path. Then go to
[operations/first-run-intel-vm2vm.md](operations/first-run-intel-vm2vm.md)
for the full runbook, or to
[operations/first-run-intel-network-function-offload.md](operations/first-run-intel-network-function-offload.md)
if network-function offload (not VM-to-VM) is actually what you want.

### Troubleshooting: `discover` finds nothing

`vendorctl.py discover --vendor intel-ipu` classifies devices by matching
`lspci -nn` text and known PCI device IDs (see
`plugins/intel_ipu/discovery.py`). If you have a real IPU installed and
`discover` still reports no hints:

1. Run `lspci -nn | grep -i intel` yourself on the host with the IPU.
2. Look for a line ending in `[8086:xxxx]` on an Ethernet/processing
   accelerator controller — the human-readable name (e.g.
   "Infrastructure Processing Unit E2100" vs. "Infrastructure Data Path
   Function") can differ across hosts because it comes from the local
   `pci.ids` database, not from the hardware itself. The PCI device ID
   after the colon is the stable identifier.
3. If that ID is not already in `IPU_PCI_DEVICE_IDS` in
   `plugins/intel_ipu/discovery.py`, that is a one-line fix (add the ID,
   add a test fixture with the real `lspci -nn` line, add a regression
   test) — file it or send a small PR, it is not a new feature.
4. This exact gap (PCI ID `8086:1452` rendering as "Infrastructure Data
   Path Function" instead of "Infrastructure Processing Unit E2100" on a
   real host) was found and fixed this way — see
   `tests/fixtures/intel_ipu/lspci_idpf_host.txt`.

## Before any hardware `--apply`

1. Copy the example for your vendor (the copy is gitignored):

   ```text
   cp config/lab-nvidia-vm2vm.example.yaml config/lab-nvidia-vm2vm.yaml
   # or: cp config/lab-intel-vm2vm.example.yaml config/lab-intel-vm2vm.yaml
   ```

2. Fill placeholders from **discovery**, not from the example:
   `host.name`, `host.management_interface`, and the vendor-specific
   fields (`dpu.management_ip`, `dpu.ssh_user`, `dpu.ssh_key_path`,
   `dpu.pf_name`, `dpu.pf_pci` for NVIDIA; VF/PCI/OPI or P4 endpoint
   fields for Intel — see the relevant first-run doc above), plus VM
   image paths. See [config/README-lab-vm2vm.md](../config/README-lab-vm2vm.md).

3. Re-run preflight on the filled file. Stay on dry-run until you have
   a written change window. Then follow the vendor's first-run runbook
   **one stage at a time** (`--dry-run` before `--apply` for each
   destructive stage).

4. Never change `host.management_interface`. Never use `--force` except
   on `cleanup`.

## Do not do these

- `--apply` against `*.example.yaml`
- Treat planned SSH/`ovs-vsctl`/`p4rt-ctl` argv as live proof
- Use host OVS as the default NVIDIA path (that is the advanced
  `nvidia-connectx-host-switchdev` profile only)
- Invent representor names (`eth0v1` is not a BlueField VF representor)
- Skip `discover-representors` before `configure-forwarding`
- Assume an Intel/AMD/Marvell result of `discovery-required` means the
  tool is broken — see [What is safe today](#what-is-safe-today)

## Tests (no hardware)

```text
python -m unittest discover -s tests/unit
python -m unittest discover -s tests/vendor
python -m unittest discover -s tests/vendor-certification
```

## Related docs

- [`README.md`](README.md) — full documentation map (users vs. contributors)
- [`operations/README.md`](operations/README.md) — full operations index
- [`operations/preflight-readiness.md`](operations/preflight-readiness.md)
- [`operations/configure-forwarding.md`](operations/configure-forwarding.md)
- [`operations/cleanup-and-rollback.md`](operations/cleanup-and-rollback.md)
- [`architecture/nvidia-bluefield-control-placement.md`](architecture/nvidia-bluefield-control-placement.md)
- [`usecases/usecases-to-try-as-of-15-august-2026.md`](usecases/usecases-to-try-as-of-15-august-2026.md) —
  catalog of tryable use cases (not the onboarding path)
