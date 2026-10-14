Both answers are grounded in what's actually implemented in this repo (`plugins/nvidia_bluefield/ovs.py`, `plugins/intel_ipu/`, and the vendor-readiness docs), not generic DPU advice.

## 1. Switching NVIDIA datapath: `system` → `doca`

Your current run is on **`system`** (kernel OVS-TC/ASAP²) — that's why every offloaded flow in `19-rerun.txt` shows `dp:tc`, and `ovs_version` reports a plain OVS build, not a DOCA one. `doca` means OVS-DOCA (DOCA Flow datapath instead of kernel TC), which is a heavier change than a config edit.

**First thing to do — verify the prerequisite exists, before touching config:**

```
ssh <dpu-mgmt-ip> sudo ovs-vsctl get Open_vSwitch . datapath_types
```

The plugin code (`plugins/nvidia_bluefield/ovs.py`) will **hard-refuse** `datapath_type=doca` unless `ovs-vsctl` itself reports `"doca"` in `datapath_types` — it never assumes this is available:

```
189:    if not cap.details.get("doca_datapath"):
190:        LOG.warning("enable_doca_init: OVS-DOCA datapath not listed; refusing")
```

If `doca` isn't listed, this isn't something `labctl` or a config edit can fix — you first need an OVS-DOCA-enabled OVS build on the Arm side, which comes from a specific DOCA/BFB train (per `docs/vendor-readiness/nvidia-bluefield.md` §8: "Package set that replaces distro OVS... confirm, do not assume"). That's an NVIDIA package/firmware step outside this repo.

**Once `doca` is confirmed present in `datapath_types`, the sequence is:**

1. `labctl vm2vm cleanup` (or `tools/lab_cleanup.py --apply`) — tear down the current `system`-datapath `br-int`, VF attachments, and VMs. Don't flip datapath type under live traffic/existing ports; `datapath_type` is effectively a bridge-recreation operation and existing kernel-`system` ports won't magically become `type=doca` ports.
2. Edit your local (gitignored) `config/lab-nvidia-vm2vm.yaml`:
   - `nvidia.datapath: ovs-doca`
   - `dpu.preferred_datapath: doca`
   - `ovs.datapath_type: doca`
3. Re-run from `validate-config` → `discover` (this re-probes `datapath_types` and sets the `doca_datapath` capability flag used by the refusal check above).
4. Re-run `prepare-ovs` / `configure-forwarding` with `--apply` — this calls `create_ovs_doca_bridge()`, which will error `OVS-DOCA not available (datapath_types=...)` rather than silently falling back if the build still doesn't list it.
5. Re-run `run-traffic` → `validate-offload` fresh. **Expect different evidence markers** — DOCA Flow offload won't show `offloaded:yes, dp:tc` (that's the kernel-TC heuristic); confirm with NVIDIA docs for your `<DOCA_VERSION>` what the equivalent marker is before trusting `ovs_offload_checker.py`'s current TC/dpctl heuristics under `doca`.

This is officially "Stage 9 (Advanced backend path)" in `docs/operations/vendor-maturity-execution-plan.md` — separate from and beyond the Stage 7 you just passed (`PASS_OFFLOAD_PROBABLE` on kernel TC).

## 2. Starting the same use case on Intel IPU (different server)

Set expectations first: per `docs/vendor-readiness/intel-ipu-implementation.md`, Intel is **not** at NVIDIA's parity — it's OPI/DPU-Operator-first, and `configure-forwarding` is expected to legitimately stop at `discovery-required` / `error=OPI_ENDPOINT_REQUIRED` until a real OPI endpoint exists. Don't expect to reach `validate-offload` on the first pass.

Steps:

1. **Copy the template** on the new server: `cp config/lab-intel-vm2vm.example.yaml config/lab-intel-vm2vm.yaml` (gitignored, same rule as NVIDIA).
2. **Discover for real, read-only, on that server** — do not reuse NVIDIA's PCI/PF values:
   ```
   python3 tools/inventory/dpu_inventory.py --pretty
   python3 tools/vendorctl.py discover --vendor intel-ipu --json
   ```
   A device only counts as an IPU if it reports **IPU**, **E2100**, or **"Infrastructure Processing Unit"** — an Intel `ice`/E810 NIC is explicitly **not** an IPU and will fail Stage 0/1.
3. Fill in `host.name`, `host.management_interface`, `host.ssh_user` — same safety rule as before: management interface must differ from `dpu.pf_name`.
4. Fill Intel-specific reachability fields instead of an SSH-to-Arm-OVS model:
   - `ipu.management_ip`, `ipu.redfish_endpoint`
   - `opi.endpoint` (use `opi.auth_mode: mock` unless you have a real OPI daemon/DPU Operator deployed)
5. Set `dpu.pf_name` / `dpu.pf_pci` from that server's own discovery output.
6. Run stages in the same labctl order, but expect an early, legitimate stop:
   - `validate-config` → `discover` → `prepare-vfs` (dry-run first) → `create-vms`/`attach-vfs`/`configure-vm-ips` (these are host/libvirt-level, vendor-agnostic — should behave like the NVIDIA run) → `configure-forwarding` (expect `discovery-required` or `error=OPI_ENDPOINT_REQUIRED` unless a real OPI endpoint or DPU Operator is reachable and configured).
