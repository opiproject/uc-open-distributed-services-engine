# nova-vnic-remote-managed

Nova configuration for `vnic_type=remote-managed` instances, where the NIC
port is managed by a DPU/IPU rather than directly by the hypervisor host.

Expected contents:

- `nova.conf` / PCI passthrough and device-spec configuration for
  remote-managed vNICs
- Scheduler/placement configuration for DPU-backed compute nodes
- Example flavor/port-binding configuration for remote-managed instances
