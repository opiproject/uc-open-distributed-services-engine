# IOMMU/VFIO host setup for VF passthrough

Status: Draft
Scope: Enabling IOMMU on the **compute host** (kernel command line) so
`vfio-pci` can bind an SR-IOV VF for VM passthrough. Covers Debian,
Ubuntu, and RHEL-family (RHEL/CentOS/Rocky/Alma/Fedora). Does not cover
BlueField/DPU Arm OS kernel configuration — this is host-side only.
Companions: [`vm-vfio-assignment.md`](vm-vfio-assignment.md),
[`../../qemu/vfio/vf-passthrough.md`](../../qemu/vfio/vf-passthrough.md),
[`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md),
[`first-run-nvidia-bluefield-dpu-mode.md`](first-run-nvidia-bluefield-dpu-mode.md),
[`../../ansible/roles/host_base/README.md`](../../ansible/roles/host_base/README.md)

## What checks this, and where you'll see it

`plugins/base/vm_vfio.py`'s `iommu_vfio_readiness()` is called by
`tools/labctl.py vm2vm create-vms` and `vm2vm attach-vfs`. It never
guesses: each fact stays `null`/`UNKNOWN` unless directly observed. It
reports three independent facts plus a rollup:

```json
"iommu_vfio": {
  "iommu_on_cmdline": false,
  "iommu_groups_present": true,
  "vfio_pci_present": true,
  "ready": false,
  "status": "NOT_READY",
  "message": "IOMMU/VFIO evidence is negative"
}
```

`status` is `NOT_READY` if **any** of the three checks below comes back
negative. This is informational in `create-vms` (which doesn't attach a
VF yet) but it is a hard functional requirement for `attach-vfs` — VFIO
passthrough will not work without it.

`iommu_groups_present` requires `/sys/kernel/iommu_groups` to exist
**and contain at least one group entry** — an existing-but-empty
directory is treated as `false`, the same as a missing directory. An
empty directory is not evidence of a working IOMMU: some kernels create
that stub even when the `intel-iommu`/`amd-iommu` driver never actually
attached a device to a group (most commonly because VT-d/AMD-Vi is
still disabled in firmware, or the DMAR/IVRS ACPI table is missing,
despite `intel_iommu=on`/`amd_iommu=on` being present on the kernel
command line). If you're on an `odse` checkout older than this note,
upgrade first — an earlier version of this check only tested directory
*existence* and could report `READY` for an empty, non-functional
`iommu_groups` directory.

The read-only Ansible equivalent is
[`ansible/roles/host_base/tasks/checks_iommu.yml`](../../ansible/roles/host_base/tasks/checks_iommu.yml)
(`host_base_check_iommu`). That role deliberately does **not** rewrite
your bootloader for you — this doc is the manual procedure it expects
you to have already done.

## 0. Prerequisite: firmware-level virtualization/IOMMU

Before touching the kernel command line, confirm the **BIOS/UEFI**
actually has this enabled — no kernel parameter can compensate for it
being off in firmware:

- Intel: **VT-d** (Virtualization Technology for Directed I/O)
- AMD: **AMD-Vi** / IOMMU (sometimes listed as "SVM Mode" +
  "IOMMU" separately)

Reboot into the firmware setup screen (varies by vendor: `Del`, `F2`,
`F10`, `F12` at POST) and enable it if it is off or "Auto" and you are
not sure. There is no portable way to flip this from Linux.

**HPE ProLiant "Workload Profile" gotcha:** HPE's BIOS/RBSU groups a
whole set of related toggles under a single "Workload Profile"
selection. Confirmed on real hardware in this lab: the
**"High Performance Computing (No Virtualization)"** profile disables
**VT-x, VT-d, and SR-IOV support together as one bundle** — you will
not find them individually greyed out, the profile silently sets all
three off. Switch to a profile that leaves virtualization on (e.g. a
"Virtualization"/"General"/custom profile — check `System
Configuration -> BIOS/Platform Configuration (RBSU) -> Workload
Profile`), then explicitly re-check `Virtualization Options` for
VT-x/VT-d/SR-IOV, save, and reboot. Re-run every check in step 4 after
this change: this is a firmware change, not a kernel one, so nothing
about a previous boot's `/proc/cmdline` or `dmesg` output tells you
anything about the state after switching profiles — you need fresh
evidence from the post-change boot.

## 1. Identify the CPU vendor

```bash
lscpu | grep -i vendor
# GenuineIntel -> use intel_iommu=on
# AuthenticAMD -> use amd_iommu=on
```

Both vendors also need `iommu=pt` (passthrough mode: only devices
actually assigned to a guest go through the IOMMU translation path;
everything else uses the normal fast path).

## 2. Add the kernel parameters (distro-specific)

Target parameters, either vendor:

```
intel_iommu=on iommu=pt      # Intel
amd_iommu=on iommu=pt        # AMD
```

### RHEL / CentOS / Rocky / AlmaLinux / Fedora (`grubby`)

`grubby` is the standard tool on this family and edits the correct
boot entry (including on UEFI) without you having to find the right
`grub.cfg` path yourself:

```bash
sudo grubby --update-kernel=ALL --args="intel_iommu=on iommu=pt"

# Verify it was written before rebooting
sudo grubby --info=ALL | grep args
```

If `grubby` is unavailable, fall back to the generic GRUB2 method
below (`grub2-mkconfig` instead of `update-grub`).

### Ubuntu (GRUB2)

```bash
sudo sed -i \
  's/^GRUB_CMDLINE_LINUX="\(.*\)"/GRUB_CMDLINE_LINUX="\1 intel_iommu=on iommu=pt"/' \
  /etc/default/grub

# Confirm the line looks right before applying
grep ^GRUB_CMDLINE_LINUX= /etc/default/grub

sudo update-grub
```

(Substitute `amd_iommu=on` for AMD.) On UEFI systems `update-grub`
still finds and regenerates the right `grub.cfg` under
`/boot/efi/EFI/ubuntu/`.

### Debian (GRUB2)

Same mechanism as Ubuntu, different config-regen command name on
older Debian releases:

```bash
sudo sed -i \
  's/^GRUB_CMDLINE_LINUX="\(.*\)"/GRUB_CMDLINE_LINUX="\1 intel_iommu=on iommu=pt"/' \
  /etc/default/grub

grep ^GRUB_CMDLINE_LINUX= /etc/default/grub

sudo update-grub || sudo grub-mkconfig -o /boot/grub/grub.cfg
```

If `vfio-pci` is built as a module (the common case) and your initramfs
is generated with module pre-loading rules, also run
`sudo update-initramfs -u` after adding `vfio-pci` to
`/etc/modules-load.d/vfio-pci.conf` (see step 4) — otherwise it's fine
to `modprobe` it after boot as this doc does.

### Generic GRUB2 fallback (any distro, no `grubby`)

```bash
sudo sed -i \
  's/^GRUB_CMDLINE_LINUX="\(.*\)"/GRUB_CMDLINE_LINUX="\1 intel_iommu=on iommu=pt"/' \
  /etc/default/grub
sudo grub2-mkconfig -o /boot/grub2/grub.cfg          # BIOS
# or, on UEFI:
sudo grub2-mkconfig -o /boot/efi/EFI/<distro>/grub.cfg
```

## 3. Reboot

```bash
sudo reboot
```

**Sequencing note:** if you also plan to run `labctl vm2vm prepare-vfs
--apply` (creating SR-IOV VFs), do the IOMMU reboot **first**.
`sriov_numvfs` writes are not persisted across reboot by `labctl.py`
itself — only `ansible/roles/sriov_setup`'s installed systemd unit
does that. If you create VFs before this reboot, they'll reset to `0`
and you'll have to redo that stage.

## 4. Verify after reboot

```bash
# 1. Kernel command line actually has the parameter
cat /proc/cmdline

# 2. IOMMU groups exist and are populated
find /sys/kernel/iommu_groups -maxdepth 1 -mindepth 1 | head

# 3. vfio-pci is loadable (load it now if not already loaded)
sudo modprobe vfio-pci
lsmod | grep vfio_pci
```

**If step 2 prints nothing** (no group directories under
`/sys/kernel/iommu_groups`), that is a real negative result, not a
false alarm — do not treat the cmdline check from step 1 as sufficient
on its own. Confirm with the kernel boot log before touching `modprobe`
further:

```bash
sudo dmesg | grep -iE 'dmar|iommu|amd-vi'
```

Look for lines like `DMAR: IOMMU enabled` / `AMD-Vi: Interrupt
remapping enabled` (good) vs. `DMAR: IOMMU not found` / `DMAR: BIOS
error: DMAR table not found` (means VT-d is disabled or unsupported —
go back to step 0 and re-check BIOS/UEFI; `intel_iommu=on` on the
cmdline cannot fix this by itself).

Also note that `modprobe vfio-pci` normally must run as root; if it
"didn't work" but printed no visible error, check its actual exit
status and the boot log right after:

```bash
sudo modprobe -v vfio-pci; echo "exit=$?"
lsmod | grep vfio
dmesg | tail -20
```

To make `vfio-pci` load automatically on every future boot instead of
relying on a manual `modprobe`:

```bash
echo vfio-pci | sudo tee /etc/modules-load.d/vfio-pci.conf
```

Then confirm `labctl` itself agrees:

```bash
python tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --dry-run --json
```

Look for `"iommu_vfio": {"status": "READY", "ready": true, ...}` in the
output.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `iommu_on_cmdline: false` after reboot | Edit didn't reach the booted entry (wrong `grub.cfg` regenerated, or a different boot entry was used) | Re-check `cat /proc/cmdline`; re-run the distro-specific regen command against the actual EFI/BIOS `grub.cfg` path in use |
| `iommu_groups_present: false` even with the cmdline set | VT-d/AMD-Vi disabled in firmware, or genuinely unsupported CPU/chipset | Re-check BIOS/UEFI setting (step 0); some consumer boards silently lack IOMMU remapping tables (no ACPI DMAR/IVRS) |
| `vfio_pci_present: false` | Module not loaded, or kernel built without `CONFIG_VFIO_PCI` | `modprobe vfio-pci`; on a custom/minimal kernel, confirm `zgrep VFIO_PCI /proc/config.gz` (or your distro's config) shows `=m`/`=y` |
| IOMMU group contains more than just your target VF | No ACS (Access Control Services) support/enabled on the PCIe topology, so multiple functions share one group | See "IOMMU groups" under "Known risks and limitations" in [`../../qemu/vfio/vf-passthrough.md`](../../qemu/vfio/vf-passthrough.md#10-known-risks-and-limitations) — do not bind the PF to `vfio-pci` just to force a group to work |
| Works, then reverts after a later reboot | Someone regenerated `grub.cfg` from a stale `/etc/default/grub`, or a kernel update reset a per-entry `grubby` edit | Re-run `grubby --update-kernel=ALL` (RHEL family) after kernel updates; re-check `/etc/default/grub` on Debian/Ubuntu |
| `sriov_numvfs` write fails with `PermissionError: [Errno 13] Permission denied` (not a `dmesg`/firmware symptom at all) | Plain Linux file permissions — `labctl` writes sysfs directly as whatever user ran it, and never calls `sudo` itself; the root-owned attribute file simply refused a non-root writer | Re-run the same `--apply` command with `sudo`; see "A fourth, non-labctl gate: OS file permissions" in [`labctl-usage.md`](labctl-usage.md). This is unrelated to VT-d/SR-IOV firmware state — fixing BIOS settings does not fix a plain `EACCES` |
| BIOS "Workload Profile" (HPE and similar vendor-bundled profiles) silently disables VT-d/SR-IOV as a set | See the HPE note under step 0 | Switch to a profile that keeps virtualization enabled, re-check the individual VT-x/VT-d/SR-IOV toggles, reboot, and re-verify step 4 from scratch |

## Next step

Once `labctl vm2vm create-vms --dry-run` reports `iommu_vfio.status:
READY`, continue with
[`vm-vfio-assignment.md`](vm-vfio-assignment.md) (`create-vms` then
`attach-vfs`).
