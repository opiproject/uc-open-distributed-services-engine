# NVIDIA BlueField control placement

For the vDSE-like **VM-to-VM** lab, NVIDIA BlueField is **not** ambiguous.

## Default: `nvidia-bluefield-dpu-mode`

| Plane | Owner |
|---|---|
| OVS / OVS-DOCA | **DPU** (BlueField Arm OS) |
| VF representors | **DPU** |
| Forwarding / offload | **DPU** |
| KVM / QEMU / libvirt | **Host** |
| VF passthrough into VMs | **Host** |
| Host OVS for lab forwarding | **Disabled** (`allow_host_ovs_for_forwarding: false`) |

Config: `config/lab-nvidia-vm2vm.example.yaml` and
`config/profiles/nvidia-bluefield-dpu-mode.yaml`.

`labctl vm2vm validate-config` fails with:

```text
Invalid NVIDIA DPU-mode configuration: this profile requires DPU-side OVS/OVS-DOCA ownership. Host OVS must not own VF representors for forwarding.
```

if `nvidia.mode`, `ovs_location`, `representor_location`, or the forwarding
control flags disagree with this table.

Host `dpu.enable_switchdev` is **false** in this profile: the x86 host is
not the representor owner. Do not run host `devlink eswitch set` as the
forwarding path.

VF-to-representor mapping is DPU-side (`representor_mapping.location: dpu`,
strategy `nvidia-portname`). See
[`dpu-side-vf-to-representor-mapping.md`](../operations/dpu-side-vf-to-representor-mapping.md).

## Advanced: `nvidia-connectx-host-switchdev`

Host-side switchdev + host OVS. **Must be selected explicitly**
(`vendor.profile: nvidia-connectx-host-switchdev`). Not the first-run
target. See `config/profiles/nvidia-connectx-host-switchdev.yaml`.

## What must not happen in DPU mode

- Host OVS attaching VF representors for lab forwarding
- Assuming OVS-DOCA on the host
- Treating Kind or fixture tests as DPU-mode certification
