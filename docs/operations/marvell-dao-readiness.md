# Marvell DAO readiness

Read-only gate before any Marvell OCTEON VM-to-VM or DAO OVS-offload attempt.

Profile: `marvell-octeon-dao-ovs-offload`. Host OVS is **not** the forwarding
path. DPDK representors and DAO OVS-offload must be detected; they are never
copied from the NVIDIA BlueField backend.

```text
python tools/preflight_readiness.py --config config/lab-marvell-vm2vm.example.yaml --json
```

## Status values

| Status | Meaning |
|---|---|
| `MARVELL_READY_FOR_DRY_RUN` | OCTEON, DAO, DPDK, hugepages, and a representor source are present in samples or config |
| `MARVELL_DAO_MISSING` | DAO binary / env not found |
| `MARVELL_DPDK_MISSING` | DPDK tools not found |
| `MARVELL_HUGEPAGES_MISSING` | hugepages required but total is 0 or unknown |
| `MARVELL_REPRESENTOR_DISCOVERY_REQUIRED` | no explicit DPDK/DAO port list yet |
| `MARVELL_NOT_READY` | no OCTEON device, or `allow_host_ovs_for_forwarding: true` |

Readiness does **not** start DAO, bind `vfio-pci`, or allocate hugepages.
Unit tests inject `samples=`; live SSH is not part of this gate.

## Config

See `config/lab-marvell-vm2vm.example.yaml`: `marvell`, `dpdk`, `dao`, and
`representor_mapping.model: dpdk`.
