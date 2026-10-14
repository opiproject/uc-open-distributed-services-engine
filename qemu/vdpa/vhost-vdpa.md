# vhost-vDPA for DPU/NIC acceleration experiments

This document describes how to run a KVM/QEMU guest with a **vhost-vDPA**
backend so the guest sees a standard **virtio-net** NIC while the host
or DPU provides an accelerated datapath.

It is the host-side path in
[`docs/architecture/overview.md`](../../docs/architecture/overview.md)
§10.2. The DPU/NIC side is still a VF (or vendor equivalent) plus a
**representor** on a switchdev eswitch, attached to Open vSwitch. Unlike
VFIO passthrough, the guest never loads a vendor NIC driver.

Prefer these Ansible roles for the host baseline, then follow this
document for device attach and validation:

| Step | Role / tool |
|---|---|
| IOMMU, KVM, libvirt, OVS packages | [`ansible/roles/host_base`](../../ansible/roles/host_base/README.md) |
| Create VFs | [`ansible/roles/sriov_setup`](../../ansible/roles/sriov_setup/README.md) |
| Eswitch / representors | [`ansible/roles/switchdev_setup`](../../ansible/roles/switchdev_setup/README.md) |
| vDPA device lifecycle | [`ansible/roles/qemu_vdpa_setup`](../../ansible/roles/qemu_vdpa_setup/README.md) (stub today; this doc is the manual path) |
| OVS bridge + representor port | [`ansible/roles/ovs_setup`](../../ansible/roles/ovs_setup/README.md) |
| Offload observation | [`tools/offload-checker/ovs_offload_checker.py`](../../tools/offload-checker/README.md) |

Libvirt domain XML: [`qemu/libvirt-xml/vhost-vdpa.xml.template`](../libvirt-xml/vhost-vdpa.xml.template).

VFIO alternative: [`qemu/vfio/vf-passthrough.md`](../vfio/vf-passthrough.md).

All commands use **placeholders**. Substitute values from *your* host.
vDPA support is **not** uniform across vendors — see §10 and
[`docs/vendor-readiness/vendor-capability-matrix.md`](../../docs/vendor-readiness/vendor-capability-matrix.md).

---

## Placeholders

| Placeholder | Meaning |
|---|---|
| `<PF_NAME>` | PF netdev used to create the backing VF |
| `<PF_PCI>` | PF PCI address, `0000:bb:dd.f` form |
| `<VF_INDEX>` | VF index on that PF |
| `<VF_PCI>` | Backing VF PCI address |
| `<VDPA_MGMTDEV>` | vDPA management device name from `vdpa mgmtdev show` |
| `<VDPA_NAME>` | Name you assign when creating the vDPA net device |
| `<VHOST_VDPA_DEV>` | Character device, typically `/dev/vhost-vdpa-<N>` |
| `<REPR_NAME>` | Representor netdev for the backing VF |
| `<BRIDGE>` | OVS bridge name |
| `<VM_NAME>` | libvirt domain name |
| `<IMAGE_PATH>` | Guest disk image |
| `<MAC>` | Guest virtio-net MAC |
| `<GUEST_IFACE>` | Interface name inside the guest |
| `<PEER_IP>` | Other VM or test endpoint |

---

## 1. What vDPA is

**vDPA** (virtio Data Path Acceleration) is a Linux framework that
presents the **virtio datapath** to software while a **vendor parent
device** (NIC, DPU, or a software emulator) implements the actual rings
and forwarding.

Two layers matter in this lab:

1. **vDPA device** — created against a *management device* (usually tied
   to a VF or SF). Visible via `vdpa dev show` / `/sys/class/vdpa`.
2. **vhost-vDPA** — the `vhost_vdpa` kernel module exposes
   `/dev/vhost-vdpa-<N>` so **QEMU** can use that device as a vhost
   backend for `virtio-net-pci`. The guest driver is ordinary virtio-net.

Contrast with **virtio-vdpa**, which binds the vDPA device to the *host
kernel* virtio stack (a host netdev). This document is about **vhost-vDPA
into a VM**, not virtio-vdpa as a host interface.

In ODSE terms, vDPA is the open analog of VMware **UPTv2** (uniform,
virtio-shaped, potentially migratable passthrough). Raw VFIO VF
passthrough is the analog of **VMDirectPath**. See
[`docs/architecture/vdse-to-open-equivalent-mapping.md`](../../docs/architecture/vdse-to-open-equivalent-mapping.md).

