# Marvell DAO cleanup

Default is dry-run. `--apply` is still required for mutations.

`plugins/marvell_octeon/cleanup.py` / `labctl vm2vm cleanup` / `lab_cleanup.py`
for `vendor.type: marvell-octeon`:

1. Stop lab-owned guest traffic only.
2. Stop DAO only if `dao.stop_on_cleanup: true` **and** this run started it.
3. Remove DAO/OVS logical ports or the bridge only when `ovs.lab_owned` (or
   this run's `prepare-ovs.json`) marks it lab-owned.
4. Restore DPDK bindings only when they are listed as lab-owned in run state.
5. Untracked bindings return `UNTRACKED_DPDK_BINDING_REFUSED`. Host
   `ovs-vsctl` is not used as the Marvell cleanup path.

Never reset devices that are not `lab_owned`.
