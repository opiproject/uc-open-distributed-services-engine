# First-run runbook: Intel IPU VM-to-VM (discovery-first)

If you have not run ODSE yet, start at
[`../getting-started.md`](../getting-started.md). Conditional flowchart
(and the OPI answer): [`user-doc-flow.md`](user-doc-flow.md). This page is the Intel
IPU follow-on for the **VM-to-VM lab use case** — the same use case the
[NVIDIA BlueField runbook](first-run-nvidia-bluefield-dpu-mode.md) covers,
run against Intel IPU hardware instead.

Status: Draft, **discovery-first**. Unlike the NVIDIA runbook, this page
does **not** end in a proven hardware forwarding path yet. Read
[Where this honestly stands today](#2-where-this-honestly-stands-today)
before you start.

Companions: [`intel-ipu-readiness.md`](intel-ipu-readiness.md),
[`intel-vm-to-vm-capability-detection.md`](intel-vm-to-vm-capability-detection.md),
[`../vendor-readiness/intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md),
[`../vendor-readiness/intel-ipu-e2100.md`](../vendor-readiness/intel-ipu-e2100.md),
[`first-run-intel-network-function-offload.md`](first-run-intel-network-function-offload.md) —
a **different, more mature** Intel use case (network-function offload, not
VM-to-VM), [`labctl-usage.md`](labctl-usage.md),
[`preflight-readiness.md`](preflight-readiness.md).

---

## 0. Which Intel path do you want?

Intel IPU supports two different use cases in this repo today. Do not
assume they are the same thing:

| Use case | First-run doc | Maturity |
|---|---|---|
| Network-function offload (proxy/firewall/packet-filter placed on the IPU) | [`first-run-intel-network-function-offload.md`](first-run-intel-network-function-offload.md) | First-class Intel path. Real `labctl intel-nf` stages, OPI/DPU-Operator gating. |
| VM-to-VM (two VMs on the host, traffic forwarded via the IPU) — **this page** | This page | Discovery-first / mock-first. Real discovery and capability detection; forwarding is still a placeholder until OPI or P4Runtime is proven on your hardware. |

If you just want *something* to work end-to-end on Intel today, use
`labctl intel-nf`. If your goal is genuinely the VM-to-VM lab (same shape
as the NVIDIA runbook), keep reading.

## 1. Objective

Run the same VM-to-VM shape as NVIDIA, on Intel IPU instead:

two SR-IOV VFs → two KVM/QEMU VMs (VFIO passthrough) → an IPU-side control
plane (OPI/DPU-Operator **or** P4Runtime/IPDK, whichever your host proves)
→ VM-to-VM traffic → offload evidence (if any) → safe cleanup.

Do not treat a passing `configure-forwarding` placeholder, a demo ping, or
host `ovs-vsctl` as IPU offload proof. Intel's control plane here is
**discovery-required**: unknown facts stay `null`, and the lab refuses to
invent `offloaded:yes`.

## 2. Where this honestly stands today

- Discovery, readiness, and capability-detection are real and read-only —
  they tell you the truth about your host, including "not proven yet."
- VF creation, VM creation/attach, and traffic stages reuse the same
  `labctl vm2vm` machinery as NVIDIA and work the same way (dry-run by
  default, `--apply` gated by the same readiness checks).
- `configure-forwarding` is the one stage that is still a **staged
  skeleton** on Intel: it only proceeds past discovery once
  `check_vm_to_vm_forwarding_capability()` proves an OPI endpoint **or** a
  P4Runtime/IPDK pipeline (see
  [`intel-vm-to-vm-capability-detection.md`](intel-vm-to-vm-capability-detection.md)).
  Until then it returns
  `INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED`, not a fabricated
  success.
- There is no Intel equivalent yet of NVIDIA's `pf0vf0`-style
  high-confidence representor mapping or `ovs-vsctl`/`dpctl` offload
  evidence. `validate-offload` on Intel stays `unknown` until real
  hardware samples (`p4rt-ctl dump-entries`, OPI telemetry, or similar)
  are wired in for your SKU.

This is not a bug to route around — it is the accurate current state.
Treat every stage's JSON `status` field as the source of truth over this
document.

## 3. Assumptions

- You have SSH access to the host with the Intel IPU installed (not
  necessarily to the IPU's IMC/ACC management complex yet — that is only
  needed once you configure `p4.acc_ssh`/`dpu.ssh_*` for the P4Runtime CLI
  path).
- Host has KVM/QEMU/libvirt for the VM stages, same as NVIDIA.
- Config file is a filled copy of the Intel VM-to-VM example, not the
  example itself, before `--apply`.

## 4. Step 1 — raw discovery (no config needed)

Before touching any lab YAML, confirm the framework actually sees your
IPU:

```bash
python tools/vendorctl.py discover --vendor intel-ipu --json
```

Read `result.devices`, `result.physical_functions`, `details.devlink_devices`,
`details.ip_netdevs`, and `message` in the output. Expected good signs on
real E2100-class hardware:

- An `lspci -nn` line mentioning `Infrastructure Processing Unit`,
  `Infrastructure Data Path Function`, `IDPF`, `E2100`, or PCI device ID
  `8086:1452` (the text differs by host `pci.ids` version; the id does
  not — see the docstring in `plugins/intel_ipu/discovery.py`).
- Netdevs with a `parentdev` matching that PCI address (e.g.
  `enp10s0f0`, `enp10s0f0d1`, `enp10s0f0d2`, ... — the `d<N>` suffixes are
  the IPU's sub-function/representor-style netdevs).

**This is where `dpu.pf_pci` and `dpu.pf_name` in step 3 come from.**
`result.physical_functions[0].pci_address` and `.netdev` are populated
straight from this command — no separate lookup, no guessing:

```json
"physical_functions": [
  {
    "id": "pf-0000:0a:00.0",
    "pci_address": "0000:0a:00.0",
    "netdev": "enp10s0f0",
    "dpu_device_id": "intel-0000:0a:00.0"
  }
]
```

Copy `pci_address` into `dpu.pf_pci` and `netdev` into `dpu.pf_name`
verbatim. `result.devices[0].vendor_extensions.subfunction_netdevs`, if
present, lists sibling `d<N>` netdevs on the same PCI function
(`enp10s0f0d1`, ...) — informational only; they are not asserted to be
representors (Intel has no `pf0vf0`-style proof of that yet, unlike
NVIDIA). If `physical_functions` is empty but `devices` has one entry
with `pci_address: null`, discovery found the IPU but could not pair it
with a netdev — you can still copy the PCI address printed in
`vendor_extensions.lspci`, but `dpu.pf_name` will need to come from your
own `ip -d link show` read.

If `discover` reports `no IPU/E2100 hints in lspci`, run
`lspci -nn | grep -i intel` yourself and compare against the hints above
before assuming there is no IPU present — the vendor:device ID match is
more reliable than the free-text description, which is a `pci.ids`
snapshot, not a stable vendor string. If you find a real IPU device ID
that is not yet in `IPU_PCI_DEVICE_IDS`, that is a one-line fix in
`plugins/intel_ipu/discovery.py`, not a new feature.

## 5. Step 2 — readiness

```bash
python tools/preflight_readiness.py --config config/lab-intel-vm2vm.example.yaml --json
```

See [`intel-ipu-readiness.md`](intel-ipu-readiness.md) for what each
status (`INTEL_READY_FOR_OPI_DRY_RUN`, `INTEL_VM_TO_VM_DISCOVERY_REQUIRED`,
etc.) means. Expect `INTEL_VM_TO_VM_DISCOVERY_REQUIRED` or
`INTEL_NOT_READY` on the example file — that is correct until you fill in
real values.

## 6. Step 3 — copy and fill the config

```bash
cp config/lab-intel-vm2vm.example.yaml config/lab-intel-vm2vm.yaml
```

Fill from discovery, not from the example. `dpu:` holds the IPU's PF
identity, same field names NVIDIA's `dpu:` block uses:

| Field | Source |
|---|---|
| `dpu.pf_pci` | Step 1's `result.physical_functions[0].pci_address` |
| `dpu.pf_name` | Step 1's `result.physical_functions[0].netdev` |
| `host.name`, `host.management_interface` | Your own host, not discovery |
| VM image paths | [`vm-image-preparation.md`](vm-image-preparation.md) |
| **either** `opi.endpoint` **or** `p4.mode`/`p4.artifacts`/ACC SSH fields under `dpu:` | Whichever control plane you are proving — see [`../vendor-readiness/intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md) for the P4Runtime/IPDK fields |

If step 1 printed `physical_functions: []` (empty), there is nothing to
copy yet — do not invent a PCI address or netdev; go back and fix
discovery first (see [Failure triage](#9-failure-triage) below). Re-run
preflight on the filled file.

## 7. Step 4 — validate config and capability

```bash
python tools/labctl.py vm2vm validate-config --config config/lab-intel-vm2vm.yaml --json
python tools/labctl.py vm2vm discover --config config/lab-intel-vm2vm.yaml --json
```

## 8. Stages 3–16 — the same `labctl vm2vm` pipeline as NVIDIA

Everything from here on is `tools/labctl.py vm2vm <stage>` — the exact
same 16-stage pipeline in
[`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md#minimal-example-nvidia),
just with `--config config/lab-intel-vm2vm.yaml`. This doc's own
"Step 1–4" above (discovery, readiness, copy-config, validate-config)
cover the pre-lab work and correspond to that pipeline's stages 1
(`discover`) and 2 (`validate-config`); **from here on, "stage" means
that pipeline's `#` column**, not this doc's own step numbers, so there
is no renumbering to do — run stages 3 through 16 in order, same
command names as NVIDIA:

| Stage # | `labctl vm2vm` stage | Same as NVIDIA? |
|---|---|---|
| 3 | `prepare-host` | Yes, unchanged (vendor-agnostic tool/package check) |
| 4 | `prepare-vfs` | **Yes, unchanged — actually creates VFs.** `configure_sriov()` writes `sriov_numvfs` under `/sys/class/net/<dpu.pf_name>/device/` via `plugins/base/sriov.py`, the same generic kernel SR-IOV sysfs mechanism NVIDIA's backend uses (see [`intel-vm-to-vm-capability-detection.md`](intel-vm-to-vm-capability-detection.md)). Gated by `--dry-run`/`--apply` exactly like NVIDIA. (Older builds of this backend always returned `status: skipped, executed: false` here regardless of `--apply` — if you still see that, update; this was a stub bug, not intended behavior, fixed alongside the discovery fixes in [§4](#4-step-1--raw-discovery-no-config-needed).) |
| 5 | `prepare-switchdev` | **Effectively a no-op.** It only runs `configure_switchdev()` when `dpu.enable_switchdev: true`; the Intel example sets it `false` (Intel's control plane is OPI/DPU-Operator or P4Runtime, not kernel switchdev), so this stage returns `skipped: true` |
| 6 | `discover-representors` | **Runs a real host-sysfs scan for `vf_pci`, but reports no representor.** `stage_discover_representors` calls `tools/topology-discovery/vf_to_representor_map.py --location host` regardless of vendor — once stage 4 has created real VFs, this finds their real PCI addresses under `/sys/class/net/<pf>/device/virtfn*` (that's what stage 10 attaches), even though `representor_netdev` stays `null` for Intel (no `pf0vf0`-style proof yet, unlike NVIDIA — see [§2](#2-where-this-honestly-stands-today)). The Intel *backend's* own `discover_representors()` (a separate, informational-only call folded into this stage's `backend_result` field) always returns an empty list with `message: "representors unknown for Intel IPU until datapath discovery"` — that field is not what feeds `attach-vfs`, so it staying empty is not a blocker |
| 7 | `prepare-ovs` | **Refused/skipped unless `intel.control_plane: ovs`.** By default (or `control_plane: opi`/`p4`) `configure_ovs_bridge()` returns `status: skipped` — Intel's OPI/DPU-Operator and raw P4Runtime (`p4rt-ctl`) control planes do not use an ACC OVS bridge. If you set `intel.control_plane: ovs` (see [`intel-ipu-ovs-p4.md`](../vendor-readiness/intel-ipu-ovs-p4.md)), this plans (`dry_run=True` always, same as NVIDIA's `prepare-ovs`) an `ovs-vsctl add-br` over ACC SSH; real bridge/port creation happens under stage 11's `--apply`, not here |
| 8 | `create-vms` | Yes, unchanged |
| 9 | `verify-vms` | Yes, unchanged |
| 10 | `attach-vfs` | Yes, unchanged — attaches the real `vf_pci` addresses stage 6 found |
| 11 | `configure-forwarding` | **Different, and expected to report "unsupported" — keep going anyway.** See below |
| 12 | `configure-vm-ips` | Yes, unchanged |
| 13 | `run-traffic` | Yes, unchanged |
| 14 | `validate-offload` | Runs the same command; result stays `unknown` on Intel until real hardware samples are wired in (see [§2](#2-where-this-honestly-stands-today)) |
| 15 | `collect-evidence` | Yes, unchanged |
| 16 | `cleanup` | Yes, unchanged |

Do not hand-skip stages 5–7 — run the full sequence in order like
NVIDIA does; the table above just explains why their output will look
like a no-op instead of treating that as a bug. Stage 11
(`configure-forwarding`) is the one stage that behaves differently:

**Before filling `p4.forwarding_entries` or
`forwarding.endpoints[].representor` for real**, see
[`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) — it walks
through what to install/start on the ACC and exactly how to discover
the real table/action names and representor names this stage needs.
Nothing in stages 1–10 above tells you those; they are IPU-side
values this project deliberately never guesses.

```bash
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-intel-vm2vm.yaml --dry-run --json
```

- If capability is not proven yet: `success: false`, status
  `INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED`. **This is expected
  and not an error in the tooling** — it means your host has not yet
  proven an OPI or P4Runtime/IPDK control path for these VFs. It is *not*
  a stub you need to "fix" or route around by hand: `check_vm_to_vm_forwarding_capability()`
  is deliberately honest that no forwarding control plane is proven yet,
  the same way `validate-offload` deliberately stays `unknown` rather
  than inventing `offloaded: yes`.
- **Keep walking the pipeline anyway — run stages 12 through 16 exactly
  like you would if stage 11 had succeeded.** Nothing downstream of stage
  11 reads stage 11's output: `configure-vm-ips` only needs the VMs
  created/attached in stages 8–10; `run-traffic` sends real packets
  between the two VM VFs over whatever the IPU's default forwarding does
  (which may be nothing, if that is even what you are trying to observe);
  `validate-offload`/`collect-evidence`/`cleanup` all run unconditionally.
  This is exactly what `labctl vm2vm run-all-safe` already does — it
  loops over every stage in `RUN_ALL_SAFE_ORDER` and never stops early
  because one stage's `success`/`status` was negative.
- If capability *is* proven (you configured `opi.endpoint`, a working
  P4Runtime/IPDK pipeline, or `intel.control_plane: ovs` with a reachable
  ACC OVS): the backend builds an OPI-style `NetworkAttachment`
  placeholder, a P4Runtime table-entry-shaped placeholder, or an OVS
  bridge/port-shaped placeholder, depending on `details.control_plane`
  (`"opi"`, `"p4"`, or `"ovs"`). In dry-run this placeholder is all you
  get, for any of the three.
- **The P4 and OVS control planes can go past the placeholder under
  `--apply`; OPI cannot yet.**
  - If `control_plane == "p4"` and you run this stage with `--apply`
    (readiness gate cleared, `dry_run_default: false`), the backend
    actually calls `P4RuntimeClient.write_table_entry()` per endpoint —
    real `p4rt-ctl add-entry` over SSH in `p4.mode: cli`. It still
    refuses to guess a FLOW string: you must fill `p4.forwarding_entries`
    (one `{vf_index, table, flow}` per VM/VF, the real table/action names
    from your compiled P4 program — see
    [`intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md))
    first, or it refuses outright naming the missing endpoint(s) rather
    than submitting anything partial.
  - If `control_plane == "ovs"` and you run this stage with `--apply`,
    the backend calls `ovs_p4.create_bridge()`/`add_port()` per endpoint
    — real `ovs-vsctl add-br`/`add-port` over ACC SSH (see
    [`intel-ipu-ovs-p4.md`](../vendor-readiness/intel-ipu-ovs-p4.md)).
    Unlike `p4`, no table/action names are needed for plain bidirectional
    bridging — only `forwarding.endpoints[].representor` per VM/VF — but
    it still refuses outright, naming the missing endpoint(s), if any
    representor is unset.
  - The OPI control plane has no equivalent `--apply` path yet —
    `opi.deploy_network_function()` is still HTTP-placeholder-only, so
    `control_plane == "opi"` stays placeholder-only under `--apply` too,
    for now.

Stage JSON lands in `tests/results/intel-ipu/<run-id>/`, same layout as
NVIDIA's `tests/results/nvidia-bluefield/<run-id>/`.

## 9. Failure triage

| Symptom | Likely cause | What to do |
|---|---|---|
| `no IPU/E2100 hints in lspci` from raw `discover` | Real hint-matching gap, or genuinely no IPU on this host | Check `lspci -nn` yourself for the PCI device ID as described in Step 1 above |
| `physical_functions: []` from raw `discover` even though a device was found | IPU present but no netdev's `parentdev` matched its PCI address (rare — usually means the netdev probe ran on the wrong host/namespace) | Run `ip -d link show \| grep -A0 parentdev` yourself and compare against the device's `pci_address` from the same JSON |
| `INTEL_VM_TO_VM_DISCOVERY_REQUIRED` / `INTEL_NOT_READY` from preflight | Example placeholders, or no OPI/P4 endpoint configured yet | Expected before filling the config; not a stop-everything error |
| `configure-forwarding` returns `INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED` | Neither `opi.endpoint` nor a P4Runtime/IPDK pipeline proven — **expected on most hosts today, not a bug** | Do not stop here: run stages 12–16 anyway (see [§8](#8-stages-316--the-same-labctl-vm2vm-pipeline-as-nvidia)). Configure `opi.endpoint` or a P4Runtime/IPDK pipeline only if you specifically need stage 11 to succeed; see [`intel-vm-to-vm-capability-detection.md`](intel-vm-to-vm-capability-detection.md) |
| Don't know what to put in `p4.forwarding_entries` (table/flow names) or `forwarding.endpoints[].representor` | These are IPU-side values from your compiled P4 program and running ACC — not something to guess from this repo | Do the ACC-side bring-up and discovery steps in [`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) first |
| `prepare-vfs` returns `status: skipped, executed: false` under `--apply` | Either `safety.dry_run_default: true` is still set in your config (forces dry-run regardless of `--apply` — see `tools/labctl.py`'s `_dry_run()`), or `dpu.pf_name` is unset/wrong | Set `dry_run_default: false` in the config once you mean to actually create VFs, confirm `dpu.pf_name` matches Step 1's discovery output, then re-run with `--apply` |
| Everything else (VF/VM stages, IOMMU, SSH, cloud-init issues) | Same as NVIDIA — these stages are vendor-agnostic | See the matching row in [the NVIDIA runbook's triage table](first-run-nvidia-bluefield-dpu-mode.md#23-failure-triage-matrix) |

Operator control point remains `labctl.py`. Do not hand-run `p4rt-ctl`,
`opi-*` CLIs, or DPU-Operator `kubectl` commands for this use case outside
of what `labctl` plans and logs.
