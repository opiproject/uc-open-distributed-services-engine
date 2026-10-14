# vendor_nvidia_bluefield

Vendor-specific Ansible role for **NVIDIA BlueField-2/3** (first ODSE
hardware track). Companion design doc:

[`docs/vendor-readiness/nvidia-bluefield.md`](../../../docs/vendor-readiness/nvidia-bluefield.md)

This role is **documentation and variable contract first**. It must
**not** embed DOCA-release-specific `mlxconfig` / BFB / `mst` command
lines in tasks until those strings are supplied as inventory variables
looked up from NVIDIA docs for **`<DOCA_VERSION>`**.

Generic datapath work stays in:

| Concern | Role |
|---|---|
| Host packages, IOMMU, OVS install | `host_base` |
| DPU OS baseline | `dpu_base` |
| `sriov_numvfs` | `sriov_setup` |
| `devlink` eswitch | `switchdev_setup` (vendor hooks only if `devlink` is not enough) |
| Bridges / hw-offload | `ovs_setup` |
| OPI adapter process | `opi_services` (`opi_services_backend: nvidia`) |

## Intended responsibilities (TODO)

When implemented, this role should:

1. Record/assert lab identity: `<BF_MODEL>`, `<DOCA_VERSION>`,
   `<FW_VERSION>`, DPU vs NIC mode (fail if unknown — do not guess).
2. Optionally apply **firmware-level** VF / mode settings using
   `nvidia_bluefield_sriov_fw_cmd` (operator-supplied command template
   from vendor docs — not hardcoded in git).
3. Push BFB / reboot **only** when explicitly requested
   (`nvidia_bluefield_apply_bfb: false` by default).
4. Register `switchdev_setup_vendor_hooks` if the BFB needs a pre/post
   eswitch command (`<NVIDIA_ESWITCH_CMD>`).
5. Not own representor naming; discovery is
   `tools/topology-discovery/vf_to_representor_map.py`.

## Safety

- Default **check/discover**, not mutate firmware.
- Never run host-side eswitch changes when docs for this train say DPU
  mode owns switchdev on Arm only (see vendor doc §2 and §4).
- Do not use the experiment PF as the only SSH path.
- BFB install is disruptive; require an explicit boolean.

## Datapath order (after this role’s discovery)

1. Kernel OVS + TC offload (baseline).
2. VFIO passthrough (`qemu/vfio/vf-passthrough.md`).
3. Optional: vDPA, OVS-DPDK, OVS-DOCA, OVN-Kubernetes DPU, OpenStack
   off-path — each needs placeholders filled from NVIDIA docs.

## OPI

`kubernetes/opi/adapter/vendor_nvidia.py` still raises
`UnsupportedOperation`. Do not set `opi_services_manage_service: true`
until `list_devices()` is implemented.
