# vendor_marvell_octeon

Vendor-specific Ansible role for **Marvell OCTEON** + **DAO / DPDK**
offload. Companion:

[`docs/vendor-readiness/marvell-octeon.md`](../../../docs/vendor-readiness/marvell-octeon.md)

Read that doc’s **Generic OVS vs DAO** section before adding tasks.
This role must **not** treat kernel `switchdev` as a given (matrix:
**discovery-required**). NVIDIA `ovs_setup` kernel+TC is a different
datapath.

Skeleton: **discover/check first**. No SDK install by default.

## Intended responsibilities (TODO)

1. Record `<OCTEON_SKU>`, `<DAO_VERSION>`, `<DPDK_VERSION>` (fail if
   unset when mutate is requested).
2. Optional DAO/SDK bring-up only if
   `marvell_octeon_install_dao: false` is overridden **and**
   `marvell_octeon_dao_install_cmd` comes from vendor docs (not git).
3. Map VFs to **DPDK representors** (`<DPDK_REPR>`), not assumed
   netdev names.
4. Start/stop `<DAO_OVS_APP>` as a documented service — do not
   silently replace distro `openvswitch` unless
   `marvell_octeon_replace_ovs: false` stays false (default).
5. Wire `opi_services_backend: marvell` after
   `MarvellOcteonBackend.list_devices()` exists.

`sriov_setup` / `switchdev_setup` / `ovs_setup` are **opt-in after
devlink/DAO discovery**, not hard dependencies.

## Safety

- Default: no firmware load, no hugepage rewrite, no OVS replacement.
- Do not use the experiment uplink as the only SSH path.
- Do not copy `vendor_nvidia_bluefield` eswitch hooks here.

## Tests to enable later

First-packet classification vs post-offload HW vs `rte_flow` dumps —
generic OVS appctl **and** `<RTE_FLOW_DUMP_CMD>`, never only one.

OVN-Kubernetes DPU mode and OpenStack off-path are **future** for
this vendor — out of role scope.
