# VF passthrough (VFIO) for DPU/NIC offload experiments

This document describes how to run a KVM/QEMU guest with an SR-IOV
**virtual function (VF) passed through via VFIO**, so the guest datapath
can participate in DPU/NIC hardware-offload experiments.

It is the host-side path in
[`docs/architecture/overview.md`](../../docs/architecture/overview.md)
§10.1 (VF passthrough). The DPU/NIC side is a **representor** of that VF
on a switchdev eswitch, attached to Open vSwitch. The guest sees a
vendor NIC; OVS on the host or DPU sees the representor, not the VF
netdev (the VF netdev is unbound from the host kernel when VFIO owns
it).

Prefer these Ansible roles for the host baseline, then follow this
document for the VM attach and validation:

| Step | Role / tool |
|---|---|
| IOMMU, KVM, libvirt, OVS packages | [`ansible/roles/host_base`](../../ansible/roles/host_base/README.md) |
| Create VFs | [`ansible/roles/sriov_setup`](../../ansible/roles/sriov_setup/README.md) |
| Eswitch / representors | [`ansible/roles/switchdev_setup`](../../ansible/roles/switchdev_setup/README.md) |
| OVS bridge + representor port | [`ansible/roles/ovs_setup`](../../ansible/roles/ovs_setup/README.md) |
| Offload observation | [`tools/offload-checker/ovs_offload_checker.py`](../../tools/offload-checker/README.md) |

Libvirt domain XML: [`qemu/libvirt-xml/vf-passthrough.xml.template`](../libvirt-xml/vf-passthrough.xml.template).

All commands below use **placeholders**. Substitute values from *your*
host; do not copy PCI addresses or interface names from this file.

---

## Placeholders

| Placeholder | Meaning |
|---|---|
| `<PF_NAME>` | PF netdev (e.g. the uplink or the PF you enabled SR-IOV on) |
| `<PF_PCI>` | PF PCI address, `0000:bb:dd.f` form |
| `<VF_INDEX>` | VF index on that PF (`0` … `N-1`) |
| `<VF_PCI>` | VF PCI address, `0000:bb:dd.f` form |
| `<VF_DOMAIN>` `<VF_BUS>` `<VF_SLOT>` `<VF_FUNCTION>` | Hex fields of `<VF_PCI>` for libvirt XML (`0000`, `bb`, `dd`, `f`) |
| `<IOMMU_GROUP>` | IOMMU group number for the VF |
| `<REPR_NAME>` | Representor netdev for that VF (host or DPU OS) |
| `<BRIDGE>` | OVS bridge name (e.g. `br-int`) |
| `<VM_NAME>` | libvirt domain name |
| `<IMAGE_PATH>` | Guest disk image |
| `<MAC>` | Guest-visible MAC for the VF (optional; some stacks require it to match OVN) |
| `<GUEST_IFACE>` | Interface name *inside the guest* after boot |
| `<PEER_IP>` | Address of the other VM or test endpoint |

---

## 1. Prerequisites

On the **compute host** (where QEMU runs):

- CPU virtualization (`vmx` or `svm`) and `/dev/kvm`.
- IOMMU enabled for passthrough. On x86 this is typically
  `intel_iommu=on iommu=pt` or `amd_iommu=on iommu=pt` on the kernel
  command line (`/proc/cmdline`). `host_base` checks this; it does not
  rewrite GRUB for you. See
  [`../../docs/operations/iommu-vfio-host-setup.md`](../../docs/operations/iommu-vfio-host-setup.md)
  for the manual GRUB edit on Debian, Ubuntu, and RHEL-family hosts.
- `vfio-pci` loadable (`modprobe vfio-pci`).
- QEMU/KVM + libvirt (`host_base_install_kvm` / `host_base_start_libvirtd`).
- SR-IOV VFs created on the intended PF (`sriov_setup`).
- **Out-of-band management** to the host. Do not use the PF you are
  turning into SR-IOV/switchdev as your only SSH path.

On the **eswitch / DPU datapath** (same OS as the host for on-path NICs;
the DPU OS for off-path DPU models):

- Switchdev (or vendor equivalent) so VF representors exist
  (`switchdev_setup`).
