# Intel IPU control model

Intel IPU control is **OPI / DPU Operator / IPU plugin**, not host or DPU
OVS-DOCA.

```text
labctl intel-nf
  -> plugins.intel_ipu (OPI client, DPU Operator probe)
  -> IPU packet pipeline or network function (exact mechanism discovery-required)
```

VM-to-VM (`labctl vm2vm configure-forwarding`) is a separate path and stays
`INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED` until capability detection
proves VFs, endpoint mapping, and **either** an OPI/plugin L2 API **or** a
P4Runtime/IPDK pipeline (`plugins/intel_ipu/p4runtime.py`) — these two are
alternate control planes, not a primary path and a fallback of it; proving
P4Runtime does not require OPI/DPU Operator to exist, and vice versa. Do not
assume representors or switchdev; P4Runtime table-entry rules are only
assumed for the mock client, never for real hardware without discovery.

See [`intel-ipu-e2100.md`](../vendor-readiness/intel-ipu-e2100.md),
[`intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md), and
[`opi-alignment.md`](opi-alignment.md).
