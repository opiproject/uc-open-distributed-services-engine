# topology-discovery

Network topology discovery for ODSE: mapping VFs, representors, and
(later) host/DPU/fabric connectivity.

## `vf_to_representor_map.py`

Skeleton **discovery** tool. It walks local Linux sysfs and, where
present, `ip -d link show`, `devlink port show`, and `ethtool -i`, then
emits JSON rows that attempt to join:

| Field | Typical source | If unknown |
|---|---|---|
| `pf_netdev` | `/sys/class/net/*/device/sriov_numvfs` | omitted PF (not listed) |
| `pf_pci_address` | sysfs `device` symlink | `null` |
| `vf_index` | `virtfnN` | `null` |
| `vf_pci_address` | `virtfnN` realpath | `null` |
| `vf_netdev` | `virtfnN/net/*` | `null` (common after VFIO bind) |
| `representor_netdev` | `devlink` `flavour pcivf` + `vfnum`, else `phys_port_name` | `null` |
| `dpu_host_identity` | **not inferred by core** | always `null` unless a plugin sets it |
| `vendor_hint` | PCI vendor ID / `ethtool` driver / `lspci -nn` (fallback) | `null` |
| `lspci_description` | `lspci -nn` device string for the VF's (or PF's) PCI address | `null` |
| `no_representor_reason` | why `representor_netdev` is null (see below) | `null` when a representor was found |
| `troubleshooting_hints` | list of concrete next steps for `no_representor_reason` | `[]` |

Default mode is **discovery** (`--mode discovery`). There is no mutate /
bind / configure path.

### Conservative matching

Core logic does **not** guess representors from netdev names (`eth0v1`,
`enp…v0`, vendor prefixes). A representor is filled only when:

1. **Preferred:** `devlink port show` has `flavour pcivf`, `vfnum` equals
   the VF index, and PCI matches the PF when the handle includes a PCI
   address (`mapping_method: devlink_pcivf`, confidence `high` if a
   netdev name is present).
2. **Fallback:** `ip -d link` `phys_port_name` parses as `pf<N>vf<M>` /
   `p<N>vf<M>` / `c<C>pf<N>vf<M>` and `phys_switch_id` matches the PF
   when the PF has one (`mapping_method: phys_port_name`).

`dpu_host_identity` stays `null` on purpose. This process cannot tell a
hypervisor from an off-path DPU OS. `local_host` is where the tool ran.
A vendor plugin may set `dpu_host_identity` when it has a serial, BMC
name, or OPI inventory id.

`vendor_hint` uses the same conservative PCI/driver lists as
`tools/inventory/dpu_inventory.py` (Intel driver matches stay `low`
confidence). It is a hint, not identification. Resolution order is
sysfs PCI vendor ID (`high`) → `ethtool -i` driver (`low`/`medium`) →
`lspci -nn` vendor ID for the same PCI address (`medium`, only used
when sysfs `vendor` was unreadable) → `--vendor` override
(`user_specified`, only used when none of the above found anything).
A stronger signal is never overwritten by a weaker one.

Subfunctions (NVIDIA-style `pf0sf0` `phys_port_name`) are parsed as
`kind: "sf"` with a distinct `sf_index`, never as a VF. Core does not
emit SF rows into `mappings` — see `vendor_plugins/nvidia_sf.py` for the
extension point.

### Diagnosing a missing representor

When `representor_netdev` is `null` for a VF, `no_representor_reason`
explains *why*, and `troubleshooting_hints` gives concrete next steps.
The tool distinguishes:

- **No data source at all** — `devlink` and `ip -d link show` both
  failed (missing tool, insufficient privilege, or wrong host).
- **Data available, no vfnum match** — `devlink port show` returned
  `flavour pcivf` ports, but none for this VF's index (check
  `sriov_numvfs` and that switchdev/eswitch mode is enabled).
- **Candidate found, switch ID mismatch** — a `phys_port_name` matched
  the VF index, but its `phys_switch_id` differs from the PF's (it
  belongs to a different eswitch instance).
- **No match at all** — neither source produced anything usable; some
  vendors need a vendor-specific discovery path (see "Vendor plugins").

This is diagnostic only — the tool never guesses a representor name to
satisfy `--strict`.

### Requirements

- Python 3.7+ (stdlib only).
- Meaningful on Linux. On other OS, or without SR-IOV sysfs, `mappings`
  is an empty list and the document still validates.
- Root is not required to *run*, but `devlink` / some sysfs nodes may be
  empty without `CAP_NET_ADMIN`.

### Usage

```bash
# Discovery JSON on stdout
python3 vf_to_representor_map.py --pretty

# One PF
sudo python3 vf_to_representor_map.py --pf <PF_NAME> --pretty -o /tmp/vf-map.json

# Parsers only (no hardware)
python3 -m unittest test_vf_to_representor_map.py
```

