# VM image preparation for the VM-to-VM single-DPU lab

Status: Draft
Scope: Building the two guest disk images referenced by `vms[].image`
in `config/lab-*-vm2vm.example.yaml`, before running
`tools/labctl.py vm2vm create-vms`.
Companions: [`../../qemu/images/README.md`](../../qemu/images/README.md),
[`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md),
[`labctl-usage.md`](labctl-usage.md),
[`../../qemu/vfio/vf-passthrough.md`](../../qemu/vfio/vf-passthrough.md)

## What the guest needs to do

Each of the two lab VMs only needs to: boot, bring up its VF-backed
network interface, be reachable over SSH on a **separate** management
NIC, run `ping`/`iperf3` against the other VM, and optionally capture
traffic with `tcpdump` for debugging. It does not need a desktop
environment, a container runtime, or any vendor control-plane agent —
keep the image minimal.

Required packages (see the table in
[`../../qemu/images/README.md`](../../qemu/images/README.md)):
`iproute2`, `iputils-ping`, `ethtool`, `iperf3`, `tcpdump`,
`openssh-server`, and `cloud-init` if you use approach 1 below.

## Two NICs, not one

Every VM in this lab has **two** interfaces (see
`qemu/libvirt-xml/vm-vfio-template.xml`):

1. A libvirt `default` NAT network interface — the **management NIC**
   `labctl` uses for SSH (`configure-vm-ips`, `run-traffic`,
   `validate-vm-image.sh`).
2. The SR-IOV **VF passed through via VFIO** — the interface under
   test, whose representor ends up on the DPU/host OVS bridge.

`guest-network-setup.sh.template` deliberately excludes whichever
interface currently owns the guest's default route (assumed to be the
management NIC) when auto-detecting which interface to configure,
specifically so it does not accidentally reconfigure the link your SSH
session depends on.

## Approach 1: cloud-init (recommended)

Best when your base image is a cloud/distro cloud image that already
runs cloud-init on first boot (e.g. Ubuntu/Debian/Fedora cloud images).

1. Generate (or reuse) an SSH key pair for the lab. Use the **public**
   key only from here on:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/odse-lab-vm2vm -N ""
   ```

2. Build the image and a NoCloud seed ISO:

   ```bash
   qemu/images/prepare-vm-image.sh --method cloud-init \
     --base-image /path/to/ubuntu-22.04-cloudimg.qcow2 \
     --output /var/lib/libvirt/images/odse-vm2vm-vm1.qcow2 \
     --hostname odse-vm2vm-vm1 \
     --ssh-user odse \
     --ssh-key-file ~/.ssh/odse-lab-vm2vm.pub \
     --apply
   ```

   Without `--apply` the script only prints the commands it would run
   (`cloud-localds`, `qemu-img create`) — review them first.

3. Boot the resulting `.qcow2` **once**, with the generated `*-seed.iso`
   attached as a CD-ROM, so cloud-init's NoCloud datasource can find it
   and apply `user-data` (packages, user, SSH key, hostname). Do this
   as a throwaway libvirt domain, entirely separate from the real
   `odse-vm2vm-vm1` domain `labctl vm2vm create-vms` will define later
   (that domain's XML has no CD-ROM device at all — this seed-attach
   step only ever happens here, manually, once):

   ```bash
   virt-install \
     --name odse-vm2vm-vm1-seedboot \
     --memory 2048 --vcpus 2 \
     --disk path=/var/lib/libvirt/images/odse-vm2vm-vm1.qcow2,format=qcow2,bus=virtio \
     --disk path=/var/lib/libvirt/images/odse-vm2vm-vm1-seed.iso,device=cdrom \
     --os-variant detect=on --import \
     --network network=default,model=virtio \
     --graphics none --console pty,target_type=serial
   ```

   This attaches you to the serial console — **do not try to log in
   here.** `user-data.template` sets `lock_passwd: true` and
   `ssh_pwauth: false` on purpose, so there is no password for this
   user (or root — `disable_root: true`), and SSH public-key auth does
   not work over a serial console, only over the network. If a
   `login:` prompt appears, there is no credential that gets you past
   it at this point. This console is for **watching boot/cloud-init
   log text stream by**, nothing else.

   Watch for cloud-init's own completion line (configured in
   `user-data.template`'s `final_message`):

   ```text
   ODSE vm2vm lab guest odse-vm2vm-vm1 cloud-init finished after ... seconds
   ```

   Once you see it, detach (`Ctrl+]` — this does not stop the VM) and
   switch to a **second terminal on the host**. Confirm cloud-init
   actually finished successfully the same way `labctl` itself will
   reach this guest later: over the network, using the key you already
   generated in step 1, not the console.

   ```bash
   # Wait for a DHCP lease on the libvirt "default" NAT network (retry
   # every few seconds until it prints an address — this is the
   # guest's own network coming up, independent of cloud-init):
   virsh domifaddr odse-vm2vm-vm1-seedboot

   ssh -i ~/.ssh/odse-lab-vm2vm -o StrictHostKeyChecking=accept-new \
     odse@<address-from-above> cloud-init status --wait
   ```

   SSH succeeding at all already proves the `users:` module authorized
   your key. `cloud-init status --wait` blocks until cloud-init fully
   finishes (or returns immediately if it already has) and must print
   `status: done`. If it instead prints `status: disabled` /
   `boot_status_code: disabled_by_generator`, the seed ISO was not
   found (wrong path, or the CD-ROM device didn't attach) — nothing
   else in this checklist will work until this specific check passes.
   Do not skip it and move on "to save time"; skipping it is exactly
   what silently breaks SSH/DHCP many steps later with no obvious error
   (see "Cloud-init never ran" below).

   **If SSH never comes up at all** — no address ever appears from
   `virsh domifaddr`, or it does but the SSH connection is refused or
   times out — go back to *reading* the console (`virsh console
   odse-vm2vm-vm1-seedboot`; still do not attempt to log in) for boot
   errors; see "Cloud-init never ran (silently)" and "Management NIC
   gets no DHCP lease" below for the common causes. If the console
   itself is completely blank or `virsh console` just hangs with no
   output at all, the base image's kernel likely never enabled a
   serial console (missing `console=ttyS0` on its boot command line) —
   official cloud images normally set this by default; a hand-built or
   heavily customized base image might not, and that is a base-image
   problem this checklist cannot fix from the host side.

   Once `status: done` is confirmed over SSH, shut down and remove this
   **temporary** domain — the seed ISO's job is done, and everything
   it did is now permanently written to the qcow2 file itself, so this
   cleanup only removes the throwaway libvirt definition, never the
   disk:

   ```bash
   virsh shutdown odse-vm2vm-vm1-seedboot
   virsh domstate odse-vm2vm-vm1-seedboot   # wait until: shut off
   virsh undefine odse-vm2vm-vm1-seedboot   # removes the definition only, not the qcow2
   ```

4. Repeat for the second VM with a different `--hostname` and
   `--output`.

Cloud-init disabling itself again on later boots (once the real
`odse-vm2vm-vm1` domain — with no CD-ROM — starts, via `labctl vm2vm
create-vms`) is expected and harmless: `cloud-init-generator` detects
the NoCloud CD-ROM is now gone and skips cloud-init for that boot, but
everything it already wrote (user, SSH key, hostname, network config)
is regular persisted filesystem state and does not depend on cloud-init
running again.

### Cloud-init never ran (silently)

If you skip step 3 above, boot the real domain directly, and only
discover later that SSH/`virsh domifaddr` don't work: **you have no
existing login to check this with** (no user was created, no SSH key
was injected — that is exactly the symptom, and exactly why you cannot
just "check `cloud-init status --long` over the console" the way you
might expect; there is nothing to log in as). Diagnose and fix in this
order:

1. First rule out a false alarm: retry the SSH check from step 3 a few
   more times (`virsh domifaddr <domain>` for an address, then `ssh -i
   ~/.ssh/odse-lab-vm2vm odse@<address> cloud-init status --wait`) —
   DHCP leases and cloud-init both take real wall-clock time on a slow
   host. A **connection refused**, or `Permission denied (publickey)`
   with no such user, means this is real, not just slow.
2. **Preferred fix**: repeat step 3 properly on this same qcow2 (it is
   not destructive — cloud-init only *adds* state) with the seed ISO
   correctly attached, then re-verify the SSH-based `cloud-init status
   --wait` check from step 3 prints `status: done` before moving on.
3. **If you can't get the seed ISO to attach**, or the image is
   already in a state you don't want to disturb, you need a way *in*
   before you can check or fix anything else — you cannot use the
   console for this yet, because there is still no password. Get one,
   offline, the same way `virt-customize --ssh-inject` avoids needing
   one at all: set a **temporary** root password directly on the qcow2
   (works even though the VM has never successfully booted, because it
   edits the disk offline, not through a login):

   ```bash
   virt-customize -a /var/lib/libvirt/images/odse-vm2vm-vm1.qcow2 \
     --root-password password:<temporary-throwaway-password>
   ```

   Boot the VM, log in at the console as `root` with that password
   this time (this now genuinely works, unlike step 3's console),
   and run `cloud-init status --long` there to confirm the diagnosis.
   Then either patch this session live — create the `ssh_user`
   account, `mkdir -p ~/.ssh && chmod 700 ~/.ssh`, put your **public**
   key in `~/.ssh/authorized_keys` (`chmod 600`), `chown` it to that
   user, `systemctl enable --now ssh` — or just fix whatever blocked
   the seed ISO and redo step 3 properly instead (preferred, so the
   image matches what `prepare-vm-image.sh` actually produces).
   **Re-lock root when done** (`passwd -l root`) — do not leave a
   working root password on an image you intend to keep using.

`prepare-vm-image.sh` renders
[`cloud-init/user-data.template`](../../qemu/images/cloud-init/user-data.template)
and
[`cloud-init/meta-data.template`](../../qemu/images/cloud-init/meta-data.template),
substituting `<HOSTNAME>`, `<SSH_USER>`, `<SSH_PUBLIC_KEY>`, and
`<INSTANCE_ID>`. It also copies
[`guest-network-setup.sh.template`](../../qemu/images/guest-network-setup.sh.template)
onto the seed image.

## Approach 2: virt-customize

Best when you have libguestfs tools available and want to edit a base
image **offline** (no boot cycle needed), including non-cloud-init
images.

```bash
qemu/images/prepare-vm-image.sh --method virt-customize \
  --base-image /path/to/base.qcow2 \
  --output /var/lib/libvirt/images/odse-vm2vm-vm2.qcow2 \
  --ssh-user odse \
  --ssh-key-file ~/.ssh/odse-lab-vm2vm.pub \
  --hostname odse-vm2vm-vm2 \
  --apply
```

This installs the required packages, creates the lab user with a
**locked** password (SSH key auth only), injects the public key,
disables SSH password authentication, and uploads
`guest-network-setup.sh.template` to
`/usr/local/bin/guest-network-setup.sh` — all via `virt-customize`
without ever booting the image.

## Approach 3: manual fallback

For any base image where neither of the above fits (custom ISO,
appliance image, no libguestfs, etc.):

```bash
qemu/images/prepare-vm-image.sh --method manual
```

This prints a checklist (create user, install packages, authorize your
public key, disable password auth, copy the network helper script) —
nothing is automated. Follow it over console or an existing SSH path,
then shut the guest down and note the disk path for `vms[].image`.

## Validating an image

After first boot (any approach), confirm the guest actually has
everything the lab workflow expects:

```bash
qemu/images/validate-vm-image.sh \
  --host <management-ip> --user odse --identity-file ~/.ssh/odse-lab-vm2vm
```

This only runs read-only `command -v` / `systemctl is-active` checks
over SSH; it does not modify the guest. A non-zero exit means at least
one required package/service is missing — fix it before wiring the
image into `vms[].image`.

## Assigning the VF IP and testing traffic

Once the VM is running with its VF attached (`labctl vm2vm attach-vfs`),
`tools/labctl.py vm2vm configure-vm-ips` pipes
`guest-network-setup.sh.template` over SSH:

```bash
ssh odse@<management-ip> bash -s -- <vms[].ip_cidr>
```

The script auto-detects the non-management interface, brings it up,
assigns the CIDR, and prints link/address state. Pass a second
argument to name the interface explicitly instead of auto-detecting,
and `--iperf3-server` as a final argument to also start a one-shot
`iperf3 -s -1` in the background — this is what
`tools/labctl.py vm2vm run-traffic` relies on for the receiving VM.

## Management NIC gets no DHCP lease

Symptom: `virsh domifaddr <vm>` prints only the header row (no address),
`labctl vm2vm verify-vms`/`configure-vm-ips` fail with
`GUEST_SSH_REQUIRED`, and `vms[].management_ip` is unset (relying on
DHCP).

**If you used approach 1 (cloud-init)**, a missing DHCP lease is
frequently a *symptom* of "cloud-init never ran" (see "Cloud-init
never ran (silently)" above), not a networking problem on its own —
and if it never ran, there is also no console login to check it with,
for the same reason. Follow that section's diagnosis order (SSH first,
`virt-customize --root-password` only if SSH genuinely never comes
up), fix that, then re-check `virsh domifaddr`.

The rest of this section is mainly for **approach 2 (virt-customize)**
and **approach 3 (manual)**. This is a **guest-image** problem, not a
`labctl`/libvirt one —
`virsh domstate` correctly shows `running`, and the domain XML's
management interface (`<source network='default'/>`) is fine.

Root cause: approach 2 (`virt-customize`) edits the disk **offline** and
never boots the image, so whatever network configuration the *base*
image already ships with (or doesn't) is exactly what the guest gets on
first real boot. Cloud/distro cloud images configure DHCP on every
interface out of the box; a generic/minimal/ISO-installed base image
often does not, so the management NIC comes up with link but no
address.

Items 3 and 4 below assume you can actually log in at the console.
Approach 2 also locks the lab user's password (`passwd -l`), same as
approach 1 — if the *base image itself* never had a working root/other
login set up independently of `prepare-vm-image.sh`, you have the same
"no credential" problem as cloud-init's console; use the same
`virt-customize --root-password` trick from "Cloud-init never ran
(silently)" above to get a temporary one before trying either step.
Approach 3 (manual) does not have this problem — it requires existing
console/SSH access as a prerequisite.

Fixes, in order of preference:

1. **Rebuild with a cloud image as the base**, if one is available for
   your distro — this is what approach 1 (cloud-init) is designed for,
   and cloud images virtually always DHCP every interface by default.
2. **Re-run `prepare-vm-image.sh --method virt-customize`** on a build
   from after this doc was updated — it now uploads and runs
   [`guest-first-boot-dhcp.sh`](../../qemu/images/guest-first-boot-dhcp.sh)
   via virt-customize's `--firstboot` (executes once, as root, on the
   guest's *actual* first real boot — unlike anything run during the
   offline virt-customize chroot itself). It tries `systemd-networkd`,
   `netplan`, `ifupdown`, and `NetworkManager` in turn, writing only
   `99-odse-*`-prefixed config so it never overwrites a distro-owned
   file, and finally falls back to running `dhclient`/`dhcpcd` directly.
   This is a best-effort safety net, not a substitute for a base image
   that already manages its own networking.
3. **Already booted the guest once without it?** Run the same script by
   hand over console (no network needed, since it operates locally) or
   copy/run it over SSH if some other path to the guest already exists:
   ```bash
   sh qemu/images/guest-first-boot-dhcp.sh
   ```
   It is idempotent — safe to run more than once.
4. **Still nothing?** Open the guest console (`virsh console <vm>`) and
   check what the guest itself sees:
   ```bash
   ip link                                  # is the interface even up?
   ip addr show <iface>                     # any address at all?
   journalctl -u systemd-networkd --no-pager       # or: journalctl -u NetworkManager
   dhclient -v <iface>                      # run it in the foreground, watch for errors
   ```
   A link that's `DOWN` (not just missing an address) usually means the
   `virtio_net` driver isn't loaded in the guest kernel — an unusual,
   very minimal/custom kernel build, not something `labctl` or this
   image-prep tooling can fix from the host side.

## Not enough disk space to install packages

Symptom: `apt-get install ...` (or the equivalent for your distro) runs
out of space, even though `--size` (default `10G`) sounds like plenty.

Root cause: `--size` grows the **virtual disk** (the qcow2 container),
not what's *inside* it. The base image's own partition table and
filesystem are built at whatever size the image publisher chose (often
2-3G for a minimal cloud image) and are not automatically grown just
because the qcow2 file around them got bigger — something has to grow
the partition and then the filesystem to actually use the new space,
**before** anything tries to write a meaningful amount of new data
(like installed packages) into that filesystem.

- **Approach 1 (cloud-init)**: this is normally automatic —
  `growpart`/`resize_rootfs` are cloud-init modules that grow the root
  partition/filesystem on first boot, and are enabled by default in
  virtually every official cloud image's own `/etc/cloud/cloud.cfg`.
  This doc's `user-data.template` now sets them explicitly too, so it
  no longer depends on that default. Cloud-init's own module ordering
  already runs `growpart`/`resizefs` (init stage) before
  `package_update_upgrade_install` (config stage), so packages are
  installed after the filesystem has room. **But** this only runs if
  cloud-init itself actually ran — if you hit this on an
  already-provisioned guest, check with `ssh ... cloud-init status
  --long` (not over the console — see "Cloud-init never ran
  (silently)" above for why); a disk-space problem and a "cloud-init
  never ran" problem often show up together because they share the
  same root cause.
- **Approach 2 (virt-customize)**: `prepare-vm-image.sh` runs
  `qemu-img resize <output> <size>` to grow the virtual disk. Growing
  the guest's partition and filesystem, installing packages, and
  configuring sshd are then **all deferred to the guest's real first
  boot**, via `--firstboot`, in this order:
  1. [`guest-first-boot-growroot.sh`](../../qemu/images/guest-first-boot-growroot.sh)
     — grows the partition + filesystem to fill `--size`. Deliberately
     *not* attempted inside the offline virt-customize chroot itself —
     that appliance's device naming does not reliably match the real
     guest's, and getting a partition resize wrong on the wrong device
     name risks corrupting the disk.
  2. [`guest-first-boot-dhcp.sh`](../../qemu/images/guest-first-boot-dhcp.sh)
     — best-effort DHCP so the next step can reach a package mirror.
  3. [`guest-first-boot-install-packages.sh`](../../qemu/images/guest-first-boot-install-packages.sh)
     — installs the required packages (apt-get/dnf/yum/zypper/apk).
     **This step used to run offline, via virt-customize's own
     `--install`, before the filesystem was grown — that was the actual
     bug this section used to describe as already fixed when it was
     not: installing packages during the offline session edits a
     filesystem that is still the base image's original, often much
     smaller, size, so `apt-get install` (etc.) could fail with "no
     space left on device" regardless of how large `--size` was set.**
     It now only runs after step 1 has actually grown the filesystem.
  4. [`guest-first-boot-configure-sshd.sh`](../../qemu/images/guest-first-boot-configure-sshd.sh)
     — disables SSH password auth and enables the service, now that
     `openssh-server`/`openssh` is actually installed (it wasn't yet,
     when this ran offline).

  If you built an image with an **older** version of
  `prepare-vm-image.sh` (one that used offline `--install`) and hit
  this, rebuild the image rather than trying to patch it — the offline
  `apt-get`/`dnf` transaction may have partially failed in ways that
  are hard to detect after the fact (some packages present, others
  silently missing). `validate-vm-image.sh` will tell you which
  packages are actually missing on an already-built image.

**Already have a guest you can log into (console or SSH) that's stuck
at its original size?** Run the same steps by hand — safe to run more
than once:

```bash
sh qemu/images/guest-first-boot-growroot.sh
df -h /   # confirm
```

Or manually, if you'd rather see each step:

```bash
findmnt /                                # confirm the root device, e.g. /dev/vda1
growpart /dev/vda 1                      # substitute the real disk+partition number
resize2fs /dev/vda1                      # ext2/3/4; use xfs_growfs / for xfs
df -h /
```

If `growpart` isn't installed and you can't `apt-get install
cloud-guest-utils` because there's no space even for that, use `parted`
instead (near-universally preinstalled):

```bash
parted -s /dev/vda resizepart 1 100%
resize2fs /dev/vda1
```

As an immediate, purely stopgap measure to free a little space right
now without resizing anything: `apt-get clean` and
`journalctl --vacuum-size=50M`.

## `systemctl enable sshd` fails: "Refusing to operate on alias name"

Symptom:

```text
Failed to enable unit: Refusing to operate on alias name or linked unit file: sshd.service
```

Not a real failure in most cases. On Debian/Ubuntu, `ssh.service` is
the actual unit and `sshd.service` is a compatibility symlink alias to
it (some other distros have it the other way around). Newer `systemd`
refuses `enable`/`disable` **by the alias name** specifically — `start`,
`stop`, `restart`, and `status` all still work fine via either name;
only persistent enable/disable state is ambiguous for an alias, which
is what systemd is actually complaining about.

Check whether SSH is already fine regardless of this message:

```bash
systemctl is-active ssh.service 2>/dev/null || systemctl is-active sshd.service 2>/dev/null
systemctl is-enabled ssh.service 2>/dev/null || systemctl is-enabled sshd.service 2>/dev/null
```

If either prints `active`/`enabled`, you're done — ignore the error. If
neither does (e.g. `openssh-server`'s install did not fully complete,
often because of the disk-space issue above), enable the **real** unit
name directly instead of the alias:

```bash
systemctl list-unit-files 'ssh*.service' 'sshd*.service'   # which one is NOT "alias"?
systemctl enable --now ssh.service       # or sshd.service, whichever the above shows as real
```

`prepare-vm-image.sh`'s `virt-customize` method and
`cloud-init/user-data.template` now check `is-enabled` on both names
before ever attempting `enable`, specifically to avoid surfacing this
message in the common case where `openssh-server` already ships
preinstalled and pre-enabled.

## `sudo: a terminal is required to read the password`

Symptom, from `configure-vm-ips --apply` or `run-traffic --apply`:

```text
sudo: a terminal is required to read the password; either use the -S option to read
from standard input or configure an askpass helper
```

`labctl` invokes `guest-network-setup.sh` by piping it over
`ssh ... bash -s -- <args>` — a non-interactive SSH session with no
controlling terminal (no `-t`). If sudo actually needs to prompt for a
password there, it has no way to do so and fails with this message
instead of hanging forever. Two independent things need to both be true
for the guest's `ssh_user` to sudo successfully in that situation:

1. **NOPASSWD sudo is actually granted** for that exact user (not just
   membership in the `sudo`/`wheel` group, which normally still prompts
   for a password on its own).
2. **Nothing forces a tty regardless of NOPASSWD.** Some base images
   (RHEL/CentOS/Rocky/Alma family especially) ship a global
   `Defaults requiretty` in `/etc/sudoers`, which can force every sudo
   invocation to demand a controlling terminal even when NOPASSWD would
   otherwise apply.

Both `cloud-init/user-data.template` and `prepare-vm-image.sh`'s
`virt-customize` method now write a scoped
`/etc/sudoers.d/90-<ssh_user>-no-tty` drop-in covering both of these for
newly-built images. If you're hitting this on a guest built **before**
that fix (or hand-built via the manual/approach-3 checklist), fix the
already-running guest directly. You need a way in as root once (an
existing console session, or another account that can already sudo);
`lock_passwd: true` means the affected user itself has no password to
fall back on:

```bash
cat <<'EOF' > /etc/sudoers.d/90-<ssh_user>-no-tty
<ssh_user> ALL=(ALL) NOPASSWD:ALL
Defaults:<ssh_user> !requiretty
EOF
chmod 0440 /etc/sudoers.d/90-<ssh_user>-no-tty
visudo -c   # validates every file in /etc/sudoers.d, not just the main file
```

Then confirm from the orchestrating host (should print nothing and
exit 0, with no password prompt):

```bash
ssh <ssh_user>@<mgmt-ip> sudo -n true && echo OK
```

If that still fails, `sudo -n` will now tell you plainly whether it's a
missing NOPASSWD grant ("sudo: a password is required") rather than the
more confusing tty message — `guest-network-setup.sh.template`'s own
`sudo` calls were changed to `sudo -n` for exactly this reason.

## `verify-vms`/`configure-vm-ips`/`run-traffic` fail with "Permission denied (publickey)"

Symptom: `ssh -i <path> <ssh_user>@<vm-ip>` works fine by hand, but
`labctl vm2vm verify-vms` (or `configure-vm-ips`/`run-traffic`) reports
the guest unreachable, with a message like "SSH probe failed ...  or
the SSH key was not actually authorized".

Root cause: unless you set `vms[].ssh_key_path` in your lab config,
every ssh call labctl makes to a guest VM uses ssh's own **default**
identity resolution (ssh-agent, `~/.ssh/config` `IdentityFile`
directives, or `~/.ssh/id_rsa`/`id_ed25519` if present) — never `-i` —
so if the key you actually authorized into the guest image isn't one of
those defaults (e.g. a dedicated per-lab key you pass with `-i`
explicitly when testing by hand), `labctl` and manual `ssh` resolve to
two *different* keys and only one of them is authorized.

Fix: set `vms[].ssh_key_path` to the exact private-key path that works
when you `ssh -i <path>` by hand, for **each** VM in your lab config
(see `config/lab-vm2vm-single-dpu.schema.yaml` and any
`config/lab-*-vm2vm.example.yaml`):

```yaml
vms:
  - name: odse-vm2vm-vm1
    ...
    ssh_key_path: /home/lab/.ssh/odse_vm2vm_id_ed25519
  - name: odse-vm2vm-vm2
    ...
    ssh_key_path: /home/lab/.ssh/odse_vm2vm_id_ed25519
```

This is picked up by `verify-vms`, `configure-vm-ips`, and
`run-traffic` alike (all guest-VM ssh calls share the same helper); it
is independent of `dpu.ssh_key_path`, which is the separate key used
for `remote_execution` calls to the DPU/IPU management OS, not the
guest VMs.

If `ssh_key_path` is already set correctly and this still fails, the
`ssh_command`/`ssh` result captured in `verify-vms`' JSON output
(`vms[].ssh_command.stderr`) has the actual client-side error — compare
it against what you see running the identical `ssh -i <path> -o
BatchMode=yes <ssh_user>@<host> echo ok` by hand.

## Backing-file base images: a different permission model than the top qcow2

If `vms[].image` is a thin qcow2 created with a `-b`/`--backing-file`
pointing at a separate base image (common with `virt-customize`/manual
approaches to avoid duplicating a large base image per VM), be aware
this is **not `labctl`'s or `virsh`'s permission model** — it is
whatever OS user the actual `qemu-system-x86_64` process runs as, which
is normally **not root**, even though `virsh define`/`virsh start`
themselves may have run fine (as `libvirtd`, over its socket, or via
`sudo`).

Find the real runtime user/group libvirtd will drop the QEMU process
to:

```bash
grep -E '^\s*(user|group)\s*=' /etc/libvirt/qemu.conf
```

If both are commented out, the distro default applies: `qemu:qemu` on
RHEL/CentOS/Rocky/Alma/Fedora, `libvirt-qemu:kvm` on Debian/Ubuntu.
That user needs **read access to every file in the backing chain**
(top qcow2 and base image) and **execute ("traverse") permission on
every directory component of both paths** — not just the file itself.

Diagnose exactly where the chain breaks:

```bash
sudo namei -l /path/to/base-image.qcow2
sudo -u qemu test -r /path/to/base-image.qcow2 && echo OK || echo DENIED   # substitute libvirt-qemu on Debian/Ubuntu
```

The most common cause in a lab setup: the base image sits under an
operator's home directory (`/home/<user>/...`), and home directories
default to `0700`/`0750` — no `qemu`/`libvirt-qemu` traversal at all,
regardless of the image file's own permissions.

**Preferred fix** — move (or copy/hardlink) the base image into an
existing libvirt storage pool directory, which already has correct
ownership for this:

```bash
sudo mkdir -p /var/lib/libvirt/images/base
sudo mv /path/to/base-image.qcow2 /var/lib/libvirt/images/base/
# then re-point the top qcow2's recorded backing file, or rebuild it:
sudo qemu-img rebase -u -b /var/lib/libvirt/images/base/base-image.qcow2 /var/lib/libvirt/images/odse-vm2vm-vm1.qcow2
```

**If it must stay where it is**, grant the minimum needed instead of
opening the whole home directory to everyone — add the qemu group as a
secondary group on the path and only set the group execute bit:

```bash
sudo chgrp kvm /home/<user> /home/<user>/<subdir...>            # every directory down to the file; group name is libvirt-qemu's group (kvm on Debian/Ubuntu) or qemu on RHEL family
sudo chmod g+x /home/<user> /home/<user>/<subdir...>
sudo chgrp kvm /path/to/base-image.qcow2
sudo chmod 640 /path/to/base-image.qcow2   # base image is a read-only backing layer; 640 is enough
```

**RHEL/CentOS/Fedora/Rocky/Alma only — check SELinux too.** Unix
permissions can be correct and SELinux (enforcing mode) will still
deny access, because paths outside `/var/lib/libvirt/images` don't
carry the `virt_image_t` context by default:

```bash
sudo ls -Z /path/to/base-image.qcow2       # look for a context other than virt_image_t/svirt_image_t
sudo semanage fcontext -a -t virt_image_t "/path/to/base-image-dir(/.*)?"
sudo restorecon -Rv /path/to/base-image-dir
```

Confirm whether SELinux is actually the blocker (as distinct from the
Unix permission issue above) with `sudo ausearch -m avc -ts recent` or
`journalctl -t setroubleshoot`.

None of this is gated by `labctl`'s own `--apply`/`dry_run_default`
logic — the readiness/dry-run gates already passed by the time
`virsh start` reached this error; it is purely the OS-level file
permission model for the QEMU process itself.

## Security warning

Do not put real credentials, private SSH keys, API tokens, or vendor
management-plane secrets into any image or template in
`qemu/images/`. See the "Security warning" section of
[`../../qemu/images/README.md`](../../qemu/images/README.md) for the
full list of what is (and is not) safe to embed. Treat every image
built here as disposable: rebuild it for a new experiment rather than
reusing one that may have accumulated logs or state from a previous
run, and never commit a `.qcow2`/`.iso` artifact to this repository.
