#!/bin/sh
# ODSE VM-to-VM lab: disable SSH password auth and make sure the SSH
# service is enabled and running.
#
# Runs at the guest's real first boot (via virt-customize --firstboot),
# after guest-first-boot-install-packages.sh. It used to run *during*
# the offline virt-customize session, but /etc/ssh/sshd_config does not
# exist yet at that point if openssh-server/openssh was not already
# present in the base image -- package installation was moved to
# guest-first-boot-install-packages.sh for the same disk-space reason
# (see that script's header and "Not enough disk space to install
# packages" in docs/operations/vm-image-preparation.md), so this has to
# move with it.
#
# Safe to run more than once (idempotent).

set -u

log() { echo "[odse-first-boot-configure-sshd] $*" >&2; }

CONF="/etc/ssh/sshd_config"
if [ ! -f "${CONF}" ]; then
    log "no ${CONF} found -- openssh-server/openssh install likely did not complete (see guest-first-boot-install-packages.sh log); nothing to configure"
    exit 0
fi

sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "${CONF}"
log "PasswordAuthentication no set in ${CONF}"

if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-enabled ssh.service >/dev/null 2>&1 || systemctl is-enabled sshd.service >/dev/null 2>&1; then
        log "ssh service already enabled"
    else
        systemctl enable ssh.service >/dev/null 2>&1 \
            || systemctl enable sshd.service >/dev/null 2>&1 \
            || log "could not enable ssh.service/sshd.service by either name -- check 'systemctl list-unit-files ssh*.service sshd*.service'"
    fi
    systemctl restart ssh.service >/dev/null 2>&1 || systemctl restart sshd.service >/dev/null 2>&1 || true
fi

log "done"
exit 0
