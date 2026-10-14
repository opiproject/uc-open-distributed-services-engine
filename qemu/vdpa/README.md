# vdpa

Reference configuration for vDPA (vhost-vDPA)-backed networking in QEMU
virtual machines.

- Procedure (what vDPA is, vs VFIO, device discovery, guest checks,
  performance, offload visibility, vendor placeholders):
  [`vhost-vdpa.md`](vhost-vdpa.md)
- libvirt domain template:
  [`../libvirt-xml/vhost-vdpa.xml.template`](../libvirt-xml/vhost-vdpa.xml.template)

Host preparation is expected to use `ansible/roles/host_base`,
`ansible/roles/sriov_setup`, `ansible/roles/switchdev_setup`, and (when
implemented) `ansible/roles/qemu_vdpa_setup`.