```text
 Guest virtio-net
        |
 QEMU virtio-net-pci  +  vhost-vdpa  (/dev/vhost-vdpa-N)
        |
 Kernel vDPA device  <-- vendor parent (VF / DPU function)
        |
 Representor --> OVS/OVN --> (optional HW offload) --> uplink
```

---

## 2. When to use vDPA vs VF passthrough

| | vhost-vDPA | VFIO VF passthrough |
|---|---|---|
| Guest ABI | virtio-net (distro drivers) | Vendor NIC (needs vendor driver in the guest) |
| Live migration | Structurally possible; **not assumed working** per vendor/QEMU/libvirt version | Generally **not** migratable |
| Peak PPS / features | Often slightly behind raw VF; vendor extras (RSS, proprietary stats) may be hidden | Closest to bare metal; guest sees NIC-specific features |
| Host control | vDPA + representor; VF is consumed by the vDPA parent, not by QEMU as PCI | QEMU owns the VF PCI function via `vfio-pci` |
| Isolation | vhost-vDPA control path is in the **host kernel**; do not assume it matches VFIO isolation. See `docs/architecture/roles-and-trust-boundary.md` | IOMMU maps the VF into the guest |
| When to choose | Portable guests, future migration experiments, virtio-only images | Lowest latency, vendor features, or when vDPA is missing on that NIC |

If the vendor matrix lists vDPA as `discovery-required`, start with VFIO
passthrough for datapath experiments and treat vDPA as a separate
enablement task.

---

## 3. Host prerequisites

On the **compute host** (where QEMU runs):

- CPU virtualization and `/dev/kvm`.
- IOMMU enabled (`intel_iommu=on iommu=pt` or `amd_iommu=on iommu=pt` on
  x86). Even though the guest does not take the VF via VFIO, many vendor
  vDPA parents still require IOMMU/DMA isolation.
- QEMU built/packaged with **vhost-vdpa** support, and a libvirt new
  enough for `<interface type='vdpa'>`.
- Kernel modules (names vary by distro; load what exists):

  ```bash
  modprobe vdpa
  modprobe vhost_vdpa
  # vendor parent, if packaged separately, e.g. mlx5_vdpa — see §10
  ```

- `vdpa` CLI from **iproute2**.
- SR-IOV VFs created (`sriov_setup`) unless the vendor exposes a
  non-VF parent (SF, emulated mgmtdev). Do not assume that exception.
- Out-of-band management; do not use the experiment PF as the only SSH
  path.

```bash
ls /dev/kvm
vdpa help
ls /dev/vhost-vdpa-* 2>/dev/null || true
```

`qemu_vdpa_setup` is intended to automate module load, `vdpa dev add`,
and permissioning of `/dev/vhost-vdpa-*`. Until that role is implemented,
use the commands in §5.

---

## 4. DPU/NIC prerequisites

On the **eswitch owner** (hypervisor for on-path NICs; DPU OS for
off-path):

- Firmware/driver that actually implements a vDPA **management device**.
  Presence of SR-IOV does **not** imply vDPA.
- Switchdev (or vendor equivalent) so the backing VF has a representor
  (`switchdev_setup`), if you will attach that representor to OVS.
- Open vSwitch if you are validating bridge/OVN offload (`ovs_setup`).

Typical create/bind ordering (vendor-dependent; confirm before
automating):

1. Create VFs on the PF.
2. Put the PF in switchdev mode; confirm representors.
3. Do **not** bind the VF to `vfio-pci` (that is the other path).
4. Leave the VF bound to the vendor driver that registers the vDPA
   mgmtdev, **or** follow the vendor’s documented unbind/bind for vDPA.
5. `vdpa dev add` against that mgmtdev.
6. Attach `<REPR_NAME>` to `<BRIDGE>`.

If `vdpa mgmtdev show` is empty after VFs exist, stop. This NIC/firmware
is not presenting vDPA to Linux; see §10.

---

## 5. How to identify vDPA devices

**Management devices** (parents):

```bash
vdpa mgmtdev show
# example shape (do not copy names):
#   pci/<VF_PCI>
#   supported_classes net
```

**vDPA net devices**:

```bash
vdpa dev show
ls -l /sys/class/vdpa
```

Create one (template only; mgmtdev name is vendor-specific):

```bash
vdpa dev add name <VDPA_NAME> mgmtdev <VDPA_MGMTDEV>
vdpa dev show <VDPA_NAME>
```

**vhost-vDPA character device** (what QEMU/libvirt consume):