7. To progress past `discovery-required`, you'd need to actually stand up a real OPI-compliant daemon or DPU Operator and confirm it via `list_devices()` — currently `plugins/intel_ipu/opi.py` and `dpu_operator.py` are documented no-mutate stubs.
8. Note `validation.collect_ovs_flows`/`collect_tc_filters` are `false` by default in the Intel example (no OVS representor model assumed) — `ethtool`, CPU stats, and vendor telemetry are the evidence sources there, not `ovs_offload_checker.py`'s dpctl/TC path.

Read `docs/vendor-readiness/intel-ipu-implementation.md`'s "Order of work" (discovery → Redfish/IMC → OPI daemon → DPU Operator → datapath **last**) before running anything — it explicitly warns not to copy the NVIDIA OVS-TC/representor sequence onto Intel.

`p4_pipeline_configured` is collected into `facts` and reported for visibility, but it is **not** one of the three facts required to flip `capability_proven` to `True` — only `vfs_manageable`, `vf_endpoint_map`, and `opi_or_plugin_l2_api` gate that. There is:

- No `plugins/intel_ipu/p4*.py` file — no P4Runtime client, no `infrap4d`/IPDK gRPC connection, no table-entry writer.
- No config schema field to point at a P4Runtime endpoint (`config/lab-intel-vm2vm.example.yaml` only has `opi.endpoint`, `dpu_operator.kubeconfig`, `ipu.redfish_endpoint` — nothing P4-shaped).
- `plugins/intel_ipu/backend.py` only mentions P4 in a comment/message string (`"Intel IPU packet pipeline, P4 path, or DPU-side network function (exact mechanism discovery-required)"`) — it's descriptive text, not executable.

So today: **we can't** "just use it" — it's a documented gap, not a hidden feature. If you want to actually exercise it, the real work is:

1. Add `plugins/intel_ipu/p4runtime.py` (mirroring `opi.py`'s mock-first pattern): a P4Runtime gRPC client that can do `GetForwardingPipelineConfig`/`Write TableEntry` against `infrap4d` (the IPDK P4Runtime daemon Intel ships for E2000/E2100-class IPUs).
2. Add a `p4` config block (endpoint, `p4info` path, pipeline binary) alongside `opi:`/`dpu_operator:` in the lab config schema.
3. Wire a real probe into `check_vm_to_vm_forwarding_capability()` that sets `p4_pipeline_configured=True` only when that connection and a loaded pipeline are actually confirmed (same "no fabricated capability" rule everything else here follows).
4. Decide whether `p4_or_pipeline_path` becomes an **alternate** proof of capability (i.e., P4Runtime *or* OPI L2 API, either is sufficient) rather than requiring OPI specifically — that's a one-line change to the `all(...)` gate once a real P4 signal exists.

I can build that if you want — happy to scope it as its own track.

## 2. History: yes, Intel IPUs predate Red Hat's DPU Operator

You're right, and this is exactly why the repo's capability list includes P4 as a separate fact from `dpu_operator_resource_model` and `opi_or_plugin_l2_api` — they're independent, and the P4/IPDK path is older:

- **IPDK (Infrastructure Programmer Development Kit)** — the open-source project (Intel-initiated) that predates the DPU Operator. It runs `infrap4d` on the IPU's Arm complex, exposing **P4Runtime** (gRPC) and **TDI** (Table Driven Interface) so you can push P4 table entries directly, plus a `p4-ovs`/OVS-translation layer that maps OVS-style flow semantics onto P4Runtime writes without any Kubernetes control plane involved at all.
- Intel also ships/shipped a lower-level **SDE (Software Development Environment)** for the IPU ASIC pipeline itself — the thing `p4c` compiles against — independent of any orchestrator.
- Early Mount Evans/E2000 devkits and N3000-class SmartNICs were absolutely brought up and driven this way: SSH or gRPC directly to the IPU's control plane, load a P4 pipeline, write table entries via `p4rt-ctl` (IPDK's OVS-like CLI over P4Runtime) or raw P4Runtime calls — no Kubernetes, no operator, no Red Hat component in the loop.
- **Red Hat's DPU Operator** is a later, Kubernetes-native *orchestration* layer on top of that — it doesn't replace P4Runtime/IPDK, it automates deploying and wiring the same underlying daemons (including, per public material, IPDK/`infrap4d` as one of its managed components) so you don't hand-roll it per node.

That's precisely the distinction the repo's capability model preserves: `dpu_operator_resource_model` and `p4_or_pipeline_path` are tracked as **separate, independent** facts rather than treating "DPU Operator present" as a prerequisite for anything P4-related. The doc language in `docs/architecture/intel-ipu-control-model.md` ("Do not assume representors, switchdev, or P4 rules") and `intel-ipu-e2100.md` currently treat OPI/DPU-Operator as the "strongest public ODSE-adjacent story" **only because it's the best-documented public path for this project so far**, not because it's the only or original one — the P4/IPDK path being unimplemented here is a gap in this repo's coverage, not a claim that it doesn't exist or came later.

If you have IPDK/`infrap4d` access on real E2000/E2100 hardware, that's arguably the more direct, historically-proven path to get VM-to-VM forwarding working on Intel than waiting on a DPU Operator deployment — worth prioritizing over OPI if that's what you actually have available. Want me to scope out the `p4runtime.py` plugin work?