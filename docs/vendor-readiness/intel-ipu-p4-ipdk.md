# Intel IPU: P4Runtime / IPDK control path

An **alternate, independent** control plane for Intel IPU (E2000/E2100
class) VM-to-VM forwarding, alongside — not instead of, and not
dependent on — the OPI/DPU Operator path documented in
[`intel-ipu-e2100.md`](intel-ipu-e2100.md) and
[`intel-ipu-implementation.md`](intel-ipu-implementation.md).

## Why this exists as a separate path

The Infrastructure Programmer Development Kit (IPDK) ships `infrap4d`,
a daemon that exposes **P4Runtime** (a gRPC control-plane API,
`p4.v1`) and a table-driven interface (TDI) over an IPU's
programmable pipeline. This mechanism:

- **Predates** Red Hat's OpenShift DPU Operator and the OPI daemon
  model — it is Intel's original, lower-level IPU control story, not
  a fallback invented after the Kubernetes-native path.
- Does **not require** an OPI daemon, a DPU Operator CR, or
  Kubernetes/OpenShift to be present at all. A lab could reach
  `infrap4d` directly over gRPC with nothing else installed.
- Is **not** a P4-compiles-to-every-vendor abstraction. This project
  does not build or claim a universal P4 cross-vendor pipeline
  compiler (see `docs/architecture/overview.md`, "what this project
  is not"). This is Intel-specific plumbing to Intel's own daemon.

Because of this, ODSE's capability-detection model
(`plugins/intel_ipu/vm_to_vm_capability.py`) treats a proven P4Runtime
pipeline (`p4_pipeline_configured`) and a proven OPI L2 forwarding API
(`opi_l2_forwarding_api`) as **alternate, either-is-sufficient** proofs
of the same underlying capability — proving one is not gated on the
other, and neither is preferred by default.

## Real-hardware reference: ipdk-io/ipu-playbook

[`ipdk-io/ipu-playbook`](https://github.com/ipdk-io/ipu-playbook) is
the official IPDK automation repo for real Intel IPU E2100 hardware
(Apache-2.0, actively maintained). Its `ovs_offload/ovs_offload_lnw.py`
tool is the real-world counterpart to this repo's mock/`cli` client: it
SSHes host → IMC → ACC, loads the `fxp-net_linux-networking` P4
package, starts `infrap4d`, and drives the exact same `p4rt-ctl`
utility this repo's `mode="cli"` shells out to
(`set-pipe`/`get-pipe`/`add-entry`/`dump-entries`, confirmed against
[`p4rt-ctl` client docs](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/clients/p4rt-ctl.rst)
— `ipdk.io` itself now returns HTTP 500; see the callout at the top of
[`intel-ipu-acc-bringup.md`](../operations/intel-ipu-acc-bringup.md)).
It confirms:

- No OPI daemon, no DPU Operator, no Kubernetes anywhere in that tool
  — P4Runtime/IPDK really is independent of the OPI/DPU-Operator path,
  not a fallback of it.
- A real 3-tier SSH topology (host/IMC/ACC), a real P4 artifact set
  (`p4Info.txt`, `tdi.json`, `context.json`, `entries.json`, compiled
  `.pkg`), and a real representor-mapping convention: index-paired
  `vf_interfaces[i]` ↔ `acc_pr_interfaces[i]` arrays.
- Intel's real recipe **does** end in an OVS bridge built from ACC
  port representors — P4Runtime/`infrap4d` produces the representors
  and the offload, but OVS is still the bridging layer on top. Don't
  read "P4Runtime is independent of OPI" as "P4Runtime has nothing to
  do with OVS" — those are separate claims. This repo now implements
  that OVS path too, as a third alternate `intel.control_plane`
  choice (`ovs`, alongside `opi` and `p4`) — see
  [`intel-ipu-ovs-p4.md`](intel-ipu-ovs-p4.md). It is **not** an
  independent hardware mechanism from the `p4rt-ctl`-over-SSH path
  documented in this file: both are P4Runtime clients of the same
  `infrap4d` instance, just via a different front-end (ordinary
  `ovs-vsctl`/`ovs-ofctl` instead of `p4rt-ctl`).

This repo does **not** replicate that tool's package-flashing/IMC-reboot
step, or its tmux-based host VM bring-up — those are firmware/pipeline
lifecycle and generic VM-prep concerns respectively, out of scope for a
per-operation P4Runtime client (see "What stays out of scope" below).

## What exists in this repo today

- `plugins/intel_ipu/p4runtime.py` — `P4RuntimeClient`, mirroring the
  mock-first structure of `plugins/intel_ipu/opi.py`, with three modes:
  - `mode="mock"` returns configured stand-ins from
    `mock_pipeline_config` / `mock_table_entries` — no real IPU, no
    real `infrap4d`, nothing invented beyond what the config says.
  - `mode="cli"` shells the real `p4rt-ctl` utility over SSH to the ACC
    — the same mechanism `ovs_offload_lnw.py` uses. Only subcommands
    confirmed against the official `p4rt-ctl` docs are implemented:
    `get-pipe` (pipeline capability probe), `add-entry` (table entry
    write, FLOW string passed through verbatim — this client does not
    build FLOW strings from a DSL), `dump-entries` (raw counters dump,
    **not** parsed into `TelemetrySample` because the exact output
    format isn't pinned here). `del-entry` deliberately returns
    `not_implemented` rather than guessing syntax that isn't confirmed
    for `p4rt-ctl` specifically. SSH execution reuses
    `plugins.base.remote.RemoteCommandRunner`/`ssh_params()` — the same
    machinery NVIDIA's DPU-mode SSH probing already uses — and is gated
    by the same `remote_execution.dry_run_default` dual-gate convention
    (see `docs/operations/dpu-remote-execution.md`).
  - `mode="grpc"` is a **placeholder only**. It logs a warning and
    returns `not_implemented`; **no RPC is ever sent**. There is no
    pinned `p4.v1` protobuf/grpcio dependency in this repo — `cli` mode
    is the realistic near-term path; `grpc` exists so the config schema
    and error messaging stay honest about that gap.
  - `probe_pipeline_capability(config)` — flat discovery-config helper
    for the `labctl intel-nf` path. Only mock (always) and `cli` (only
    when the ACC SSH call actually executed and returned a non-empty
    pipeline) can return `True`; `grpc` mode never can.
  - `P4RuntimeClient.from_config()` reads the flat discovery-style
    config (`labctl intel-nf` path); `P4RuntimeClient.from_lab_config()`
    reads the nested `lab-intel-vm2vm.yaml` shape (`p4:` block for
    mode/endpoint/device_id, the shared `dpu:` block for ACC SSH fields
    — the ACC reuses NVIDIA's DPU-mode SSH field shape rather than a
    separate block — and `remote_execution:` for the dry-run gate).
- `plugins/intel_ipu/vm_to_vm_capability.py` — `p4_or_pipeline_path`
  is one of three alternate control-plane facts (the others are
  `opi_or_plugin_l2_api` and `ovs_p4_control_plane`, see
  [`intel-ipu-ovs-p4.md`](intel-ipu-ovs-p4.md)); any one, combined with
  `vfs_manageable` and `vf_endpoint_map`, is sufficient to prove
  capability. A configured
  `p4.endpoint` (or `dpu.p4_endpoint` / top-level `p4_endpoint`) also
  counts the same way a configured `opi.endpoint` does when deciding
  whether to report `OPI_ENDPOINT_REQUIRED` vs plain
  discovery-required — configuring *either* is enough to move past
  "no control-plane endpoint at all". `_maybe_probe_p4_live()` fills in
  `p4_pipeline_configured` with a **real** `p4rt-ctl get-pipe` result
  — not mock-only — but only when `p4.mode == "cli"` **and**
  `remote_execution.dry_run_default == false` are both explicitly set;
  it never runs implicitly, and an explicit `samples["p4_pipeline_configured"]`
  (including `False`) always overrides it.
- `plugins/intel_ipu/backend.py` — `configure_forwarding()` builds a
  P4Runtime-table-entry-shaped placeholder object (not an OPI
  `NetworkAttachment`-shaped one) when capability was proven via the
  P4 path specifically. In dry-run (the default, and always true when
  `--apply` is not passed and the readiness gate has not cleared) it
  stops there, same as the OPI path. **Under `--apply`** (`dry_run=False`,
  once `tools/apply_readiness_gate.py` has cleared) it calls
  `_submit_p4_forwarding()`, which actually calls
  `P4RuntimeClient.write_table_entry()` per endpoint — a real
  `p4rt-ctl add-entry` over SSH in `mode="cli"`, an in-memory write in
  `mode="mock"`, or `not_implemented` in `mode="grpc"` (no protobuf
  stubs). This still never invents a P4 table/action name or FLOW
  string: every `ForwardingEndpoint.vf_index` must have a matching
  entry under the new `p4.forwarding_entries` config list (`vf_index`,
  `table`, `flow`, optional `switch`) or `configure_forwarding()`
  refuses outright, naming the missing endpoint(s), instead of
  submitting anything partial. `success` is only ever `True` when every
  endpoint's write actually executed with `status: ok` — an
  unreachable ACC or missing `p4rt-ctl` in `cli` mode surfaces as
  `success: false`, not a fabricated success. See
  `tests/vendor/intel/test_intel_configure_forwarding_p4.py` for the
  exact refuse/submit/fail-honestly behavior. The `ovs` control plane
  (`intel.control_plane: ovs`) is wired the same way via
  `_submit_ovs_forwarding()` — see
  [`intel-ipu-ovs-p4.md`](intel-ipu-ovs-p4.md). **What is still not
  wired**: the OPI path (`opi.deploy_network_function()`) — `http` mode
  there is still a pure placeholder, so `configure_forwarding()` never
  submits anything for `control_plane == "opi"` regardless of
  `--apply`.
- `config/lab-intel-vm2vm.example.yaml` — a `p4:` block (`mode`,
  `endpoint`, `device_id`, `artifacts:` with the real IPDK artifact set,
  `vf_interfaces`/`acc_pr_interfaces` representor-mapping arrays),
  parallel to the existing `opi:` block, plus ACC SSH fields added to
  the shared `dpu:` block and a `remote_execution:` gate block.

## What stays out of scope

- **P4 package flashing + IMC reboot** (`ovs_offload_lnw.py`'s
  `load_package`). This is firmware/pipeline-image lifecycle, the same
  bucket as `<IPU_FW_BUNDLE>` handling elsewhere in this repo:
  opt-in-only, no blob in git, default-off. It does not belong in a
  per-forwarding-operation client.
- **Host VM namespace/VF setup and tmux choreography.** Generic VM
  bring-up is handled by this repo's `qemu/images` + libvirt workflow,
  not per-vendor P4 code.

**For the "how do I actually get artifacts onto the ACC, start
`infrap4d`, load a pipeline, and find the real table/action names for
`p4.forwarding_entries`" steps this section deliberately excludes**,
see [`../operations/intel-ipu-acc-bringup.md`](../operations/intel-ipu-acc-bringup.md)
— a hands-on runbook, not a code-scope description like this file.

## What is still discovery-required per lab

- Whether the specific IPU SKU/firmware on hand actually runs
  `infrap4d` and answers `p4rt-ctl`/P4Runtime at all — not assumed by
  anything above; `mode="cli"` reports `pipeline_configured: None`
  (not `False`) when the ACC is unreachable or `p4rt-ctl` isn't found,
  same honesty rule as everywhere else in this repo.
- The exact `p4info` / compiled pipeline binary for that SKU, and the
  P4 program's real table/action names (needed for `add-entry`'s FLOW
  string). Do not commit vendor P4 programs, `p4Info.txt`, `tdi.json`,
  `context.json`, or compiled pipeline binaries to this repo; treat
  every path under `p4.artifacts` as per-lab and gitignored locally.
- Whether VF-to-table-entry mapping is 1:1, requires representor-like
  identifiers, or uses some other IPU-specific key. The
  `vf_interfaces`/`acc_pr_interfaces` config arrays document the
  convention `ovs_offload_lnw.py` uses; this project does not invent a
  mapping beyond what's configured.
- `p4rt-ctl del-entry` exact syntax (not confirmed against the official
  `p4rt-ctl` docs at the time this was written).
- A real gRPC client, if `cli`-mode-over-SSH is ever insufficient. Pin
  an IPDK/P4Runtime version, vendor or generate the `p4.v1`
  protobuf/grpcio stubs, and replace `P4RuntimeClient._grpc_placeholder`
  call sites with real RPCs. None of that exists yet.

## Relationship to the vendor capability matrix

`docs/vendor-readiness/vendor-capability-matrix.md` does not carry a
dedicated P4Runtime/IPDK row for every vendor (this project does not
claim P4 as a universal path). For Intel specifically: this path is
mock-implemented in the codebase but hardware/`infrap4d` connectivity
is unvalidated, i.e. the same `discovery-required` posture as the rest
of Intel's datapath rows — implementing the client does not change
that rating on its own.
