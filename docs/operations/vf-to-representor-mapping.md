# VF-to-representor mapping

Status: Draft
Scope: operating `tools/topology-discovery/vf_to_representor_map.py`
— what it discovers, how to read its diagnostic output, and the
current state of vendor-specific gaps.
Companions: [`labctl-usage.md`](labctl-usage.md),
[`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md),
[`../../tools/topology-discovery/README.md`](../../tools/topology-discovery/README.md)

## Why this tool exists

Bridging SR-IOV VFs on OVS requires knowing, for each VF, which
representor netdev on the eswitch corresponds to it. That mapping is
not exposed as a single kernel API — it has to be assembled from
`sysfs` (`virtfnN`), `devlink port show` (`flavour pcivf` + `vfnum`),
and `ip -d link show` (`phys_port_name` / `phys_switch_id`) as a
fallback. This tool does that assembly **conservatively**: it never
guesses a representor name from a netdev naming convention. If it
can't confirm a mapping from structured data, the row says so and
explains why.

`labctl.py`'s `discover-representors` stage calls this tool for you
(see [`labctl-usage.md`](labctl-usage.md)); this document is for
running it directly, interpreting its output, and troubleshooting a
`null` `representor_netdev`.

For NVIDIA BlueField **DPU mode**, representors are on the Arm OS. Use
[`dpu-side-vf-to-representor-mapping.md`](dpu-side-vf-to-representor-mapping.md)
(`--location dpu`). Do not treat a host-side scan as DPU mapping.

## Quick start

```bash
# All SR-IOV-capable PFs on this host
python3 tools/topology-discovery/vf_to_representor_map.py --pretty

# One PF by PCI address, fail the run if any VF is unmapped
sudo python3 tools/topology-discovery/vf_to_representor_map.py \
    --pf-pci 0000:03:00.0 --strict --pretty -o /tmp/vf-map.json
echo "exit=$?"   # 3 means: see no_representor_reason on the offending row(s)
```

Run this **on the node that owns the eswitch** — for an off-path DPU
model that is usually the DPU OS, not the hypervisor. Run from the
wrong node and every `representor_netdev` will be `null` with
`no_representor_reason` explaining that no usable `devlink`/`ip`
output was found.

## Flags added for this lab use case

| Flag | Purpose |
|---|---|
| `--pf-pci PCI_ADDRESS` | Narrow discovery to one PF by PCI address (`0000:bb:dd.f`), in addition to or instead of `--pf IFACE`. Useful when the PF's netdev name is not yet known but its PCI address is (e.g. straight from `dpu.pf_pci` in a lab config). |
| `--vendor VENDOR` | Force a vendor hint (`nvidia_mellanox_bluefield`, `intel_ipu`, `marvell_octeon`, `amd_pensando`) when PCI-ID/driver/`lspci` heuristics found nothing. Reported at `confidence: user_specified` — it never overrides a signal the tool actually found. |
| `--json` | No-op today (output is always JSON); accepted for CLI consistency with `labctl.py` and `dpu_inventory.py`. |
| `--strict` | Exit `3` if any VF row has `representor_netdev: null`. Intended for CI/lab gating — pair with `no_representor_reason` to see why. |

`labctl.py`'s `discover-representors` stage passes `--pf-pci` from
`dpu.pf_pci` and `--vendor` from a mapping of the config's
`vendor.type` (e.g. `nvidia-bluefield` → `nvidia_mellanox_bluefield`)
whenever those config fields are set — it does not pass `--strict`,
so a lab run's own discovery stage does not fail on the first
unmapped VF; use `--strict` yourself when you want that behavior
(e.g. after `prepare-switchdev`, before trusting `attach-vfs`).

## Reading the output

Each row in `mappings` is a PF or a PF+VF pair. Relevant fields for
this use case:

| Field | Meaning |
|---|---|
| `representor_netdev` | The representor, or `null` if not confirmed. |
| `mapping_method` | `devlink_pcivf` (preferred) or `phys_port_name` (fallback), or `null`. |
| `mapping_confidence` | `high`/`medium`/`low`, or `null` when unmapped. |
| `vendor_hint` | `{vendor, confidence, evidence}` or `null`. See resolution order below. |
| `lspci_description` | The `lspci -nn` description string for this PCI address, when available — useful to sanity-check you're looking at the right card. |
| `no_representor_reason` | One-line explanation of why `representor_netdev` is `null`. `null` when a representor was found. |
| `troubleshooting_hints` | List of concrete next actions tied to `no_representor_reason`. `[]` when a representor was found. |

### `vendor_hint` resolution order

1. sysfs PCI vendor ID (`confidence: high`)
2. `ethtool -i` driver name (`confidence: low` for Intel `ice`/`idpf`,
   `medium` for others — Intel's driver list is shared across
   non-DPU NICs, so it stays low-confidence by design)
3. `lspci -nn` vendor ID for the same PCI address, **only when sysfs
   `vendor` was unreadable** (`confidence: medium`) — this exists
   because a normal user often cannot read
   `/sys/bus/pci/devices/.../vendor` for a device they don't already
   have driver access to, but `lspci -nn` still resolves the id.
4. `--vendor` override (`confidence: user_specified`) — only used when
   none of the above found anything.

A stronger signal is never replaced by a weaker one, and none of these
are ever promoted to `high` except #1.

### Diagnosing `no_representor_reason`

| `no_representor_reason` (paraphrased) | What it means | What to do |
|---|---|---|
| "no data source available: `devlink` and `ip -d link show` both returned no usable output" | Neither tool is installed, or this process lacks privilege, or the eswitch lives elsewhere (e.g. the DPU OS, not the hypervisor). | Install `iproute2` (`ip`, `devlink`); re-run with `sudo`/`CAP_NET_ADMIN`; confirm you're on the node that owns the eswitch. |
| "devlink port show returned pcivf ports, but none with vfnum=N" | `devlink` works, but no `flavour pcivf` port has this VF's index. | Check `sriov_numvfs` on the PF actually created N+1 VFs; confirm switchdev/eswitch mode is enabled (`ansible/roles/switchdev_setup`); re-run `devlink port show` by hand. |
| "found phys_port_name candidate(s) [...] but phys_switch_id did not match the PF's switch id" | A `phys_port_name` fallback candidate exists but is on a different eswitch instance than the PF. | Confirm the candidate netdev and PF share `phys_switch_id`; the candidate may belong to a different card/PF. |
| "no devlink pcivf port and no phys_port_name of the form pf\<N\>vf\<M\> ... found" | Neither discovery path produced anything for this VF, and both tools otherwise had data. | Some vendors need a discovery path this tool's core does not implement (see below). Check the vendor-specific gaps table. |

`troubleshooting_hints` on the row repeats the actionable subset of
the above in the JSON itself, so a script consuming the JSON does not
have to also parse this table.

## Subfunctions (SFs) are not VFs

NVIDIA-style `phys_port_name` values like `pf0sf0` are parsed as
`kind: "sf"` with a `sf_index`, and are never matched to a `vf_index`
by `match_representor`. Core discovery does not emit SF rows into
`mappings` at all — SFs are a distinct kernel construct (mlx5
subfunctions) from SR-IOV VFs, and this lab use case's two-VF/two-VM
topology does not use them. See `vendor_plugins/nvidia_sf.py` for the
intended extension point if a future lab scenario needs SF-aware
discovery.

## Vendor-specific discovery gaps

Core discovery (sysfs + `ip`/`devlink`/`ethtool`/`lspci`) is
vendor-neutral by design. Four bundled plugin scaffolds document where
each vendor's real gap is, and currently report
`{"status": "not_implemented"}` rather than guessing:

| Vendor | Plugin | Known gap |
|---|---|---|
| NVIDIA BlueField | `vendor_plugins/nvidia_sf.py` | SF representor annotation and `flavour pcisf` matching (VFs already work via core `devlink_pcivf`/`phys_port_name`). |
| Marvell OCTEON | `vendor_plugins/marvell.py` | Representors are typically managed by DAO/DPDK, not host-visible `devlink`/`ip`; core discovery is expected to report `no_representor_reason` here until a DAO/DPDK query path is implemented. |
| Intel IPU | `vendor_plugins/intel.py` | Representor identity typically requires an OPI/Redfish query to the IMC, mirroring the `discovery_required` posture in `plugins/intel_ipu/backend.py`'s `configure_forwarding()`. |
| AMD Pensando | `vendor_plugins/amd.py` | No confirmed host-visible representor construct yet identified for this repository; plugin is a placeholder pending that investigation. |

None of these plugins fabricate a representor name. Each one is a
documented no-op until the corresponding vendor discovery mechanism is
implemented and verified against real hardware — see the module
docstring in each file for the specific TODOs.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | A document was produced (including an empty `mappings` list), and (with `--strict`) every VF resolved a representor. |
| `1` | Unexpected failure, or `-o PATH` could not be written. |
| `3` | Only with `--strict`: one or more VFs have `representor_netdev: null`. |

## Testing without hardware

`tools/topology-discovery/test_vf_to_representor_map.py` covers every
parser (`phys_port_name` including SFs, `lspci -nn`, `devlink port
show` text/JSON, `ip -d link show`), the matching/diagnostic logic,
and the new CLI flags — all pure-function, no sysfs required. Its
`FixtureIntegrationTests` replay the same recorded vendor command
output used by `tools/inventory/dpu_inventory.py`
(`tests/fixtures/nvidia_bluefield/`, `tests/fixtures/marvell_octeon/`,
...) through the full parse chain, so changes are checked against
realistic `lspci -nn` / `ip -d link show` / `devlink port show` text,
not only hand-written strings.

```bash
python3 -m pytest tools/topology-discovery/test_vf_to_representor_map.py -v
```