- Open vSwitch running (`ovs_setup`), with hardware offload enabled if
  you intend to validate TC/OVS offload.

Confirm IOMMU and KVM before binding anything:

```bash
cat /proc/cmdline
ls -l /dev/kvm
find /sys/kernel/iommu_groups -type l | head
```

If IOMMU groups are empty or `/dev/kvm` is missing, stop. VFIO
passthrough will not work.

---

## 2. Identify the VF PCI address

From the PF netdev:

```bash
readlink -f /sys/class/net/<PF_NAME>/device
# -> /sys/devices/pci.../<PF_PCI>

ls -l /sys/class/net/<PF_NAME>/device/virtfn*
readlink -f /sys/class/net/<PF_NAME>/device/virtfn<VF_INDEX>
# -> .../<VF_PCI>
```

From sysfs / `lspci`:

```bash
cat /sys/class/net/<PF_NAME>/device/sriov_numvfs
lspci -nn -s <PF_PCI>
lspci -nn -s <VF_PCI>
```

Record `<VF_PCI>` and split it for XML:

```text
<VF_PCI> = 0000:<VF_BUS>:<VF_SLOT>.<VF_FUNCTION>
```

`sriov_setup` also reports VF PCI addresses in its facts file
(`/etc/ansible/facts.d/sriov_setup.fact` when that role has been run).

Note the IOMMU group — **every other device in the same group** must be
bound to `vfio-pci` (or unused) for QEMU to attach this VF:

```bash
readlink /sys/bus/pci/devices/<VF_PCI>/iommu_group
ls -l /sys/bus/pci/devices/<VF_PCI>/iommu_group/devices
```

If the group contains the PF or unrelated functions you cannot isolate,
this VF is not a safe passthrough candidate until ACS/IOMMU topology
allows a dedicated group (hardware- and firmware-dependent).

---

## 3. Bind the VF to `vfio-pci`

You can let **libvirt** do this (`managed='yes'` in the template) or bind
manually. Manual bind is useful for debugging and for `managed='no'`.

Unbind any host netdev driver from the VF first (the VF should **not**
remain a host kernel netdev while the guest owns it):

```bash
# Current driver, if any
basename "$(readlink /sys/bus/pci/devices/<VF_PCI>/driver)"

# Optional: prevent the host from auto-binding new VFs on this PF
echo 0 > /sys/class/net/<PF_NAME>/device/sriov_drivers_autoprobe

# Unbind from the current driver (ignore errors if already unbound)
echo <VF_PCI> > /sys/bus/pci/devices/<VF_PCI>/driver/unbind
```

Bind to VFIO:

```bash
modprobe vfio-pci

echo vfio-pci > /sys/bus/pci/devices/<VF_PCI>/driver_override
echo <VF_PCI> > /sys/bus/pci/drivers/vfio-pci/bind

# Confirm
basename "$(readlink /sys/bus/pci/devices/<VF_PCI>/driver)"
# expect: vfio-pci

ls /dev/vfio/
# should include <IOMMU_GROUP>
```

`sriov_setup` can request `vf_driver: vfio-pci` and/or
`autoload_driver: false`; treat that as automation of the same steps,
not a different mechanism.

**libvirt shortcut** (if you prefer not to bind by hand):

```bash
virsh nodedev-list --cap pci
virsh nodedev-dumpxml pci_0000_<VF_BUS>_<VF_SLOT>_<VF_FUNCTION>
virsh nodedev-detach pci_0000_<VF_BUS>_<VF_SLOT>_<VF_FUNCTION>
```

`nodedev-detach` rebinds the device to `vfio-pci`. Pair with
`nodedev-reattach` after the experiment.

---

## 4. libvirt XML template

Use [`qemu/libvirt-xml/vf-passthrough.xml.template`](../libvirt-xml/vf-passthrough.xml.template).
Copy it, replace placeholders, define the domain:

```bash
cp qemu/libvirt-xml/vf-passthrough.xml.template /tmp/<VM_NAME>.xml
# edit placeholders
virsh define /tmp/<VM_NAME>.xml
virsh start <VM_NAME>
```

