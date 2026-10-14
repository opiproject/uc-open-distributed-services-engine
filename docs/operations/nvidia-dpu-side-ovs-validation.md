# NVIDIA DPU-side OVS / OVS-DOCA validation

In `nvidia-bluefield-dpu-mode`, Open vSwitch runs on the **BlueField Arm
OS**. Host `ovs-vsctl` is not the lab forwarding control plane.

`validate_dpu_ovs_environment(config)` checks (via planned SSH, dry-run
default):

1. `ovs-vsctl` exists
2. `ovs-appctl` exists
3. OVS service status can be queried
4. OVS database is reachable
5. `ovs-vsctl show` works
6. hw-offload config can be read
7. OVS-DOCA only if `doca` is in `datapath_types`

`validate-config` is **read-only**. It plans SSH probes (`command -v ovs-vsctl`,
`datapath_types`, `ovs-vsctl show`). It does **not** plan host or DPU
`ovs-vsctl add-br`. Bridge create and representor add belong to
`prepare-ovs` / `configure-forwarding`, which use `RemoteCommandRunner`
to the DPU in this profile.

Statuses: `OVS_AVAILABLE`, `OVS_MISSING`, `OVS_SERVICE_NOT_RUNNING`,
`OVS_DB_UNREACHABLE`, `OVS_DOCA_AVAILABLE`, `OVS_DOCA_MISSING`,
`DPU_REMOTE_UNREACHABLE`.

If `nvidia.datapath=ovs-doca` and DOCA is missing, labctl `prepare-ovs`
refuses unless fallback is explicitly enabled.

Do not treat host OVS as a substitute in this profile.

**Note on `OVS_DOCA_AVAILABLE`:** this status means `doca` was found in
`datapath_types` — i.e. the OVS build supports DOCA. It does **not**
check the separate `doca_initialized` field NVIDIA's own `ovs-vsctl
list Open_vSwitch` output reports, which can still be `false` on a
card that lists `doca` here. For the concrete commands to activate and
confirm OVS-DOCA is actually initialized (not just listed), and how to
get both OVS-Kernel and OVS-DOCA available on the same card in the
first place, see
[`nvidia-bluefield-dpu-bringup.md`](nvidia-bluefield-dpu-bringup.md).
