# qemu/images

Guest disk image preparation for the [VM-to-VM single-DPU lab](../../docs/operations/lab-runbook-vm2vm-single-dpu.md).
Produces the two guest images referenced by `vms[].image` in
`config/lab-*-vm2vm.example.yaml`, consumed by
`qemu/libvirt-xml/vm-vfio-template.xml` via
`tools/labctl.py vm2vm create-vms`.

Full walkthrough: [`docs/operations/vm-image-preparation.md`](../../docs/operations/vm-image-preparation.md).

## What must be inside the guest

The VM image must be minimal and suitable for traffic testing only —
not a general-purpose guest. Required packages:

| Package | Why |
|---|---|
| `iproute2` | `ip link` / `ip addr` to bring up the VF-backed interface (`guest-network-setup.sh.template`) |
| `iputils-ping` | L2/L3 sanity traffic (`run-traffic` stage, ping) |
| `ethtool` | interface identification and driver/link inspection |
| `iperf3` | throughput traffic (`run-traffic` stage, iperf3 client/server) |
| `tcpdump` | packet capture for debugging forwarding/offload issues |
| `openssh-server` | how `labctl` reaches the guest (`configure-vm-ips`, `run-traffic`) |
| `cloud-init` | only if you use the cloud-init approach below |

## Three supported preparation approaches

See [`docs/operations/vm-image-preparation.md`](../../docs/operations/vm-image-preparation.md)
for the full comparison and step-by-step commands for each:

1. **cloud-init** (recommended for cloud/distro cloud images) —
   [`cloud-init/user-data.template`](cloud-init/user-data.template) +
   [`cloud-init/meta-data.template`](cloud-init/meta-data.template),
   built into a seed ISO by [`prepare-vm-image.sh`](prepare-vm-image.sh).
2. **virt-customize** (offline image edit, no boot required) —
   `prepare-vm-image.sh --method virt-customize`.
3. **manual** (any base image, console/SSH access, run the package
   manager yourself) — `prepare-vm-image.sh --method manual` prints the
   checklist; nothing is automated.

## Files

| File | Purpose |
|---|---|
| [`prepare-vm-image.sh`](prepare-vm-image.sh) | Builds a guest qcow2 image using one of the three approaches above. Defaults to printing the commands it would run; pass `--apply` to execute them. |
| [`validate-vm-image.sh`](validate-vm-image.sh) | SSHes into a running guest and checks that every required package/service from the table above is present. |
| [`guest-network-setup.sh.template`](guest-network-setup.sh.template) | Copy into the guest or pipe over SSH (`ssh user@host bash -s -- <ip-cidr>`) to detect the VF interface, bring it up, assign an IP, and optionally start an iperf3 server. Used by `tools/labctl.py vm2vm configure-vm-ips` / `run-traffic`. |
| [`guest-first-boot-dhcp.sh`](guest-first-boot-dhcp.sh) | Uploaded and run via `virt-customize --firstboot` (approach 2) as a best-effort DHCP enabler for the management NIC, for base images that don't already configure it. See [`vm-image-preparation.md`](../../docs/operations/vm-image-preparation.md#management-nic-gets-no-dhcp-lease). |
| [`guest-first-boot-growroot.sh`](guest-first-boot-growroot.sh) | Uploaded and run via `virt-customize --firstboot` (approach 2), **first** of the four firstboot scripts, to grow the root partition/filesystem to fill the `--size` virtual disk (`qemu-img resize` alone only grows the qcow2 container, not what's inside it). See [`vm-image-preparation.md`](../../docs/operations/vm-image-preparation.md#not-enough-disk-space-to-install-packages). |
| [`guest-first-boot-install-packages.sh`](guest-first-boot-install-packages.sh) | Uploaded and run via `virt-customize --firstboot` (approach 2), **third**, after growroot and dhcp — installs the required-packages table above (apt-get/dnf/yum/zypper/apk). Runs at firstboot, not inside the offline `--install` step, specifically because the filesystem is not grown yet during that offline session. |
| [`guest-first-boot-configure-sshd.sh`](guest-first-boot-configure-sshd.sh) | Uploaded and run via `virt-customize --firstboot` (approach 2), **fourth**/last — disables SSH password auth and enables the SSH service, once `openssh-server` actually exists on disk. |
| [`cloud-init/user-data.template`](cloud-init/user-data.template) | cloud-init user-data: package list, SSH key injection, password login disabled. |
| [`cloud-init/meta-data.template`](cloud-init/meta-data.template) | cloud-init meta-data: instance id and hostname. |

## Backing-file base images: permissions

If you build a thin qcow2 with a separate backing/base image (common
with `virt-customize`/manual), the actual `qemu-system-x86_64` process
libvirtd spawns — not root, not `virsh` — needs read access to the
whole backing chain and traverse permission on every directory in both
paths. A base image left under `/home/<user>/...` is the classic
failure (home dirs default to no traversal for other users/groups).
See "Backing-file base images: a different permission model than the
top qcow2" in
[`docs/operations/vm-image-preparation.md`](../../docs/operations/vm-image-preparation.md#backing-file-base-images-a-different-permission-model-than-the-top-qcow2)
for diagnosis and fix commands (including the SELinux case on RHEL).

## Security warning

**Never bake real credentials into a VM image or into these templates:**

- No plaintext passwords. `user-data.template` sets `ssh_pwauth: false`
  and `lock_passwd: true` — SSH key auth only.
- No private SSH keys. Only the **public** key is injected
  (`ssh_authorized_keys`); keep the matching private key on the
  operator's machine, never in the image or in git.
- No API tokens, cloud credentials, or vendor management-plane
  secrets. This guest only needs enough connectivity to be reached by
  `labctl` over its management NIC — it does not need to talk to OPI,
  Redfish, DAO, or any vendor control plane.
- Treat any image you build here as disposable lab material: rebuild
  it rather than reusing an image that may have accumulated real
  credentials or logs from a previous experiment.
- `.gitignore` already excludes real lab configs
  (`config/lab-*-vm2vm.yaml`); do the same for any built `.qcow2` /
  `.iso` artifacts — do not commit disk images to this repository.
