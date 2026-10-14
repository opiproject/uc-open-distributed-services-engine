# vfio

Reference configuration for VFIO-based PCI/SR-IOV VF passthrough into QEMU
virtual machines.

- Procedure (prerequisites, bind, representor, OVS, traffic, offload,
  risks): [`vf-passthrough.md`](vf-passthrough.md)
- libvirt domain template:
  [`../libvirt-xml/vf-passthrough.xml.template`](../libvirt-xml/vf-passthrough.xml.template)

Host preparation is expected to use `ansible/roles/host_base` (IOMMU/KVM)
and `ansible/roles/sriov_setup` (VF creation). Eswitch representors and
OVS attach are `switchdev_setup` / `ovs_setup`.
