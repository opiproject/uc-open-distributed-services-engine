# Lab state contract

Every `labctl.py` VM-to-VM run uses one result directory:

```text
tests/results/<vendor>/<run-id>/
```

`--run-id` reuses that directory across stages. If omitted, labctl reuses
the vendor's `.latest` pointer, or creates a UTC timestamp id.

## Required files

| File | Stage |
|---|---|
| `run-metadata.json` | created when the run starts |
| `validate-config.json` | `validate-config` |
| `discover.json` | `discover` |
| `prepare-vfs.json` | `prepare-vfs` |
| `prepare-switchdev.json` | `prepare-switchdev` |
| `representor-map.json` | `discover-representors` |
| `prepare-ovs.json` | `prepare-ovs` |
| `create-vms.json` | `create-vms` |
| `verify-vms.json` | `verify-vms` |
| `attach-vfs.json` | `attach-vfs` |
| `configure-forwarding.json` | `configure-forwarding` |
| `configure-vm-ips.json` | `configure-vm-ips` |
| `run-traffic.json` | `run-traffic` |
| `offload-validation.json` | `validate-offload` |
| `collect-evidence.json` | `collect-evidence` |
| `cleanup.json` | `cleanup` |

`prepare-host.json` is also written when that stage runs. Underscore
aliases (`discover_representors.json`, `validate_offload.json`, …) are
still written so older tools keep working. `tools/lab_state.py` reads
canonical names first, then aliases.

## Per-stage rules

Each stage:

1. Loads previous required outputs from the same run directory.
2. Fails with `STAGE_MISSING_DEPENDENCY` if a **hard** dependency is
   missing (`configure-forwarding` requires `discover-representors` /
   `representor-map.json`). It does not invent representors, PCI
   addresses, or offload claims.
3. Writes its own JSON **atomically** (temp file, then replace).
4. Includes `config_hash` (SHA-256 of the loaded config) and `timestamp`.
5. Includes `dry_run` / `apply`.

`run-metadata.json` records `run_id`, vendor, config path, config hash,
and dry-run/apply at the start of the run.

## Documented dependency graph

```text
prepare-host            → validate-config
prepare-vfs             → validate-config, discover
prepare-switchdev       → discover
discover-representors   → discover
prepare-ovs             → discover-representors
create-vms              → validate-config
verify-vms              → create-vms
attach-vfs              → create-vms, verify-vms, discover
configure-forwarding    → discover-representors, prepare-ovs
configure-vm-ips        → create-vms
run-traffic             → configure-vm-ips, configure-forwarding
validate-offload        → run-traffic
collect-evidence        → validate-offload
cleanup                 → (uses whatever exists; never invents lab-owned objects)
```

`tools/report_generator.py` reads this contract (canonical files plus
aliases) and never fills gaps with guesses.

See [`labctl-usage.md`](labctl-usage.md).
