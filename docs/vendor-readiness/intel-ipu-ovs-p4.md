# Intel IPU: OVS control path (P4Runtime-backed, not a separate hardware mechanism)

A **third alternate** control plane for Intel IPU VM-to-VM forwarding,
selectable via `intel.control_plane: ovs` alongside `opi` and `p4`
(see [`intel-ipu-p4-ipdk.md`](intel-ipu-p4-ipdk.md) for the other two).
Read that file first — this one assumes its P4Runtime/`infrap4d`
background.

## Why this is not "a fourth independent path"

Unlike `opi` vs. `p4` (which really are independent hardware/software
mechanisms — see `intel-ipu-p4-ipdk.md`'s "Why this exists as a
separate path"), `ovs` is **not** independent of `p4`. Per IPDK's own
docs
([es2k-linux-networking.md](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/apps/lnw/es2k/es2k-linux-networking.md)
— `ipdk.io` itself now returns HTTP 500; see the callout at the top of
[`intel-ipu-acc-bringup.md`](../operations/intel-ipu-acc-bringup.md)
for why, and for the corrected link source used throughout this file):

> `infrap4d`: includes a P4Runtime server... programs IPU E2100 via TDI.
> `ovs-vswitchd`: integrated with p4runtime intelligence and acts as a
> **gRPC client**. Programs IPU E2100 ... by communicating with the
> gRPC server.

The IPDK-patched ("enhanced") `ovs-vswitchd` is itself a **P4Runtime
client** of the exact same `infrap4d` gRPC server that
`plugins/intel_ipu/p4runtime.py`'s `mode="cli"` drives directly via
`p4rt-ctl`. Choosing `intel.control_plane: ovs` instead of `p4` is a
choice of *front-end onto the same backend*, not a choice between two
independent hardware datapaths. This is a materially different
relationship than NVIDIA's OVS-TC vs. OVS-DOCA choice, where both
options are genuinely OVS `datapath_type` values with no non-OVS
raw-DOCA-Flow-API client implemented in this repo at all (see
`plugins/nvidia_bluefield/ovs.py`'s `enable_doca_init`/
`create_ovs_doca_bridge`). For Intel, `p4` and `ovs` are two clients of
one server; for NVIDIA, `system` and `doca` are two datapaths under one
client. Do not read a future "Intel now has an OVS row too" capability-
matrix entry as symmetric with NVIDIA's OVS-TC/OVS-DOCA row without
this distinction.

## Why choose this over raw `p4rt-ctl` (`intel.control_plane: p4`)

- **No FLOW string needed for simple bridging.** `p4` mode requires
  `p4.forwarding_entries` — a real `{table, flow}` pair per endpoint,
  in your compiled P4 program's exact match/action syntax
  (`intel-ipu-p4-ipdk.md`'s "never invents a FLOW string" refusal
  rule). `ovs` mode only needs each endpoint's ACC port representor
  netdev name (`forwarding.endpoints[].representor`); per IPDK's
  Linux Networking recipe, `ovs-vswitchd`'s own dynamic MAC learning
  (first packet to/from each representor) programs L2 forwarding via
  P4Runtime automatically once representors are attached to a bridge
  — no explicit flow, no P4 table/action names.
- **Ordinary `ovs-vsctl`/`ovs-ofctl` semantics** instead of a
  pipeline-specific CLI, which is the interface most operators
  familiar with NVIDIA/kernel OVS already know.

Trade-off: `ovs` mode needs the IPDK-patched `ovs-vswitchd`/`ovsdb-server`
running on the ACC (per
[es2k-lnw-overlay-vms.md](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/apps/lnw/es2k/es2k-lnw-overlay-vms.md):
started as a separate process, with `OVS_P4_OFFLOAD=false` set before
first start) in addition to `infrap4d` — one more moving part than
`p4` mode's plain `p4rt-ctl`.

## What exists in this repo today

- `plugins/base/ovs.py` — the generic (not NVIDIA-specific)
  add-br/add-port/add-flow/capability-probe mechanics, extracted out of
  `plugins/nvidia_bluefield/ovs.py` so both vendors' real `ovs-vsctl`/
  `ovs-ofctl` calls share one well-tested code path
  (`vendor=` parameter only changes labeling). NVIDIA's OVS-DOCA-
  specific gating (`datapath_type=doca` validation, `doca-init`,
  `hw-offload`) stays in `plugins/nvidia_bluefield/ovs.py` — those are
  genuine NVIDIA/DOCA concepts with no Intel equivalent.
- `plugins/intel_ipu/ovs_p4.py` — Intel's thin ACC-SSH wrapper around
  `plugins/base/ovs.py` (`vendor="intel"`): `probe_capabilities()`,
  `create_bridge()`, `add_port()`, `add_flow()`, `dump_state()`, all
  over `plugins.base.remote.RemoteCommandRunner`/`ssh_params()` against
  the same `dpu:` SSH block `p4.mode="cli"` and NVIDIA's DPU-mode SSH
  use — there is no host-OVS fallback for Intel the way NVIDIA has one.
  `probe_control_plane_live()` is the honest live-capability proof:
  returns `True` only when every underlying `ovs-vsctl` SSH call
  actually executed and returned `0`; `None` (never `False`) on
  anything short of that.
- `plugins/intel_ipu/vm_to_vm_capability.py` — `ovs_p4_control_plane`
  is the third alternate control-plane fact (`opi_or_plugin_l2_api`
  and `p4_or_pipeline_path` are the other two); any one, combined with
  `vfs_manageable` and `vf_endpoint_map`, is sufficient to prove
  capability. `_maybe_probe_ovs_live()` fills it from a **real**
  `ovs-vsctl` probe over ACC SSH — never mock-only — but only when
  `intel.control_plane == "ovs"` **and**
  `remote_execution.dry_run_default == false` are both explicitly set
  (same dual-gate convention as `_maybe_probe_p4_live()`); an explicit
  `samples["ovs_p4_control_plane"]` (including `False`) always
  overrides it. When more than one control plane is proven at once,
  `opi` wins over `p4`, which wins over `ovs` (see the `via` selection
  in `check_vm_to_vm_forwarding_capability()`).
- `plugins/intel_ipu/backend.py`:
  - `configure_ovs_bridge()` (the `prepare-ovs` stage) refuses unless
    `intel.control_plane: ovs` is explicitly set — Intel's `opi`/`p4`
    control planes do not use a host/ACC OVS bridge. When it is set,
    this always plans with `dry_run=True` (same convention as NVIDIA's
    `prepare-ovs`); real bridge/port creation happens under
    `configure-forwarding --apply` below, not here.
  - `configure_forwarding()` builds an OVS bridge/port-shaped
    placeholder (not a P4Runtime-table-entry or OPI
    `NetworkAttachment`-shaped one) when capability was proven via the
    `ovs` control plane. In dry-run it stops there. **Under `--apply`**
    it calls `_submit_ovs_forwarding()`, which actually calls
    `ovs_p4.create_bridge()`/`add_port()` per endpoint over ACC SSH —
    real `ovs-vsctl add-br`/`add-port`. This never invents a
    representor netdev name: every `ForwardingEndpoint` must have
    `representor` set (from a proven `discover-representors` run, or
    `forwarding.endpoints[].representor` in the lab config) or
    `configure_forwarding()` refuses outright, naming the missing
    endpoint(s). `explicit_flows.enabled` additionally calls
    `ovs_p4.add_flow()` with a caller-provided flow string (symbolic
    port names require confirming your `ovs-ofctl` build supports
    them); plain bidirectional bridging does not need this at all.
    `success` is only ever `True` when every command that had to run
    actually executed with no errors and at least one port was
    configured — an unreachable ACC surfaces as `success: false`, not
    a fabricated success. See
    `tests/vendor/intel/test_intel_configure_forwarding_ovs.py` and
    `tests/vendor/intel/test_intel_ovs_p4.py` for the exact
    refuse/submit/fail-honestly behavior.
- `config/lab-intel-vm2vm.example.yaml` — `intel.control_plane`
  (`null`/`opi`/`p4`/`ovs`) selects between the three; the existing
  `ovs:` block (`bridge`, `datapath_type`, `fail_mode`, `ports`) is now
  documented as Intel-`ovs`-mode input, and
  `forwarding.endpoints[].representor` is documented as required for
  that mode.

## What stays out of scope

- Starting/confirming the IPDK-patched `ovs-vswitchd`/`ovsdb-server`
  processes themselves on the ACC, or setting `OVS_P4_OFFLOAD`/
  `--grpc-addr` for `ovs-p4rt`. That is ACC-image/process lifecycle
  (the same bucket as `infrap4d` startup in `intel-ipu-p4-ipdk.md`'s
  "what stays out of scope"), assumed already running before labctl's
  `prepare-ovs`/`configure-forwarding` stages touch it.
- A universal OVS-vs-P4Runtime abstraction across vendors. This project
  does not claim `intel.control_plane: ovs` and NVIDIA's
  `nvidia.datapath: ovs-doca` are the same kind of choice — see "Why
  this is not 'a fourth independent path'" above.

**For the "how do I actually start the patched `ovs-vswitchd`, confirm
it's P4Runtime-integrated, and find real ACC representor names" steps
this section deliberately excludes**, see
[`../operations/intel-ipu-acc-bringup.md`](../operations/intel-ipu-acc-bringup.md)
— a hands-on runbook, not a code-scope description like this file.

## What is still discovery-required per lab

- Whether the ACC actually has the IPDK-patched `ovs-vswitchd` running
  (vs. plain upstream OVS, which has no P4Runtime integration and would
  silently do nothing useful here) — not assumed; `probe_capabilities()`
  only reports what a live `ovs-vsctl get Open_vSwitch . datapath_types`
  over SSH actually returns.
- The real ACC port representor netdev names for each VF (the
  `vf_interfaces`/`acc_pr_interfaces` convention documented in
  `intel-ipu-p4-ipdk.md` and `config/lab-intel-vm2vm.example.yaml`'s
  `p4:` block applies here too) — never guessed from `vf_index`.
