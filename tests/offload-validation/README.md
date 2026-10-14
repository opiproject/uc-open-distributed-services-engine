# offload-validation

Tests that validate flow offload *correctness*: that traffic is actually
being handled in hardware (or DPU firmware) as expected, and that offloaded
behavior matches software datapath behavior.

Expected contents:

- TC flower / `ovs-appctl dpctl` offload verification checks
- Conntrack offload validation
- Cross-checks against `tools/offload-checker` output
