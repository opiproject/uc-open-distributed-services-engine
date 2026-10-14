# Marvell DPDK representor mapping

Marvell DAO OVS-offload exposes **DPDK port representors**, not Linux netdevs
such as `eth0v1`. Mapping is discovery-only; it does not rebind devices.

```text
python tools/topology-discovery/vf_to_representor_map.py --config <config-file> --vendor marvell-octeon --location dpu --model dpdk --json
```

`confidence=high` only when a DAO/DPDK source **explicitly** names `vf_index`.
Uncertain rows stay `confidence=low`. With `representor_mapping.require_high_confidence: true`, `labctl vm2vm configure-forwarding` must not proceed on low-confidence maps.

## Output fields

`vf_index`, `vf_pci`, `dpdk_port_id`, `representor_name`, `representor_type`
(`dpdk`), `backing_device`, `mapping_source`, `confidence`, `warnings`.

Implementation: `plugins/marvell_octeon/dpdk_representor_map.py`.
Fixtures: `tests/fixtures/marvell_octeon/dao_port_list.txt`,
`dpdk_devbind_status.txt`.
