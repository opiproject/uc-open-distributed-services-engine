#!/bin/sh
# ODSE VM-to-VM lab: install the guest packages this lab needs.
#
# Why this runs at the guest's real first boot (via virt-customize
# --firstboot), instead of during virt-customize --install like it used
# to: `prepare-vm-image.sh --method virt-customize` grows the *virtual
# disk* with `qemu-img resize`, but the guest's own partition and
# filesystem are only grown later, at this same first boot, by
# guest-first-boot-growroot.sh. Installing packages offline (inside the
# virt-customize appliance, before that resize happens) edits a
# filesystem that is still the base image's original, often much
# smaller, size -- `apt-get install`/equivalent can fail with "no space
# left on device" even though `qemu-img resize` already grew the qcow2
# container. See "Not enough disk space to install packages" in
# docs/operations/vm-image-preparation.md.
#
# Ordering matters: this must run after guest-first-boot-growroot.sh
# (needs the room) and after guest-first-boot-dhcp.sh (needs network to
# reach a package mirror). `prepare-vm-image.sh` passes `--firstboot`
# flags in that order; virt-customize runs them in the order given.
#
# Safe to run more than once (idempotent): installing already-installed
# packages is a no-op for every package manager below.

set -u

log() { echo "[odse-first-boot-install-packages] $*" >&2; }

PACKAGES="iproute2 iputils-ping ethtool iperf3 tcpdump openssh-server"

if command -v apt-get >/dev/null 2>&1; then
    log "apt-get update"
    DEBIAN_FRONTEND=noninteractive apt-get update 2>&1 | sed 's/^/[apt-get update] /' >&2
    log "apt-get install -y ${PACKAGES}"
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y ${PACKAGES} 2>&1 | sed 's/^/[apt-get install] /' >&2
elif command -v dnf >/dev/null 2>&1; then
    log "dnf install -y ${PACKAGES}"
    # shellcheck disable=SC2086
    dnf install -y ${PACKAGES} 2>&1 | sed 's/^/[dnf] /' >&2
elif command -v yum >/dev/null 2>&1; then
    log "yum install -y ${PACKAGES}"
    # shellcheck disable=SC2086
    yum install -y ${PACKAGES} 2>&1 | sed 's/^/[yum] /' >&2
elif command -v zypper >/dev/null 2>&1; then
    log "zypper --non-interactive install ${PACKAGES}"
    # shellcheck disable=SC2086
    zypper --non-interactive install ${PACKAGES} 2>&1 | sed 's/^/[zypper] /' >&2
elif command -v apk >/dev/null 2>&1; then
    # Alpine's SSH server package is "openssh", not "openssh-server".
    log "apk add --no-cache iproute2 iputils ethtool iperf3 tcpdump openssh"
    apk add --no-cache iproute2 iputils ethtool iperf3 tcpdump openssh 2>&1 | sed 's/^/[apk] /' >&2
else
    log "no known package manager found (apt-get/dnf/yum/zypper/apk) -- install manually: ${PACKAGES}"
fi

missing=""
for bin in ip ping ethtool iperf3 tcpdump sshd; do
    command -v "${bin}" >/dev/null 2>&1 || missing="${missing} ${bin}"
done
if [ -n "${missing}" ]; then
    log "warning: still missing after install attempt:${missing} -- check network/mirror reachability (see guest-first-boot-dhcp.sh log) and re-run this script by hand once the guest is reachable"
else
    log "all required tools present"
fi

log "done -- verify with: qemu/images/validate-vm-image.sh --host <mgmt-ip> --user <ssh_user> --identity-file <key>"
exit 0
