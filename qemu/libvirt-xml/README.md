# libvirt-xml

Sample libvirt domain XML definitions for DPU-accelerated instances,
combining vDPA and/or VFIO device configurations with standard domain
settings (CPU pinning, hugepages, NUMA topology).

Expected contents:

- Full example domain XML files for representative VM profiles
- NUMA/CPU pinning and hugepage configuration aligned with `host_base`
  tuning

Current templates:

- [`vf-passthrough.xml.template`](vf-passthrough.xml.template) — SR-IOV VF
  via VFIO (`qemu/vfio/vf-passthrough.md`)
- [`vhost-vdpa.xml.template`](vhost-vdpa.xml.template) — virtio-net via
  vhost-vDPA (`qemu/vdpa/vhost-vdpa.md`)
- [`vm-vfio-template.xml`](vm-vfio-template.xml) — VM-to-VM single-DPU
  lab domain (SR-IOV VF via VFIO + a separate management NIC), filled
  in automatically by `tools/labctl.py vm2vm create-vms` from an
  `odse-lab-vm2vm/v1alpha1` config (`../../docs/operations/labctl-usage.md`,
  `../../docs/operations/vm-image-preparation.md`)
