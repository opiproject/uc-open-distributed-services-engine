# VM VFIO assignment

`labctl vm2vm create-vms` and `labctl vm2vm attach-vfs` create the two
KVM guests and assign one SR-IOV VF PCI device to each. Default is
**dry-run**. `--apply` is required to run `virsh`.

If either stage's dry-run JSON shows `"iommu_vfio": {"status":
"NOT_READY", ...}`, see
[`iommu-vfio-host-setup.md`](iommu-vfio-host-setup.md) to enable IOMMU
on the kernel command line (Debian/Ubuntu/RHEL-family covered) before
proceeding — `attach-vfs` will not work without it.

## create-vms

1. Render `qemu/libvirt-xml/vm-vfio-template.xml` from `vms[]`.
2. Fill `vf_pci` from config or the prior `discover-representors` /
   `representor-map.json` result. Do not invent a PCI address.
3. If `vf_pci` is still unknown, the VF hostdev block is omitted
   (`attach-vfs` hotplugs it later).
4. Refuse duplicate VM names and duplicate VF PCI assignments.
5. Record IOMMU/VFIO readiness (`intel_iommu=on` / `amd_iommu=on`,
   `/sys/kernel/iommu_groups`, `vfio-pci`). Unknown stays `null`.
6. Write domain XML under `tests/results/<vendor>/<run-id>/xml/`.
7. Plan `virsh define <xml>` and, unless `create_vms.start: false`,
   `virsh start <name>`.
8. Wait for boot **only** if `create_vms.wait_for_boot: true`.

```yaml
create_vms:
  start: true
  wait_for_boot: false
```

Prepare disks first with `qemu/images/prepare-vm-image.sh` (see
[`vm-image-preparation.md`](vm-image-preparation.md)).

## verify-vms

`create-vms`'s captured `virsh start` result is not, by itself,
sufficient evidence the VM actually booted -- it can be stale (a
domain left over from a previous run under the same name) or race with
the real boot. Run this between `create-vms` and `attach-vfs`:

```bash
python tools/labctl.py vm2vm verify-vms --config <config> --json
```

Per VM, always executed for real (read-only; no `--dry-run`/`--apply`
distinction):

1. Fresh `virsh domstate <name>` right now -- not the possibly-stale
   result `create-vms` captured earlier.
2. If (and only if) that reports `running`, an independent SSH probe
   (`ssh -o BatchMode=yes -o ConnectTimeout=5 ... echo <token>`) against
   the VM's management NIC (`vms[].management_ip`, or `virsh
   domifaddr`), using the key already provisioned into the guest image
   (see [`vm-image-preparation.md`](vm-image-preparation.md)).

Result has one entry per VM (`running`, `ssh_reachable`, `domstate`,
`management_host`) plus a top-level `all_ready` boolean. A single
check, not a poll loop -- if a VM is still booting, just re-run it
after a few seconds. `attach-vfs --apply`'s readiness gate requires
`verify-vms.json` to exist for the same run (not that it fully
passed) before it will attach a VF.

## attach-vfs

Assigns exactly one VF per VM via `virsh attach-device` using a hostdev
snippet. Skips the VM if `vf_pci` is still unknown. The same uniqueness
check applies: one VF cannot be attached to two domains.

## configure-vm-ips

SSH to `vms[].management_ip` (or `virsh domifaddr`). Never uses the VF
`ip_cidr` as the SSH target. If the guest is not reachable:

```text
GUEST_SSH_REQUIRED
```

Result files: `create-vms.json`, `verify-vms.json`, `attach-vfs.json`,
`configure-vm-ips.json` (see [`lab-state-contract.md`](lab-state-contract.md)).
