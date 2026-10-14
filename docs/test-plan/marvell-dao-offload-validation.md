# Marvell DAO offload validation

Host `ovs-vsctl` is **not** Marvell DAO offload proof. Evidence lives in
`marvell_dao_evidence` from `plugins/marvell_octeon/dao_telemetry.py`
(also attached by `tools/offload-checker/ovs_offload_checker.py` when the
vendor is Marvell).

```yaml
marvell_dao_evidence:
  dao_available:        # true/false from samples or DAO probe
  dao_running:          # null unless observed
  dpdk_ports:           # [] if unknown
  representors:         # [] if unknown
  flow_offload_indicators:  # null unless a DAO/DPDK source provided them
  confidence:           # unknown | low — never offloaded:yes from this path
  warnings: []
```

Unknown counters stay `null` or empty. Indicators at most raise confidence
to `low`. Do not treat connectivity or ionic-style stats as hardware offload.

`labctl vm2vm validate-offload` on Marvell calls `collect_dao_evidence`.
