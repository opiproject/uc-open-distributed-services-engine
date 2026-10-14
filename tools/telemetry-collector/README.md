# telemetry-collector

Vendor-neutral **snapshot** of host/DPU datapath counters for ODSE.
Not Prometheus (yet). Missing tools/counters are errors/`null`, never
fake zeros (FN-16, OPI-6).

## `dpu_telemetry_collector.py`

| JSON field | Source |
|---|---|
| `timestamp` | UTC ISO-8601 |
| `host` | hostname |
| `interfaces[]` | `/sys/class/net/<if>/statistics`, `ethtool -S`, `ip -s link` |
| `ovs_bridges` | `ovs-vsctl list bridge`, `list interface`, `ovs-appctl dpctl/show` |
| `ovs_flows` | `ovs-appctl dpctl/dump-flows -m` |
| `tc_filters` | `tc -s filter show dev <if> ingress` |
| `vendor_extension` | plugins (placeholders) |

Default: every sysfs netdev except `lo`. Restrict with `-i`.

### Usage

```bash
python3 dpu_telemetry_collector.py --pretty

sudo python3 dpu_telemetry_collector.py \
  -i <PF_NAME> -i <REPR_NAME> \
  --pretty -o /tmp/dpu-telemetry.json
```

| Option | Meaning |
|---|---|
| `-i IFACE` | Repeatable filter |
| `-o PATH` / `--pretty` | File / indent |
| `--no-more` | dump-flows without `-m` |
| `--plugin-dir DIR` | Extra plugins |
| `--no-bundled-plugins` | Skip `vendor_plugins/` |

Exit 0 if a document was written (sections may still have `error`).

### Examples (shape)

```json
{
  "schema_version": "odse-dpu-telemetry/v1alpha1",
  "timestamp": "2026-08-15T12:00:00+00:00",
  "host": "dpu-arm-1",
  "interfaces": [
    {
      "name": "eth0",
      "sysfs_statistics": {"rx_packets": 1, "tx_packets": 1},
      "ip_stats": {"rx": {"bytes": 0, "packets": 0}, "tx": {"bytes": 0, "packets": 0}},
      "ethtool_s": null,
      "ethtool_error": "..."
    }
  ],
  "ovs_bridges": {
    "bridges": [{"name": "br-int"}],
    "interfaces": [{"name": "eth0", "statistics": {}}],
    "dpctl_show": {"stdout": "...", "error": null}
  },
  "ovs_flows": {"flows": [], "error": null},
  "tc_filters": [{"interface": "eth0", "stdout": null, "error": null}],
  "vendor_extension": {
    "amd_pensando": {"implemented": false, "note": "..."},
    "nvidia_bluefield": {"implemented": false, "note": "..."},
    "intel_ipu": {"implemented": false, "note": "..."},
    "marvell_octeon": {"implemented": false, "note": "..."}
  }
}
```

Numeric values above are illustrative. Use a real run for FN-16 / Level 5.

### Vendor plugins

Bundled placeholders (always `implemented: false` until a track fills
them):

| Module | `vendor_extension` key |
|---|---|
| `vendor_plugins/amd_pensando.py` | `amd_pensando` |
| `vendor_plugins/nvidia_bluefield.py` | `nvidia_bluefield` |
| `vendor_plugins/intel_ipu.py` | `intel_ipu` |
| `vendor_plugins/marvell_octeon.py` | `marvell_octeon` |

(Renamed from `plugins/` to `vendor_plugins/` to avoid a bare-name
collision with the repo-root `plugins/` package under pytest's default
test collection.)

Core must stay free of DOCA/DAO/Redfish command lines. See
`docs/vendor-readiness/`.

### Related

- `tools/offload-checker` — offload **classification**, not a time series
- `tools/inventory` — day-0 identity
- `kubernetes/opi` `TelemetrySample` — API envelope this JSON can feed