```bash
ls -l /dev/vhost-vdpa-*
# map name -> node, e.g.
readlink -f /sys/class/vdpa/<VDPA_NAME>/device
ls /sys/class/vdpa/<VDPA_NAME>/
```

The mapping from `<VDPA_NAME>` to `/dev/vhost-vdpa-<N>` is kernel-assigned.
Record `<VHOST_VDPA_DEV>` from `ls -l /dev/vhost-vdpa-*` after `vdpa dev
add`, and confirm QEMU can open it (group `kvm`/`qemu` or root).

Teardown:

```bash
# guest must be stopped first
vdpa dev del <VDPA_NAME>
```

---

## 6. libvirt XML template

Use [`qemu/libvirt-xml/vhost-vdpa.xml.template`](../libvirt-xml/vhost-vdpa.xml.template).
Replace placeholders and define:

```bash
cp qemu/libvirt-xml/vhost-vdpa.xml.template /tmp/<VM_NAME>.xml
# edit @VM_NAME@, @VHOST_VDPA_DEV@, @MAC@, @IMAGE_PATH@, ...
virsh define /tmp/<VM_NAME>.xml
virsh start <VM_NAME>
```

The accelerated NIC is `<interface type='vdpa'>` with
`<source dev='@VHOST_VDPA_DEV@'/>` and `<model type='virtio'/>`. That is
**not** `<interface type='hostdev'>` (VFIO).

QEMU CLI equivalent:

```bash
qemu-system-x86_64 \
  -enable-kvm \
  -cpu host \
  -m <MEMORY_MIB> \
  -drive file=<IMAGE_PATH>,if=virtio \
  -netdev type=vhost-vdpa,vhostdev=<VHOST_VDPA_DEV>,id=vdpa0 \
  -device virtio-net-pci,netdev=vdpa0,mac=<MAC>
```

One vDPA device is typically exclusive to one QEMU process. Do not attach
the same `<VHOST_VDPA_DEV>` to two domains.

CPU `migratable='on'` is set in the template as a *hint* that this path
is the migratable one. It does **not** enable or test live migration by
itself.

---

## 7. Guest verification

On the **host**:

```bash
virsh dumpxml <VM_NAME> | grep -A15 "type='vdpa'"
virsh qemu-monitor-command <VM_NAME> --hmp 'info network'
```

Inside the **guest**:

```bash
lspci -nn
ethtool -i <GUEST_IFACE>
ls /sys/bus/virtio/devices
ip link
```

Expect:

- A **virtio-net** driver (`virtio_net`), not `mlx5_core` / `ice` /
  `ionic` / vendor VF drivers.
- `ethtool -i` driver `virtio_net` (or distro equivalent).
- PCI IDs for virtio-net (`1af4:…`), not the VF’s vendor/device ID.

If the guest shows the raw VF PCI ID, you attached VFIO by mistake.

Bring the interface up on the L2 domain implied by the representor/OVS
port:

```bash
ip link set <GUEST_IFACE> up
ip addr add <GUEST_IP>/<PREFIX> dev <GUEST_IFACE>
```

Keep a second, non-vDPA NIC for SSH if DHCP is not wired yet.

---

## 8. Performance tests

Use two guests (or a guest and a known peer) whose backing representors
sit on the same `<BRIDGE>` (or OVN logical switch).

```bash
# guest A
iperf3 -s

# guest B
iperf3 -c <PEER_IP> -t 30
ping -c 20 <PEER_IP>
```

Compare, on the same host and traffic pattern:

1. virtio + software vhost (`vhost-user`/`vhost-net`) if you have a
   baseline VM.
2. This vhost-vDPA guest.
3. VFIO passthrough ([`vf-passthrough.md`](../vfio/vf-passthrough.md)).

Record throughput, guest CPU, **host** CPU, and (if available) DPU CPU.
vDPA should cut host CPU versus emulated virtio; it may or may not match
VFIO. `tests/performance/vdpa/` is the planned home for result templates;
until that exists, keep JSON from the offload checker plus iperf output
next to the libvirt XML you used.

Attach the representor the same way as the VFIO lab:

```bash
ovs-vsctl add-port <BRIDGE> <REPR_NAME>
ip link set <REPR_NAME> up
```

Resolve `<REPR_NAME>` with `devlink port show` (`flavour pcivf`, matching
`<VF_INDEX>`), not by guessing netdev names.

---

## 9. Offload visibility limitations

The guest **cannot** tell you whether the DPU/NIC offloaded the flow:

