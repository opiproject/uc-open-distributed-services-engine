#!/usr/bin/env bash
# ODSE VM-to-VM lab: prepare one guest qcow2 disk image.
#
# Supports three approaches (pick one with --method):
#   cloud-init       cloud image + cloud-init NoCloud seed ISO (recommended
#                     when your base image already supports cloud-init,
#                     e.g. Ubuntu/Debian/Fedora "cloud" images)
#   virt-customize    offline edit of a base image with libguestfs
#                     (no boot required; works on non-cloud-init images too)
#   manual            prints the manual checklist and does nothing else
#                     (any base image; you do the work over console/SSH)
#
# Dry-run by default: prints every command it would run without running
# it. Pass --apply to actually execute. See
# docs/operations/vm-image-preparation.md for the full walkthrough and
# qemu/images/README.md for required guest packages and the security
# warning about credentials.
#
# Usage:
#   prepare-vm-image.sh --method cloud-init \
#     --base-image /path/to/ubuntu-22.04-cloudimg.qcow2 \
#     --output /var/lib/libvirt/images/odse-vm2vm-vm1.qcow2 \
#     --hostname odse-vm2vm-vm1 --ssh-user odse --ssh-key-file ~/.ssh/id_ed25519.pub \
#     [--size 10G] [--apply]
#
#   prepare-vm-image.sh --method virt-customize \
#     --base-image /path/to/base.qcow2 --output /var/lib/libvirt/images/odse-vm2vm-vm2.qcow2 \
#     --ssh-user odse --ssh-key-file ~/.ssh/id_ed25519.pub [--apply]
#
#   prepare-vm-image.sh --method manual

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

METHOD=""
BASE_IMAGE=""
OUTPUT=""
HOSTNAME_VALUE=""
SSH_USER=""
SSH_KEY_FILE=""
SIZE="10G"
APPLY=0

usage() {
  grep '^#' "$0" | sed 's/^#//; s/^ //' | sed -n '2,40p'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --method) METHOD="$2"; shift 2 ;;
    --base-image) BASE_IMAGE="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --hostname) HOSTNAME_VALUE="$2"; shift 2 ;;
    --ssh-user) SSH_USER="$2"; shift 2 ;;
    --ssh-key-file) SSH_KEY_FILE="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

run() {
  echo "+ $*"
  if [[ "${APPLY}" -eq 1 ]]; then
    "$@"
  fi
}

if [[ "${METHOD}" != "manual" && "${APPLY}" -eq 0 ]]; then
  echo "## DRY RUN (pass --apply to execute the commands below)" >&2
fi