The NIC is modeled as `<interface type='hostdev'>` (preferred for
SR-IOV VFs: libvirt can set a MAC) rather than a bare `<hostdev>`, which
is also valid PCI passthrough but does not speak “network interface” to
libvirt.

`managed='yes'` means libvirt binds/unbinds `vfio-pci` around the guest
lifecycle. Use `managed='no'` if you bound the VF yourself and want
libvirt to leave driver assignment alone.

A QEMU CLI equivalent (for labs without libvirt) looks like:

```bash
qemu-system-x86_64 \
  -enable-kvm \
  -cpu host \
  -m <MEMORY_MIB> \
  -drive file=<IMAGE_PATH>,if=virtio \
  -device vfio-pci,host=<VF_PCI>,id=vf0
```

Adjust architecture, firmware, and machine type for aarch64/DPU-hosted
guests; this document does not assume x86_64 beyond the example CLI.

---

## 5. Verify the guest sees the VF

On the **host**:

```bash
virsh dumpxml <VM_NAME> | grep -A20 "hostdev\|interface type='hostdev'"
virsh qemu-monitor-command <VM_NAME> --hmp 'info pci'
```

Inside the **guest** (console or SSH over a *second* NIC — do not assume
the VF already has an IP):

```bash
lspci -nn
ip link
ethtool -i <GUEST_IFACE>
dmesg | tail
```

Expect a PCI NIC matching the VF’s vendor/device ID (the same IDs as
`lspci -nn -s <VF_PCI>` on the host), **not** `virtio-net`, unless you
chose the vDPA path instead (`qemu/vdpa`).

Bring the interface up only after you know which L2 domain the
representor/OVS/OVN port is on:

```bash
ip link set <GUEST_IFACE> up
ip addr add <GUEST_IP>/<PREFIX> dev <GUEST_IFACE>   # if not using DHCP/OVN
```

---

## 6. Map the VF to its representor

The representor is a **different netdev**, usually on the PF’s eswitch
(host or DPU). Naming is vendor-specific (`eth0v1`, `p0vf1`, `pf0vf1`,
…); do not guess — resolve it.

**sysfs (VF index → PCI, then match representor):**

```bash
readlink -f /sys/class/net/<PF_NAME>/device/virtfn<VF_INDEX>

# Representors often expose phys_port_name / phys_switch_id
for n in /sys/class/net/*/phys_port_name; do
  echo "$(basename "$(dirname "$n")") $(cat "$n") $(cat "$(dirname "$n")/phys_switch_id" 2>/dev/null)"
done
```

**devlink (preferred; flavour `pcivf`):**

```bash
devlink port show
# look for: pci/<PF_PCI>/<PORT_INDEX> flavour pcivf pfnum <PF_NUM> vfnum <VF_INDEX> ... netdev <REPR_NAME>
```

Match `vfnum` to `<VF_INDEX>` on the same PF. That `netdev` is
`<REPR_NAME>`.

Helper: `tools/topology-discovery/vf_to_representor_map.py` (discovery
JSON). Confirm a match with `devlink port show` before relying on it.

---

## 7. Attach the representor to OVS

Do this on the **node that owns the representor** (DPU OS in an off-path
model; the hypervisor host for an on-path smart NIC).

```bash
ovs-vsctl add-br <BRIDGE>           # if the bridge does not already exist
ovs-vsctl add-port <BRIDGE> <REPR_NAME>
ovs-vsctl show
ip link set <REPR_NAME> up
```

Or via `ansible/roles/ovs_setup`:

```yaml
ovs_enable_hw_offload: true
ovs_bridges:
  - <BRIDGE>
ovs_ports:
  - bridge: <BRIDGE>
    port: <REPR_NAME>
    interface_type: system
```

Do **not** add `<VF_PCI>`’s old host netdev to OVS: after VFIO bind that
netdev should be gone. OVS only needs the representor.

For OVN/Neutron/OVN-Kubernetes, the logical port bind is a control-plane
step on top of this attach; this lab path is enough for two VMs on the
same bridge.

---

## 8. Traffic tests

Use two guests (or one guest and a known endpoint on the same logical
switch), each with a VF whose representor is on `<BRIDGE>`.

**L2/L3 sanity (guest):**

