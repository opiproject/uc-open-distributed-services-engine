#!/bin/sh
# ODSE VM-to-VM lab: best-effort DHCP enablement for the libvirt "default"
# NAT management NIC (see qemu/libvirt-xml/vm-vfio-template.xml).
#
# Why this exists: `prepare-vm-image.sh --method virt-customize` edits the
# guest filesystem OFFLINE via libguestfs -- it never boots the image, so
# whatever network configuration the base image *already* ships with is
# exactly what the guest gets on first real boot. Cloud/distro cloud
# images bundle DHCP-on-all-interfaces out of the box (that's what makes
# them "cloud" images); a generic/minimal/ISO-installed base image often
# does not, and the guest comes up with no IP at all on its management
# NIC. That shows up much later, non-obviously, as `virsh domifaddr`
# returning no rows and `labctl vm2vm verify-vms`/`configure-vm-ips`
# failing with GUEST_SSH_REQUIRED -- with nothing wrong on the host side.
#
# This script is uploaded into the guest and run via virt-customize
# `--firstboot` (executes once, as root, on the guest's first real boot,
# with the guest's actual network devices visible -- unlike anything run
# during the offline virt-customize chroot itself). It tries several
# common mechanisms so at least one applies regardless of which one the
# base image actually uses; anything not applicable is a silent no-op.
#
# Safe to run more than once (idempotent): every mechanism below either
# writes a `99-odse-*`-prefixed file (so it never overwrites a
# distro-owned config) or is a no-op if that subsystem is not installed.
#
# This intentionally does NOT touch a specific interface name (it cannot
# know one in advance -- see docs/operations/vm-image-preparation.md,
# "Two NICs, not one"); it matches on the common Linux predictable-name
# prefixes (`en*`, `eth*`) so it covers the VFIO-passthrough VF NIC too.
# That is fine here: `guest-network-setup.sh.template` explicitly leaves
# the interface holding the default route (the management NIC) alone,
# and the VF NIC gets its real static IP later from `configure-vm-ips`,
# so a transient DHCP attempt on it before that point is harmless.

set -u

log() { echo "[odse-first-boot-dhcp] $*" >&2; }

bring_up_all_interfaces() {
    for dev in /sys/class/net/*; do
        name="$(basename "${dev}")"
        [ "${name}" = "lo" ] && continue
        ip link set "${name}" up 2>/dev/null || true
    done
}

try_systemd_networkd() {
    [ -d /etc/systemd/network ] || return 0
    command -v systemctl >/dev/null 2>&1 || return 0
    cat > /etc/systemd/network/99-odse-dhcp.network <<'EOF'
[Match]
Name=en* eth*

[Network]
DHCP=yes
EOF
    log "wrote /etc/systemd/network/99-odse-dhcp.network"
    systemctl enable systemd-networkd >/dev/null 2>&1 || true
    systemctl restart systemd-networkd >/dev/null 2>&1 || true
}

try_netplan() {
    [ -d /etc/netplan ] || return 0
    command -v netplan >/dev/null 2>&1 || return 0
    cat > /etc/netplan/99-odse-dhcp.yaml <<'EOF'
network:
  version: 2
  ethernets:
    odse-en-match:
      match:
        name: "en*"
      dhcp4: true
    odse-eth-match:
      match:
        name: "eth*"
      dhcp4: true
EOF
    chmod 600 /etc/netplan/99-odse-dhcp.yaml
    log "wrote /etc/netplan/99-odse-dhcp.yaml"
    netplan apply >/dev/null 2>&1 || true
}

try_ifupdown() {
    [ -d /etc/network/interfaces.d ] || return 0
    for dev in /sys/class/net/*; do
        name="$(basename "${dev}")"
        [ "${name}" = "lo" ] && continue
        printf 'auto %s\niface %s inet dhcp\n' "${name}" "${name}" \
            > "/etc/network/interfaces.d/99-odse-${name}"
    done
    log "wrote /etc/network/interfaces.d/99-odse-*"
    command -v ifup >/dev/null 2>&1 && ifup -a >/dev/null 2>&1 || true
}

try_networkmanager() {
    command -v nmcli >/dev/null 2>&1 || return 0
    for dev in /sys/class/net/*; do
        name="$(basename "${dev}")"
        [ "${name}" = "lo" ] && continue
        nmcli device set "${name}" managed yes >/dev/null 2>&1 || true
        nmcli device connect "${name}" >/dev/null 2>&1 || true
    done
    log "asked NetworkManager to connect all non-loopback devices"
}

try_direct_dhcp_client() {
    if command -v dhclient >/dev/null 2>&1; then
        dhclient >/dev/null 2>&1 || true
        log "ran dhclient (no target interface -- all)"
    elif command -v dhcpcd >/dev/null 2>&1; then
        dhcpcd >/dev/null 2>&1 || true
        log "ran dhcpcd (no target interface -- all)"
    fi
}

bring_up_all_interfaces
try_systemd_networkd
try_netplan
try_ifupdown
try_networkmanager
try_direct_dhcp_client

log "done (see docs/operations/vm-image-preparation.md if the management NIC still has no address)"
exit 0