case "${METHOD}" in
  cloud-init)
    for req in BASE_IMAGE OUTPUT HOSTNAME_VALUE SSH_USER SSH_KEY_FILE; do
      if [[ -z "${!req}" ]]; then
        echo "error: --method cloud-init requires --base-image, --output, --hostname, --ssh-user, --ssh-key-file" >&2
        exit 2
      fi
    done
    if [[ ! -f "${SSH_KEY_FILE}" ]]; then
      echo "error: --ssh-key-file ${SSH_KEY_FILE} not found (must be a PUBLIC key, e.g. id_ed25519.pub)" >&2
      exit 2
    fi
    WORKDIR="$(mktemp -d)"
    trap 'rm -rf "${WORKDIR}"' EXIT

    echo "== rendering cloud-init user-data/meta-data =="
    SSH_PUBLIC_KEY="$(cat "${SSH_KEY_FILE}")"
    sed \
      -e "s#<HOSTNAME>#${HOSTNAME_VALUE}#g" \
      -e "s#<SSH_USER>#${SSH_USER}#g" \
      -e "s#<SSH_PUBLIC_KEY>#${SSH_PUBLIC_KEY}#g" \
      "${SCRIPT_DIR}/cloud-init/user-data.template" > "${WORKDIR}/user-data"
    sed \
      -e "s#<INSTANCE_ID>#${HOSTNAME_VALUE}-$(date -u +%Y%m%dT%H%M%SZ)#g" \
      -e "s#<HOSTNAME>#${HOSTNAME_VALUE}#g" \
      "${SCRIPT_DIR}/cloud-init/meta-data.template" > "${WORKDIR}/meta-data"

    echo "== injecting the real guest-network-setup.sh (from the template) =="
    run cp "${SCRIPT_DIR}/guest-network-setup.sh.template" "${WORKDIR}/guest-network-setup.sh"

    SEED_ISO="${OUTPUT%.qcow2}-seed.iso"
    echo "== building NoCloud seed ISO: ${SEED_ISO} =="
    run cloud-localds "${SEED_ISO}" "${WORKDIR}/user-data" "${WORKDIR}/meta-data"

    echo "== creating qcow2 overlay backed by ${BASE_IMAGE} =="
    run qemu-img create -f qcow2 -F qcow2 -b "${BASE_IMAGE}" "${OUTPUT}" "${SIZE}"

    echo
    echo "Next: boot the image once with the seed ISO attached as a CDROM"
    echo "so cloud-init applies user-data (packages, SSH key, hostname), then"
    echo "detach the seed ISO for normal use."
    echo "labctl next step (after both VM images exist):"
    echo "  python3 tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --json"
    echo "Validate with:"
    echo "  ${SCRIPT_DIR}/validate-vm-image.sh --host <mgmt-ip> --user ${SSH_USER} --identity-file <matching-private-key>"
    ;;

  virt-customize)
    for req in BASE_IMAGE OUTPUT SSH_USER SSH_KEY_FILE; do
      if [[ -z "${!req}" ]]; then
        echo "error: --method virt-customize requires --base-image, --output, --ssh-user, --ssh-key-file" >&2
        exit 2
      fi
    done
    if [[ ! -f "${SSH_KEY_FILE}" ]]; then
      echo "error: --ssh-key-file ${SSH_KEY_FILE} not found (must be a PUBLIC key, e.g. id_ed25519.pub)" >&2
      exit 2
    fi
    if ! command -v virt-customize >/dev/null 2>&1; then
      echo "warning: virt-customize not found on PATH (package: libguestfs-tools / guestfs-tools)" >&2
    fi

    echo "== copying ${BASE_IMAGE} -> ${OUTPUT} =="
    run cp --reflink=auto "${BASE_IMAGE}" "${OUTPUT}"

    echo "== growing the virtual disk to ${SIZE} (--size) =="
    echo "   (this only grows the qcow2 container; the guest's own partition"
    echo "   table/filesystem is grown separately, at real first boot, by"
    echo "   guest-first-boot-growroot.sh below -- see 'Not enough disk space"
    echo "   to install packages' in docs/operations/vm-image-preparation.md)"
    run qemu-img resize "${OUTPUT}" "${SIZE}"

    SSH_PUBLIC_KEY="$(cat "${SSH_KEY_FILE}")"
    echo "== creating user and injecting SSH key with virt-customize =="
    echo "   (package install and sshd config are deliberately NOT done here --"
    echo "   they run later, at guest firstboot, after the filesystem has"
    echo "   actually been grown; see the firstboot notes below and 'Not"
    echo "   enough disk space to install packages' in"
    echo "   docs/operations/vm-image-preparation.md)"
    run virt-customize -a "${OUTPUT}" \
      --run-command "useradd -m -s /bin/bash -G sudo ${SSH_USER} || true" \
      --run-command "passwd -l ${SSH_USER}" \
      --ssh-inject "${SSH_USER}:string:${SSH_PUBLIC_KEY}" \
      --run-command "echo '${SSH_USER} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-${SSH_USER}-no-tty && echo 'Defaults:${SSH_USER} !requiretty' >> /etc/sudoers.d/90-${SSH_USER}-no-tty && chmod 0440 /etc/sudoers.d/90-${SSH_USER}-no-tty" \
      --upload "${SCRIPT_DIR}/guest-network-setup.sh.template:/usr/local/bin/guest-network-setup.sh" \
      --run-command "chmod 0755 /usr/local/bin/guest-network-setup.sh" \
      --firstboot "${SCRIPT_DIR}/guest-first-boot-growroot.sh" \
      --firstboot "${SCRIPT_DIR}/guest-first-boot-dhcp.sh" \
      --firstboot "${SCRIPT_DIR}/guest-first-boot-install-packages.sh" \
      --firstboot "${SCRIPT_DIR}/guest-first-boot-configure-sshd.sh" \
      --hostname "${HOSTNAME_VALUE:-odse-vm2vm-guest}"

    echo
    echo "Notes (all four firstboot scripts run once, in this order, on the"
    echo "guest's real first boot -- never inside the offline virt-customize"
    echo "chroot itself -- because each depends on the one(s) before it):"
    echo "1. guest-first-boot-growroot.sh grows the root partition/filesystem"
    echo "   to fill the ${SIZE} virtual disk (base image's own partition"
    echo "   table is otherwise kept as-is, often much smaller than ${SIZE})."
    echo "2. guest-first-boot-dhcp.sh is a best-effort DHCP enabler for the"
    echo "   management NIC (needed before step 3 can reach a package mirror)."
    echo "3. guest-first-boot-install-packages.sh installs"
    echo "   iproute2/iputils-ping/ethtool/iperf3/tcpdump/openssh-server --"
    echo "   deliberately not done offline via --install, which used to edit"
    echo "   a filesystem still at the base image's original, often much"
    echo "   smaller, size and could fail with 'no space left on device'."
    echo "4. guest-first-boot-configure-sshd.sh disables SSH password auth"
    echo "   and enables the service, now that openssh-server actually exists."
    echo "See docs/operations/vm-image-preparation.md if any of 'df -h /'"
    echo "still small, 'virsh domifaddr' still empty, or a required package"
    echo "still missing (validate-vm-image.sh) persists after first boot."
    echo
    echo "labctl next step (after both VM images exist):"
    echo "  python3 tools/labctl.py vm2vm create-vms --config config/lab-nvidia-vm2vm.yaml --json"
    echo "VF PCI assignment is a separate stage: labctl vm2vm attach-vfs"
    echo "Validate with:"
    echo "  ${SCRIPT_DIR}/validate-vm-image.sh --host <mgmt-ip> --user ${SSH_USER} --identity-file <matching-private-key>"
    ;;

  manual)
    cat <<'EOF'
