# DPU Inventory Collector

`dpu_inventory.py` is a vendor-neutral inventory collector for Linux
hosts and DPU/IPU-capable nodes. It gathers PCI, network-interface,
`devlink`, and Open vSwitch (OVS) state from the local machine and emits a
single, normalized JSON document, along with best-effort vendor hints for
AMD Pensando, NVIDIA Mellanox BlueField, Intel IPU, and Marvell OCTEON
hardware.

It is the concrete implementation referenced by
`ansible/roles/host_base`/`ansible/roles/dpu_base` for inventory
collection, and is a stepping stone toward the vendor-neutral Inventory
API described in
[`docs/architecture/api-model.md`](../../docs/architecture/api-model.md).

## What it collects

| Section | Source command(s) | Notes |
|---|---|---|
| `pci_devices` | `lspci -nn` | Parsed into per-device slot/class/vendor/device ID records. |
| `netdevs` | `ip -d link show`, `ethtool -i <iface>`, `ethtool -S <iface>` | One entry per interface in `/sys/class/net` (excluding `lo`); `ethtool -S` is only run against interfaces heuristically identified as physical functions (PFs) — see [Known limitations](#known-limitations). |
| `devlink_devices` | `devlink dev show` | One entry per devlink handle. |
| `devlink_ports` | `devlink port show` | One entry per devlink port, with best-effort attribute parsing. |
| `ovs` | `ovs-vsctl show`, `ovs-appctl dpctl/show` | Only run if `ovs-vsctl` is found on `PATH`; raw output is preserved rather than deeply parsed. |
| `vendor_hints` | Derived from the sections above | See [Vendor hint detection](#vendor-hint-detection). |

Every section reports its own `available`/`error` state independently:
a missing or failing tool (e.g. `devlink` not installed, or `ethtool`
requiring privileges the current user does not have) never aborts the
rest of the collection run — it is recorded as an error for that section
only, and every other section still runs and reports normally.

## Requirements

- Python 3.7 or later (standard library only — no third-party
  dependencies).
- Runs on any platform, but is only meaningful on Linux, since it shells
  out to Linux-specific tools (`lspci`, `ip`, `devlink`, `ethtool`,
  `ovs-vsctl`/`ovs-appctl`). On a non-Linux host, or a Linux host missing
  these tools, the script still runs successfully and produces valid
  JSON — every section will simply report `"available": false` with an
  explanatory `error`.
- Some commands (`devlink`, in particular, and some `ethtool` operations)
  may require root privileges or `CAP_NET_ADMIN` to return full output.
  Running as root (or with `sudo`) is recommended for a complete
  inventory, but is not required for the tool to run.

The following packages provide the underlying commands on most Linux
distributions, if you want to install them ahead of time:

- `pciutils` (`lspci`)
- `iproute2` (`ip`, `devlink`)
- `ethtool`
- `openvswitch-switch` / `openvswitch` (`ovs-vsctl`, `ovs-appctl`)

## Usage

```bash
# Print JSON to stdout (compact)
python3 dpu_inventory.py

# Pretty-print JSON to stdout
python3 dpu_inventory.py --pretty

# Write pretty-printed JSON to a file instead of stdout
python3 dpu_inventory.py --output inventory.json --pretty

# Recommended: run as root for the most complete result
sudo python3 dpu_inventory.py --pretty --output /tmp/dpu-inventory.json
```

### Options

| Option | Description |
|---|---|
| `-o PATH`, `--output PATH` | Write JSON output to `PATH` instead of stdout. |
| `--pretty` | Pretty-print the JSON output with 2-space indentation. |

The tool exits `0` on success (even if individual sections failed to
collect — that is expected and reported per-section) and `1` only if
inventory collection failed in an entirely unexpected way, or if writing
the `--output` file failed.

## Output structure

The top-level JSON document always has this shape:

```json
{
  "schema_version": "odse-inventory/v1alpha1",
  "host": { "hostname": "...", "platform": "...", "system": "...", "release": "...", "machine": "..." },
  "timestamp": "2026-08-14T06:00:00+00:00",
  "pci_devices": { "source_command": "lspci -nn", "available": true, "error": null, "devices": [ ... ] },
  "netdevs": { "source_command": "ip -d link show", "available": true, "error": null, "interfaces": [ ... ] },
  "devlink_devices": { "source_command": "devlink dev show", "available": true, "error": null, "devices": [ ... ] },
  "devlink_ports": { "source_command": "devlink port show", "available": true, "error": null, "ports": [ ... ] },
  "ovs": { "installed": true, "vsctl_show": { ... }, "dpctl_show": { ... } },
  "vendor_hints": [ { "vendor": "nvidia_mellanox_bluefield", "confidence": "high", "evidence": [ "..." ] } ]
}
```

Each `netdevs.interfaces[]` entry looks like:

```json
{
  "name": "eth0",
  "ip_link": { "index": 2, "name": "eth0", "flags": ["UP", "LOWER_UP"], "mtu": 1500, "mac_address": "0c:42:a1:11:22:33", "details": ["..."] },
  "is_probable_pf": true,
  "ethtool_info": { "driver": "mlx5_core", "firmware-version": "22.36.1010", "bus-info": "0000:03:00.0" },
  "ethtool_info_error": null,
  "ethtool_stats": { "rx_bytes": 12345678, "tx_bytes": 87654321 },
  "ethtool_stats_error": null
}
```

## Vendor hint detection

`vendor_hints` reports heuristic, best-effort signals — not an
authoritative hardware identification — for four vendor tracks:
`amd_pensando`, `nvidia_mellanox_bluefield`, `intel_ipu`, and
`marvell_octeon`. Each hint carries a `confidence` level (`high`,
`medium`, or `low`) and a list of `evidence` strings explaining exactly
which signal(s) triggered it:

- **`high` confidence** comes only from a PCI vendor ID match against a
  known vendor range (e.g. `15b3` for NVIDIA/Mellanox, `1dd8` for
  Pensando Systems, `177d` for Cavium/Marvell).
- **`medium` confidence** comes from an `lspci` description string match
  (e.g. `"bluefield"`, `"pensando"`, `"octeon"`) or a known driver name
  match for AMD, NVIDIA, or Marvell.
- **`low` confidence** is used specifically for Intel IPU driver-based
  hints (`ice`, `idpf`), because Intel's PCI vendor ID (`8086`) is far too
  broad a signal on its own, and these driver names are also used by
  ordinary, non-IPU Intel NICs. Do not treat a `low`-confidence Intel IPU
  hint as confirmation of IPU hardware.

Consistent with
[`docs/vendor-readiness/vendor-capability-matrix.md`](../../docs/vendor-readiness/vendor-capability-matrix.md),
this tool intentionally avoids overclaiming vendor identification —
`vendor_hints` output should always be treated as a starting point for
manual/vendor-tool confirmation, not a final answer.

## Known limitations

This is a skeleton collector, not a finished inventory system:

- **PF detection is heuristic.** An interface is treated as a physical
  function (and therefore a candidate for `ethtool -S`) only if
  `/sys/class/net/<iface>/device/sriov_numvfs` exists. This works for
  most SR-IOV-capable drivers but is not guaranteed universal across all
  four vendor tracks — see
  `docs/vendor-readiness/vendor-capability-matrix.md`.
- **`devlink port show` parsing is a simple key/value tokenizer**, not a
  full grammar for devlink's plain-text output. Unparseable or unusual
  lines are preserved as `{"raw_line": ..., "parse_error": true}` rather
  than dropped, but downstream consumers should not assume every
  attribute is captured.
- **`ovs-vsctl show` and `ovs-appctl dpctl/show` output is not deeply
  parsed** — it is preserved as raw text under `raw` in the `ovs` section,
  since both have complex, hierarchical output that is out of scope for
  this initial collector.
- **No retries or caching.** Each run performs a fresh set of command
  invocations; there is no persistence, diffing, or historical tracking
  built into this tool. See `tools/topology-discovery/` and
  `tools/telemetry-collector/` for related, complementary tooling.
- **Not a substitute for vendor-specific tooling.** For anything
  security- or automation-critical (e.g. confirming exact DPU model,
  firmware version, or capability), cross-check against the vendor's own
  tooling and `docs/vendor-readiness/` before acting on this tool's
  output alone.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Inventory collection completed (individual sections may still report `"available": false` with an `error` — that is normal and expected, not a failure). |
| `1` | Inventory collection failed unexpectedly, or the `--output` file could not be written. |
