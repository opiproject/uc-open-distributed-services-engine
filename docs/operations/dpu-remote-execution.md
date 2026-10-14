# DPU remote execution

When NVIDIA BlueField uses **DPU mode**, OVS/OVS-DOCA and representor
commands run on the BlueField Arm OS, not on the x86 host.

```text
python tools/remote_exec.py --config config/lab-nvidia-vm2vm.example.yaml \
    --command ovs-vsctl -- show --json
```

Default is **dry-run**. `--apply` is required to send SSH. `labctl.py`
remains the operator entry point; this tool is an implementation detail
behind `plugins.base.remote.RemoteCommandRunner`.

## Config

```yaml
dpu:
  management_ip:
  ssh_user:
  ssh_port: 22
  ssh_key_path:
  sudo: true
  command_timeout_seconds: 30

remote_execution:
  enabled: true
  target: dpu
  dry_run_default: true
```

## Safety

- `shell=True` is off unless a caller explicitly requests it.
- SSH argv is a list (`ssh … -- ovs-vsctl show`).
- Key paths and credentials are redacted in logs.
- Planned/captured stdout is stored under
  `tests/results/<vendor>/<timestamp>/remote/` when a run directory is
  provided.
- Do not SSH as a way to change the host management interface.
- Every `run_ssh` call sets `-o ConnectTimeout=10`, so a genuinely
  unreachable DPU fails fast instead of hanging for the full per-command
  timeout below.

## `discover-representors` times out even though `ssh <dpu>` works fine by hand

With `representor_mapping.location: dpu` and `remote_execution.dry_run_default:
false`, `labctl vm2vm discover-representors` makes **3 sequential** real SSH
round trips to the DPU (`ip -d link show`, `devlink port show`, `ovs-vsctl
show` — see `collect_dpu_samples_via_ssh()` in
`tools/topology-discovery/vf_to_representor_map.py`), each its own fresh SSH
connection. This is not a key/auth problem — if `ssh <user>@<dpu-ip>` works
without a password prompt, the same key/host/user is what `labctl` uses.

It's a timeout-budget problem: `dpu.command_timeout_seconds` (default `30`)
bounds each of those 3 commands individually, and labctl automatically sizes
the enclosing subprocess timeout as `3 * command_timeout_seconds + 20`
(default 110s total) so three genuinely-slow-but-working calls don't get
killed by a timeout meant for a single quick one. If it still times out at
that budget, raise `dpu.command_timeout_seconds` in your lab config:

```yaml
dpu:
  ...
  command_timeout_seconds: 60   # was the 30s default
```

If it *still* times out even at a generous value, that points at a real
problem on the DPU side rather than a slow link — e.g. `ovs-vsctl show`
hanging because `ovsdb-server`/`openvswitch-switch` isn't responding. Time
each probe by hand to isolate which one is slow/hanging:

```bash
time ssh <user>@<dpu-ip> -- sudo -n ip -d link show
time ssh <user>@<dpu-ip> -- sudo -n devlink port show
time ssh <user>@<dpu-ip> -- sudo -n ovs-vsctl show
```

## `configure-forwarding --apply` reports every command `"timed_out": true`

Same root cause and same fix as `discover-representors` above (usually the
DPU's own `sudo` doing a slow/failing hostname lookup on itself), but a
different code path: `plugins.nvidia_bluefield.ovs`'s `create_ovs_bridge` /
`add_representor_port` / `enable_ovs_hw_offload` helpers don't know about
lab config, so they each ask for a flat 15s per `ovs-vsctl`/`ovs-ofctl` call.
`plugins/nvidia_bluefield/backend.py`'s DPU-mode runner raises that to
`dpu.command_timeout_seconds` (same knob as above) whenever it's larger than
15s, so setting `command_timeout_seconds: 45` (or higher) in `dpu:` also
fixes `configure-forwarding`, with no separate config needed.

**Don't know how to prepare the DPU side at all (BFB/DOCA image,
confirming OVS-Kernel vs. OVS-DOCA are actually available), before any
of these SSH commands have anything real to reach?** See
[`nvidia-bluefield-dpu-bringup.md`](nvidia-bluefield-dpu-bringup.md).

A `"timed_out": true` command is always reported as a real failure
(`"success": false`, `"offload_confirmed": false`) — it is never silently
treated as if it had succeeded, even though a timed-out command's
`returncode` is `null`, the same value dry-run commands use for "never
actually invoked".

## `validate-offload` DPU probes always show `"planned": true` and no `stdout`, no matter what flags I pass

`validate-offload` itself has no `--apply` (see the runbook: it is
read-only, so there is no destructive-operation gate to satisfy). Older
builds of `tools/offload-checker/ovs_offload_checker.py` had a separate
bug: its `_dpu_probe_section()` helper (used for `ovs-vsctl show`,
`dpctl/show`, `dpctl/dump-flows`, `ovs-ofctl dump-flows`, `tc -s filter
show`, `ethtool -S` in DPU mode) hardcoded `dry_run=True` on every SSH
call, so it could **never** actually reach the DPU regardless of
`remote_execution.dry_run_default`, `--apply`, or anything else — it was
always plan-only.

This is now fixed: `_dpu_probe_section()` honors
**`remote_execution.dry_run_default`** in your lab YAML, the same knob
`discover-representors` already uses. With it `false`, `validate-offload`
makes up to `4 + 2 * <number of interfaces>` sequential SSH round trips
to the DPU (four bridge-level probes, plus a `tc` and an `ethtool` call
per representor interface). Each one is subject to `dpu.command_timeout_seconds`
as its per-command timeout, and `labctl`'s own subprocess wrapper around
`ovs_offload_checker.py` sizes its budget the same way it does for
`discover-representors`: `(4 + 2 * <interfaces>) * dpu.command_timeout_seconds + 20`.
A slow management link needs `dpu.command_timeout_seconds` raised, not a
code change.
