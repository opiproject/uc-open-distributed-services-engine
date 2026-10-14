#!/bin/sh
# ODSE VM-to-VM lab: best-effort root filesystem growth to fill the full
# virtual disk.
#
# Why this exists: `prepare-vm-image.sh --method virt-customize` copies
# the base image and then grows the *virtual disk* with `qemu-img
# resize` to `--size` (default 10G) -- but it deliberately does NOT try
# to grow the actual partition/filesystem from inside the offline
# libguestfs appliance, because device naming inside that appliance
# (e.g. /dev/sda) does not reliably match the real guest's runtime
# naming (e.g. /dev/vda for virtio-blk). Doing a partition resize
# against the wrong device name offline is a good way to corrupt the
# disk. The real guest kernel at actual first boot always has the
# device naming right, so that is where this runs instead.
#
# Symptom without this: the qcow2/virtual disk is the requested size,
# but `df -h /` inside the guest still shows the base image's original
# (often much smaller) size -- e.g. not enough room for `apt-get
# install` to complete. See docs/operations/vm-image-preparation.md,
# "Not enough disk space to install packages".
#
# Uploaded and run via virt-customize `--firstboot` (executes once, as
# root, on the guest's actual first real boot). Safe to run again by
# hand (idempotent: growpart/resize2fs/xfs_growfs/btrfs-resize are all
# no-ops once the filesystem already fills the partition/disk).

set -u

log() { echo "[odse-first-boot-growroot] $*" >&2; }

root_source="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
if [ -z "${root_source}" ]; then
    log "could not determine the root device (findmnt failed or missing); nothing to do"
    exit 0
fi

# Split e.g. /dev/vda1 -> disk=/dev/vda part=1, or /dev/nvme0n1p1 ->
# disk=/dev/nvme0n1 part=1. Root-on-LVM/mapper (no plain partition
# number to grow) is intentionally left alone -- out of scope for this
# best-effort script, and safer to do nothing than guess wrong.
case "${root_source}" in
    *p[0-9]*)
        disk="${root_source%p*}"
        part="${root_source##*p}"
        ;;
    *[0-9])
        disk="$(printf '%s' "${root_source}" | sed -E 's/[0-9]+$//')"
        part="$(printf '%s' "${root_source}" | grep -o '[0-9]*$')"
        ;;
    *)
        log "root is on '${root_source}' (LVM/mapper/other, no plain partition number) -- not attempting a partition resize"
        disk=""
        part=""
        ;;
esac

if [ -n "${disk}" ] && [ -n "${part}" ]; then
    log "root device ${root_source} -> disk ${disk} partition ${part}"
    if command -v growpart >/dev/null 2>&1; then
        growpart "${disk}" "${part}" 2>&1 | sed 's/^/[growpart] /' >&2 || true
    elif command -v parted >/dev/null 2>&1; then
        log "growpart not installed; trying 'parted resizepart' instead"
        parted -s "${disk}" resizepart "${part}" 100% 2>&1 | sed 's/^/[parted] /' >&2 || true
        command -v partprobe >/dev/null 2>&1 && partprobe "${disk}" 2>/dev/null || true
    else
        log "neither growpart nor parted is installed; cannot grow the partition (the filesystem resize below may still be a no-op)"
    fi
fi

fstype="$(findmnt -n -o FSTYPE / 2>/dev/null || true)"
case "${fstype}" in
    ext2|ext3|ext4)
        command -v resize2fs >/dev/null 2>&1 \
            && resize2fs "${root_source}" 2>&1 | sed 's/^/[resize2fs] /' >&2 \
            || log "resize2fs not available or failed"
        ;;
    xfs)
        command -v xfs_growfs >/dev/null 2>&1 \
            && xfs_growfs / 2>&1 | sed 's/^/[xfs_growfs] /' >&2 \
            || log "xfs_growfs not available or failed"
        ;;
    btrfs)
        command -v btrfs >/dev/null 2>&1 \
            && btrfs filesystem resize max / 2>&1 | sed 's/^/[btrfs] /' >&2 \
            || log "btrfs not available or failed"
        ;;
    *)
        log "unrecognized/unknown root filesystem type '${fstype}'; not resizing"
        ;;
esac

log "done -- run 'df -h /' to confirm; see docs/operations/vm-image-preparation.md if it's still too small"
exit 0
