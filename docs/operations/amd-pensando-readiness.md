# AMD Pensando readiness

Discovery-first. Do not assume representors, switchdev, or OVS forwarding.
Profile: `amd-pensando-discovery-first`.

```text
python tools/preflight_readiness.py --config config/lab-amd-vm2vm.example.yaml --json
```

## Status values

| Status | Meaning |
|---|---|
| `AMD_PENSANDO_DISCOVERY_READY` | Device/ionic facts can be collected; forwarding not implied |
| `AMD_IONIC_MISSING` | ionic required and lsmod/ethtool samples show no ionic |
| `AMD_SRIOV_NOT_AVAILABLE` | `require_sriov` and no `sriov_totalvfs` |
| `AMD_REPRESENTORS_NOT_DISCOVERED` | required representors missing, or listed as related |
| `AMD_SWITCHDEV_NOT_DISCOVERED` | switchdev not observed |
| `AMD_OVS_PATH_NOT_DISCOVERED` | OVS compatibility not observed |
| `AMD_RDMA_AVAILABLE` | `ionic_rdma` detected (Ethernet VM-to-VM still unproven) |
| `AMD_NOT_READY` | no device, or `allow_assumed_forwarding: true` |

ionic statistics are **not** hardware-offload proof. VF create is dry-run
only unless `--apply` **and** PF identity is proven (still refused without
that proof).