```bash
ping -c 5 <PEER_IP>
ip neigh show
```

**Throughput (example; install iperf3 in both guests yourself):**

```bash
# listener
iperf3 -s

# sender
iperf3 -c <PEER_IP> -t 30
```

Generate enough packets that OVS can install a datapath flow (a handful
of pings is often enough to *see* a flow; iperf is better for offload
and counter movement).

Keep a **management path** that does not depend on the VF under test.

---

## 9. Validate offload

Offload is not “the guest has a VF”. The VF only delivers packets to the
eswitch. Offload is “established flows are forwarded in hardware / DPU
fast path,” which you infer from OVS/TC/ethtool markers.

On the **representor’s host** (after traffic):

```bash
python3 tools/offload-checker/ovs_offload_checker.py \
  --interface <REPR_NAME> \
  --interface <PF_NAME> \
  --bridge <BRIDGE> \
  --flow 'in_port=<OF_PORT>,eth_type=0x0800' \
  --pretty -o /tmp/vf-passthrough-offload.json
```

Also useful:

```bash
ovs-appctl dpctl/dump-flows -m
tc filter show dev <REPR_NAME> ingress
ethtool -S <PF_NAME>
ethtool -S <REPR_NAME>
```

Interpretation (see the offload-checker README):

- `offloaded:yes` / `dp:tc` on datapath flows, or TC `in_hw`, are
  **probable** hardware offload.
- First packets often stay on the slow path; re-dump after a bulk flow.
- No markers does **not** prove software-only forwarding (DPDK/vendor
  datapaths often hide them). It also does not prove success.

Compare CPU on the hypervisor/DPU while iperf runs with
`other_config:hw-offload=true` vs `false` if you need a behavioural
signal rather than a marker.

---

## 10. Known risks and limitations

- **Live migration.** Classic VFIO VF passthrough is **not** live
  migratable in the usual libvirt/QEMU setup. If you need a virtio-net
  ABI and a migration story, use vDPA (`qemu/vdpa`) instead. This is the
  UPTv2 vs VMDirectPath split in
  [`docs/architecture/vdse-to-open-equivalent-mapping.md`](../../docs/architecture/vdse-to-open-equivalent-mapping.md).
- **IOMMU groups.** A shared group can force you to pass unrelated
  devices into the guest or block attach. Do not bind the PF to VFIO
  “to make the group work” on a production host.
- **Host netdev loss.** Binding the VF to `vfio-pci` removes it from the
  host network stack. Anything using that VF as a kernel interface will
  break.
- **SR-IOV reset.** Changing `sriov_numvfs` (especially via 0) destroys
  VFs and tears down VFIO guests, representors, and OVS ports. See
  `sriov_setup` safety notes.
- **Switchdev / VF bind ordering.** Some drivers require VFs unbound
  before eswitch mode changes (`switchdev_setup` `require_vfs_unbound`).
  Re-check `<VF_PCI>` and `<REPR_NAME>` after any eswitch change.
- **Guest driver lock-in.** The guest must load the **vendor NIC
  driver**. Images without it will not pass traffic. That is expected
  for VFIO and is a reason to prefer vDPA for portable guests.
- **MAC / spoofcheck / VLAN.** PF-side VF trusted mode, spoofcheck, and
  VLAN anti-spoof are vendor sysfs/devlink knobs. If the guest cannot
  pass traffic, check those before assuming OVS is wrong.
- **Security.** A passed-through VF is a PCI device inside the guest
  trust boundary. Treat the guest as able to program that function
  within what the PF/eswitch allows. See
  [`docs/architecture/roles-and-trust-boundary.md`](../../docs/architecture/roles-and-trust-boundary.md).
- **Not a certified VMware equivalent.** This is an experimental lab
  path for ODSE, not VMDirectPath certification.

Teardown sketch:

```bash
virsh destroy <VM_NAME>          # or shutdown
virsh nodedev-reattach pci_0000_<VF_BUS>_<VF_SLOT>_<VF_FUNCTION>
# or: unbind vfio-pci, clear driver_override, let the host driver reprobe
ovs-vsctl del-port <BRIDGE> <REPR_NAME>   # only if you added it by hand
```