Manual preparation checklist (any base image, no cloud-init or
libguestfs required):

  1. Boot the base image once (console access or an existing management
     NIC) with enough disk to hold the packages below. If the base
     image's own disk/partition is too small (step 3 runs out of space),
     grow it now, before installing anything: resize the underlying
     qcow2 with 'qemu-img resize <path> <size>' while the guest is shut
     off, then boot and grow the partition + filesystem from inside the
     guest (see guest-first-boot-growroot.sh for the growpart/resize2fs/
     xfs_growfs/btrfs commands it runs, or run it directly: sh
     qemu/images/guest-first-boot-growroot.sh).
  2. Create a non-root sudo user for the lab (do NOT use a shared or
     production credential), and grant it passwordless, tty-free sudo --
     this user's password is locked in the next line, so without this
     step it can NEVER successfully sudo at all (no password to type,
     even interactively), and configure-vm-ips/run-traffic invoke sudo
     over a non-interactive SSH session with no controlling terminal,
     so plain group-sudo membership (which still prompts for a
     password) is not enough either:
       sudo useradd -m -s /bin/bash -G sudo <ssh_user>
       echo '<ssh_user> ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/90-<ssh_user>-no-tty
       echo 'Defaults:<ssh_user> !requiretty' | sudo tee -a /etc/sudoers.d/90-<ssh_user>-no-tty
       sudo chmod 0440 /etc/sudoers.d/90-<ssh_user>-no-tty
       sudo passwd -l <ssh_user>          # lock password auth
  3. Install required packages:
       sudo apt-get update
       sudo apt-get install -y iproute2 iputils-ping ethtool iperf3 \
         tcpdump openssh-server
     (or the dnf/zypper/apk equivalent for your distro)
  4. Authorize your PUBLIC key only (never copy a private key into the
     guest):
       sudo mkdir -p /home/<ssh_user>/.ssh
       echo '<your ssh public key>' | sudo tee /home/<ssh_user>/.ssh/authorized_keys
       sudo chown -R <ssh_user>:<ssh_user> /home/<ssh_user>/.ssh
       sudo chmod 700 /home/<ssh_user>/.ssh
       sudo chmod 600 /home/<ssh_user>/.ssh/authorized_keys
  5. Disable SSH password authentication:
       sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
       sudo systemctl restart ssh || sudo systemctl restart sshd
  6. Copy the network helper into the guest:
       scp qemu/images/guest-network-setup.sh.template <ssh_user>@<mgmt-ip>:/usr/local/bin/guest-network-setup.sh
       ssh <ssh_user>@<mgmt-ip> chmod 0755 /usr/local/bin/guest-network-setup.sh
  7. You already have console/SSH access, so the management NIC's DHCP
     is already working -- nothing else to do for that here (this is
     the gap the virt-customize approach's --firstboot step exists to
     close; it doesn't apply to this manual approach).
  8. Shut the guest down cleanly and note its disk image path for
     vms[].image in your lab config.
  9. Validate: qemu/images/validate-vm-image.sh --host <mgmt-ip> \
       --user <ssh_user> --identity-file <matching-private-key>

See docs/operations/vm-image-preparation.md for background on why
these three approaches exist and when to prefer each one.
EOF
    ;;

  "")
    echo "error: --method is required (cloud-init | virt-customize | manual)" >&2
    usage
    exit 2
    ;;

  *)
    echo "error: unknown --method '${METHOD}' (expected cloud-init | virt-customize | manual)" >&2
    exit 2
    ;;
esac
