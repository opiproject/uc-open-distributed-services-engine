# NVIDIA BlueField implementation flow (ODSE)

How to bring up **kernel OVS-TC** (default) and **optionally OVS-DOCA**
on NVIDIA BlueField using this repo’s plugins. This is an operator
sequence, not a certification.

**OVS-DOCA is NVIDIA-specific.** It requires NVIDIA DOCA packages and an
OVS build that lists `doca` in `datapath_types`. Do **not** copy this
path to Intel, Marvell, or AMD. Do **not** assume OVS-DOCA exists
because BlueField is present.

Pin `<DOCA_VERSION>` in the lab log. Frozen CLI in older blogs is
unreliable. Companion:
[`nvidia-bluefield.md`](nvidia-bluefield.md),
`plugins/nvidia_bluefield/`, `tools/vendorctl.py`.

Default plugin behavior is **dry-run**. Mutations need an explicit
`dry_run=False` (and `vendorctl --apply` does not yet flip that for all
backends).

---

## Flow

### 1. Discover devices

Read-only. Use `plugins.nvidia_bluefield.discovery.discover()` or:

```text
python tools/vendorctl.py discover --vendor nvidia-bluefield --json
```

Expect `DPUDevice` / `PhysicalFunction` only when `lspci`/`ethtool`
contain Mellanox, NVIDIA, mlx5, BlueField, or ConnectX. Empty lists are
valid on a laptop. Serial, switchdev, and offload flags stay **null**
until proven.

### 2. Configure switchdev

On the OS that owns the eswitch (often **DPU Arm**, not the host, in
DPU mode). `plugins.nvidia_bluefield.switchdev.set_eswitch_mode_switchdev(pci)`.

- Default `dry_run=True`.
- Refuses if VFs look bound unless `force=True`.
- Uses `devlink dev eswitch set pci/<pci> mode switchdev`.
- Sysfs fallback stays **off** unless `allow_sysfs=True` and `netdev` is
  given.

Confirm with `get_eswitch_mode(pci)` → `mode=switchdev`. Host-legacy in
DPU mode is a common footgun — see `nvidia-bluefield.md`.

### 3. Discover representors

After switchdev, `discover_representors()` (discovery or switchdev
module). Trust `devlink` `flavour pcivf` / `pcisf` or `portname`
`pf0vf0` / `pf0sf0`. **Do not** treat `eth0v1` as a representor.

Record netdev names; later `add_representor_port` will not invent them.

### 4. Enable OVS hw-offload

Kernel path (default):

`plugins.nvidia_bluefield.ovs.enable_ovs_hw_offload(dry_run=True)`

→ `ovs-vsctl set Open_vSwitch . other_config:hw-offload=true`

This is **config**, not proof of `in_hw` (FN-08 vs FN-17). Restart
policy is left to `ansible/roles/ovs_setup`.

### 5. Optionally enable OVS-DOCA

Only if `ovs-vsctl get Open_vSwitch . datapath_types` includes `doca`
(see `probe_ovs_capabilities()`).

Then `enable_doca_init()` may set a **placeholder**
`other_config:doca-init=true` — confirm the exact key for
`<DOCA_VERSION>`.

If `doca` is absent: **skip**. Keep kernel OVS-TC. Installing DOCA
userspace without a matching OVS build does not make `datapath_type=doca`
valid.

### 6. Create bridge

- Default: `create_ovs_bridge("br-int", datapath_type="system")`
- OVS-DOCA only: `create_ovs_doca_bridge("br-int")` which sets
  `datapath_type=doca` **after** the capability check. If the type is
  missing, the call returns `skipped`.

### 7. Attach PF and representors

`add_representor_port(bridge, representor, interface_type="system")` for
kernel representor netdevs (PF uplink and `pf0vfN`).

`add_doca_port` only if both `doca` datapath **and** iface type `doca`
are listed; it will not guess `dpdk`.

Do not attach a name that discovery did not return.

### 8. Run validation

1. `dump_ovs_state()` — `ovs-vsctl show`, optional `dpctl/show`.
2. `tools/offload-checker` — `unknown` is valid; no fake `offloaded:yes`.
3. Functional: FN-07 (bridge), FN-08 (hw-offload knob), FN-17 (evidence)
   when traffic exists.
4. Cert Level 2 needs representors **and** honest offload evidence, not
   Kind.

---

## Capability honesty

| Claim | When it is allowed |
|---|---|
| Kernel OVS + hw-offload knob | `enable_ovs_hw_offload` applied and `ovs-vsctl` shows it |
| Hardware offload | Checker/vendor markers (FN-17), not step 4 alone |
| OVS-DOCA | `doca` in `datapath_types` **and** DOCA packages for `<DOCA_VERSION>` |
| switchdev | `get_eswitch_mode` == switchdev |

---

## Code map

| Step | Module |
|---|---|
| 1 | `plugins/nvidia_bluefield/discovery.py` |
| 2 | `plugins/nvidia_bluefield/switchdev.py` |
| 3 | `discovery.discover_representors` / `switchdev.discover_representors` |
| 4–7 | `plugins/nvidia_bluefield/ovs.py` |
| 8 | `ovs.dump_ovs_state`, `tools/offload-checker`, FN-* |

Ansible still owns package install (`host_base`) and idempotent
`ovs_setup`. Plugins are the Python/vendorctl path for the same sequence.
