# qemu_vdpa_setup

Configures vDPA (vhost-vDPA) devices for use by QEMU-based virtual machines.

Expected responsibilities:

- vDPA management device discovery/creation (`vdpa` tool, `vhost_vdpa`)
- Binding vDPA devices to VFs/representors created by `sriov_setup`
- QEMU/libvirt integration for `vhost-vdpa`-backed `virtio-net` devices
- Validation against `qemu/vdpa` reference configurations