| Option | Description |
|---|---|
| `--mode discovery` | Default (only implemented mode). |
| `--pf IFACE` | Repeatable PF netdev filter. |
| `--pf-pci PCI_ADDRESS` | Limit to the PF at this PCI address (`0000:bb:dd.f`). Combine with `--pf` to require both to match. |
| `--vendor VENDOR` | Vendor hint override, used only when PCI-ID/driver/lspci heuristics found nothing. Reported at `confidence: user_specified`. |
| `--sysfs-net PATH` | Override `/sys/class/net` (tests / chroot). |
| `-o PATH` / `--pretty` | JSON file / indent. |
| `--json` | Explicit no-op; output is always JSON. Kept for parity with other ODSE CLI tools. |
| `--strict` | Exit `3` if any discovered VF has no `representor_netdev`. |
| `--plugin-dir DIR` | Extra vendor plugins. |
| `--no-bundled-plugins` | Skip `vendor_plugins/` next to this tool. |

Exit `0` if a document was produced (including empty mappings) and,
when `--strict` was given, every VF resolved a representor. Exit `1` on
unexpected failure or write error. Exit `3` only with `--strict`, when
one or more VFs have `representor_netdev: null` — see
`no_representor_reason` / `troubleshooting_hints` on those rows.

```bash
# Only the PF at this PCI address, fail loudly if any VF is unmapped
sudo python3 vf_to_representor_map.py --pf-pci 0000:03:00.0 --strict --pretty

# Force a vendor hint when heuristics can't tell (e.g. no lspci, no driver match)
python3 vf_to_representor_map.py --vendor marvell_octeon --pretty
```

### JSON shape (abridged)

```json
{
  "schema_version": "odse-vf-representor-map/v1alpha1",
  "mode": "discovery",
  "local_host": {"hostname": "compute-1", "system": "Linux"},
  "mappings": [
    {
      "pf_netdev": "p0",
      "pf_pci_address": "0000:03:00.0",
      "vf_index": 0,
      "vf_pci_address": "0000:03:00.1",
      "vf_netdev": null,
      "representor_netdev": "p0vf0",
      "dpu_host_identity": null,
      "vendor_hint": {
        "vendor": "nvidia_mellanox_bluefield",
        "confidence": "high",
        "evidence": ["PCI vendor ID 15b3"]
      },
      "mapping_method": "devlink_pcivf",
      "mapping_confidence": "high",
      "lspci_description": "Mellanox Technologies MT43244 BlueField-3 ...",
      "no_representor_reason": null,
      "troubleshooting_hints": []
    }
  ],
  "plugins": {
    "loaded": ["nvidia_sf", "marvell_octeon", "intel_ipu", "amd_pensando"],
    "results": {"nvidia_sf": {"status": "not_implemented", "note": "..."}}
  }
}
```

### Vendor plugins

Four scaffolds ship bundled and load by default (visible under
`plugins.loaded` in every run unless `--no-bundled-plugins` is passed):
`vendor_plugins/nvidia_sf.py`, `vendor_plugins/marvell.py`,
`vendor_plugins/intel.py`, `vendor_plugins/amd.py`. Each documents the
vendor-specific gap it is meant to close (SF representor annotation,
DAO/DPDK representor lookup, OPI mapping, and Pensando discovery
respectively) and currently reports `{"status": "not_implemented"}` in
its `extra_sections()` — they are deliberate no-ops, not silent
guesses, until a concrete discovery mechanism is implemented and
tested against real hardware.

The directory is named `vendor_plugins/`, not `plugins/`, specifically
to avoid colliding with the repo-root `plugins/` package under
pytest's default test collection (see "Known limitations").

Drop a module in `vendor_plugins/` (or `--plugin-dir`):

```python
from vendor_plugins import VendorPlugin

class ExamplePlugin(VendorPlugin):
    name = "example"

    def annotate_mapping(self, mapping, context):
        # mapping['dpu_host_identity'] = {"serial": "..."}  # only if known
        return mapping

    def extra_sections(self, context):
        return {}
```

### Known limitations

- Skeleton: no LLDP/CDP, no multi-host merge, no OVN port-binding join.
- Off-path DPU: run this **on the OS that owns representors** (often the
  DPU). The hypervisor may see VFs but not representors; those fields
  stay `null`.
- `devlink -j` is tried first; plain-text parse is a key/value tokenizer.
- SF representors (`flavour pcisf`) are not mapped in core (plugin hook,
  see `vendor_plugins/nvidia_sf.py`).
- The four bundled vendor plugins are scaffolds only (`not_implemented`)
  — see `docs/operations/vf-to-representor-mapping.md` for the current
  state of vendor-specific discovery gaps.
- The plugin directory is deliberately named `vendor_plugins/`, not
  `plugins/`, to avoid a top-level module name collision with the
  repo-root `plugins/` package when both are imported in the same
  pytest process (pytest imports any `__init__.py`-bearing directory it
  walks past under its bare directory name).
- Unit tests cover parsers and matching only, not live sysfs discovery;
  `test_vf_to_representor_map.py`'s `FixtureIntegrationTests` do exercise
  the full parse chain against recorded vendor command output shared
  with `tools/inventory/dpu_inventory.py` (`tests/fixtures/<vendor>/`).

See also `qemu/vfio/vf-passthrough.md` §6,
`ansible/roles/switchdev_setup`, and
`docs/operations/vf-to-representor-mapping.md` for a deeper operations
guide with troubleshooting walkthroughs.
