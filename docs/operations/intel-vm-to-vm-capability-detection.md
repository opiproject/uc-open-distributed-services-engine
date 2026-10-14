# Intel VM-to-VM capability detection

`check_vm_to_vm_forwarding_capability(config)` in
`plugins/intel_ipu/vm_to_vm_capability.py` asks:

1. Are VFs exposed and manageable?
2. Can VF-to-endpoint mapping be discovered?
3. Is a representor-like model exposed?
4. Is there an OPI / IPU plugin L2 API?
5. Is there a DPU Operator resource model for this use case?
6. Is a P4Runtime/IPDK pipeline configured (`plugins/intel_ipu/p4runtime.py`)?
7. Is a P4Runtime-backed OVS control plane configured
   (`plugins/intel_ipu/ovs_p4.py`, `intel.control_plane: ovs`)?

Facts 4, 6, and 7 are **alternate** control planes over the same
VFs — proving any one is sufficient, and none is a prerequisite for
another. Note fact 7 is *not* independent hardware from fact 6 (both
are P4Runtime clients of the same `infrap4d`; see
[`intel-ipu-ovs-p4.md`](../vendor-readiness/intel-ipu-ovs-p4.md)) —
only fact 4 (OPI) is genuinely independent of facts 6/7. Capability is
proven when `vfs_manageable` and `vf_endpoint_map` are true **and** at
least one of facts 4, 6, or 7 is true; `check_vm_to_vm_forwarding_capability()`
records which one (`details["control_plane"]` = `"opi"`, `"p4"`, or
`"ovs"` — `opi` wins over `p4`, which wins over `ovs`, when more than
one is proven at once).

**Don't know how to actually produce a proof for fact 6 or 7 on real
hardware** (what to install/start on the ACC, how to find real P4
table/action names or representor names)? See
[`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) — this document
only describes what the *code* checks, not how to prepare the IPU.

If capability is not proven, `configure_forwarding` returns
`success: false`, does **not** run OVS or P4Runtime, and records:

- status: `INTEL_VM_TO_VM_UNSUPPORTED_OR_DISCOVERY_REQUIRED`
- message: Intel IPU VM-to-VM VF forwarding path was not discovered. Use Intel network-function-offload path first, or provide a supported OPI/IPU plugin configuration, or a P4Runtime/IPDK pipeline configuration.
- labctl verdict: `UNSUPPORTED` (not `FAIL_SETUP`)

Configuring `opi.endpoint` **or** `p4.endpoint` (see
`config/lab-intel-vm2vm.example.yaml`) is enough to move past the
`OPI_ENDPOINT_REQUIRED` gate when nothing else has been proven yet —
neither endpoint is required on top of the other. See
[`intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md) for
what the P4 path does and does not do today (mock-only, no gRPC sent).

Use `labctl intel-nf` as the first-run path until capability samples prove
VM-to-VM.
