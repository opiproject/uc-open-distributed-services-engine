# DPU-side VF-to-representor mapping

For **NVIDIA BlueField DPU mode**, VF representors live on the BlueField Arm
OS. Host `sysfs` `virtfnN` mapping is the wrong node: it will not see
`pf0vf0` / `pf0vf1`.

`labctl vm2vm discover-representors` therefore calls:

```text
python tools/topology-discovery/vf_to_representor_map.py \
    --config <config-file> --location dpu --json
```

Host mapping (`--location host`) is only for the advanced
`nvidia-connectx-host-switchdev` profile.

## Config

```yaml
representor_mapping:
  location: dpu
  strategy: nvidia-portname
  require_high_confidence: true
```

Defaults match `nvidia.representor_location` / the DPU-mode profile when
this block is omitted.

## What is collected (DPU, via RemoteCommandRunner)

Read-only probes, dry-run by default:

- `ip -d link show`
- `devlink port show`
- `ovs-vsctl show` (optional corroboration; never the sole mapping source)

Tests and offline runs inject samples with `--ip-link-file` /
`--devlink-file` instead of SSH.

## NVIDIA portname patterns

Parsed from `portname` / `phys_port_name` (not from a free-form netdev
guess):

| Token | Meaning |
|---|---|
| `pf0vf0` | PF0 VF representor 0 |
| `pf0vf1` | PF0 VF representor 1 |
| `pf1vf0` | PF1 VF representor 0 (not VF index 0 of PF0) |
| `pf0sf0` | subfunction — **not** a VF mapping |

`eth0v1` and names like `enp4s0f0v0` are **not** representors.

## Output fields

Each mapping row includes:

- host VF index (`vf_index`)
- host VF PCI address if known (`vf_pci` / `vf_pci_address`)
- DPU representor name (`dpu_representor_name`, `representor_netdev`)
- DPU representor portname (`dpu_representor_portname`)
- switch id (`switchid` / `phys_switch_id`)
- `confidence` / `mapping_confidence` (`high` / `medium` / `low` / `null`)
- `mapping_source` (for example `devlink_pcivf+nvidia-portname`)

High confidence requires agreeing **devlink** `flavour=pcivf` + `vfnum`
and a structured NVIDIA portname. The mapper does not guess. Ambiguous
or conflicting candidates stay `confidence=low` with
`representor_netdev: null`.

## configure-forwarding

When `require_high_confidence: true` (DPU-mode default),
`labctl vm2vm configure-forwarding` refuses to attach representors unless
the prior `discover-representors` row is `confidence=high`. Low or missing
confidence is a hard fail, not a silent fallback to host OVS.

See also [`vf-to-representor-mapping.md`](vf-to-representor-mapping.md) and
[`nvidia-bluefield-control-placement.md`](../architecture/nvidia-bluefield-control-placement.md).