- virtio-net has no reliable `ethtool -k` / `ethtool -S` hardware-offload
  story that maps to eswitch TC offload.
- `ofproto/trace` inside the guest does not exist; tracing is on the
  **representor’s** OVS instance.

Validate on the eswitch node, after traffic:

```bash
python3 tools/offload-checker/ovs_offload_checker.py \
  --interface <REPR_NAME> \
  --interface <PF_NAME> \
  --bridge <BRIDGE> \
  --pretty -o /tmp/vhost-vdpa-offload.json
```

Additional limits specific to vDPA:

- Some vendor parents offload **inside the vDPA device** and never
  install a kernel TC flower the checker would classify as `in_hw`.
  Classification may stay `unknown` even when the fast path is hardware.
- `dpctl/dump-flows` may show little or no traffic if packets never hit
  the kernel datapath after the first exception.
- Live-migration tests are **out of scope** for this lab procedure;
  success here is “guest virtio-net passes traffic and the representor
  is in OVS,” not “UPTv2-equivalent migration.”
- Do not treat guest virtio checksum/TSO offload flags as DPU switch
  offload. Those are virtio features, not eswitch offload.

---

## 10. Vendor-specific notes (placeholders)

Ratings below match
[`docs/vendor-readiness/vendor-capability-matrix.md`](../../docs/vendor-readiness/vendor-capability-matrix.md)
(`vDPA support` row) at the time of writing. Fill in the blanks only after
lab confirmation; do not copy another vendor’s mgmtdev names.

### NVIDIA BlueField (and ConnectX, if used as the parent)

- Matrix: vDPA **likely** (`mlx5_vdpa` in-tree; less proven than
  SR-IOV/switchdev).
- Expected parent flavour to confirm in lab: `<NVIDIA_VDPA_MGMTDEV>`
  (often a `pci/<VF_PCI>` device once `mlx5_vdpa` is loaded).
- Kernel module to try: `mlx5_vdpa` (plus `mlx5_core` on the PF/VF).
- Notes to record: `<NVIDIA_VDPA_FW_VERSION>`, whether SFs vs VFs are
  required, and whether `vdpa dev add` needs the VF unbound from
  `mlx5_core`.
- Doc pointer to replace: `<NVIDIA_VDPA_VENDOR_DOC_URL>`

### Intel IPU

- Matrix: vDPA **discovery-required**.
- Confirm whether any of `<INTEL_IPU_VDPA_MGMTDEV>` appear in
  `vdpa mgmtdev show` after the Intel driver stack is loaded.
- Module / ICE / IAVF / vendor package names to record:
  `<INTEL_VDPA_KERNEL_MODULES>`
- If no mgmtdev exists, this path is blocked for that firmware; use VFIO
  or a software virtio backend instead.
- Doc pointer: `<INTEL_VDPA_VENDOR_DOC_URL>`

### AMD Pensando

- Matrix: vDPA **discovery-required** (same as most Pensando datapath
  rows). The `ionic` Ethernet driver does **not** by itself prove vDPA.
- Confirm: `<PENSANDO_VDPA_MGMTDEV>` present or absent.
- Fallback if absent: VFIO (if VFs exist) or OPI Network API experiments
  (`open-source-vdse-equivalent-plan.md` AMD track).
- Doc pointer: `<PENSANDO_VDPA_VENDOR_DOC_URL>`

### Marvell OCTEON

- Matrix: vDPA **discovery-required**.
- Confirm OCTEON SDK / VPP / vendor virtio backend vs in-kernel vDPA:
  `<MARVELL_VDPA_OR_VIRTIO_BACKEND>` (`in-kernel-vdpa` |
  `vendor-userspace` | `none`).
- If the product exposes virtio to QEMU via a **userspace** vhost-user
  socket rather than `/dev/vhost-vdpa-*`, this template does not apply;
  document the socket path as `<MARVELL_VHOST_USER_SOCKET>` and keep
  vDPA marked unavailable.
- Doc pointer: `<MARVELL_VDPA_VENDOR_DOC_URL>`

---

## Known risks (short)

- Exclusive use of `<VHOST_VDPA_DEV>`; leftover QEMU processes hold the
  fd.
- Creating/deleting vDPA devices while a guest is running will break the
  VM.
- `sriov_numvfs` changes destroy backing VFs and representors (see
  `sriov_setup` safety notes).
- Isolation of vhost-vDPA is a **host-kernel** concern, not a guest IOMMU
  map of the VF. See `docs/architecture/roles-and-trust-boundary.md`.
