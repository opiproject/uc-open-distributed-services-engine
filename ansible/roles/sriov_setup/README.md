# sriov_setup

Configures SR-IOV virtual functions (VFs) for selected physical functions
(PFs). This role validates PF existence and `sriov_totalvfs`, sets
`sriov_numvfs`, optionally persists the configuration across reboots via a
generated systemd unit, and reports the created VF PCI addresses and
netdev names (where available).

This role is intended to run **after**
[`ansible/roles/host_base`](../host_base/README.md) and **before**
`ansible/roles/switchdev_setup` and `ansible/roles/ovs_setup`, per the
sequencing described in
[`docs/architecture/overview.md`](../../../docs/architecture/overview.md)
§8. It does not configure switchdev mode, representor ports, or OVS/OVN —
those belong to the roles above.

> ## ⚠ Safety notes — read before running
>
> - **Changing `sriov_numvfs` can disrupt existing networking.** The Linux
>   kernel does not allow changing `sriov_numvfs` directly from one
>   non-zero value to a different non-zero value — the kernel requires it
>   to be reset to `0` first. Resetting to `0` **destroys and recreates
>   every existing VF on that PF**, along with anything depending on
>   them: running VMs with a VF attached, containers using a VF, VFIO
>   passthrough bindings, and any representor/OVS/OVN port bindings.
> - **This role refuses that disruptive path by default.** If a PF
>   already has a non-zero `sriov_numvfs` that differs from what you
>   requested, the role marks that PF invalid and reports why, rather
>   than silently tearing down and recreating VFs. Set
>   `sriov_setup_allow_disruptive_reconfigure: true` only after confirming
>   nothing depends on the PF's current VFs.
> - **Even a "safe" first-time VF creation is not risk-free.** Some NIC
>   drivers briefly reset the PF's link, or change the PF's own MAC/MTU
>   behavior, when `sriov_numvfs` is first set above `0`. Expect a brief
>   link flap on the PF itself when running this role for the first time
>   on a given interface, even though no existing VFs are destroyed.
> - **Run this against a host you have out-of-band (e.g. IPMI/serial)
>   access to**, or at minimum are not depending on the PF you are
>   reconfiguring for your current SSH session, the first time you use
>   this role against a given interface.

## What this role does

For each entry in `sriov_interfaces`:

1. **Validates the PF exists** (`tasks/validate_pf.yml`) — confirms the
   interface exists under `/sys/class/net` and has an associated PCI
   device.
2. **Checks `sriov_totalvfs`** (`tasks/check_totalvfs.yml`) — confirms the
   PF supports SR-IOV at all, and that the requested `num_vfs` does not
   exceed what the hardware/driver supports.
3. **Sets `sriov_numvfs`** (`tasks/set_numvfs.yml`) — idempotently, with
   the disruptive-change safety gate described above.
4. **Discovers created VFs** (`tasks/discover_vfs.yml`) — enumerates VF
   PCI addresses (via `.../device/virtfnN` symlinks) and, where a
   netdev-creating driver has already bound to a VF, its netdev name.
5. **Manages VF driver binding** (`tasks/bind_vf_drivers.yml`) — best
   effort, driven by the item's `autoload_driver` and `vf_driver` fields
   (see [Inputs](#inputs)).
6. **Optionally persists the configuration** (`tasks/persist_config.yml`)
   — only if `sriov_persist: true` — by generating and enabling a systemd
   oneshot unit per PF.

At the end of the run, the role reports a per-PF summary including
validity, any error, and the discovered VFs (PCI address, VF index, and
netdev name where available) via `ansible.builtin.debug`.

## Requirements

- Ansible 2.14+.
- No external collections required — every task uses only
  `ansible.builtin` modules.
- Target hosts must be Linux, with `sysfs` mounted normally (the default).
  Persistence requires `systemd`.
- Sudo/root privileges (writing to `/sys/class/net/*/device/sriov_numvfs`
  and `/sys/bus/pci/devices/*/driver*` requires it).
- `udevadm` is used opportunistically to let device state settle after
  changes; its absence does not fail the role.

## Inputs

### `sriov_interfaces` (required, default `[]`)

A list of objects, one per PF to configure:

| Field | Required | Default | Description |
|---|---|---|---|
| `pf_name` | Yes | — | PF network interface name, e.g. `eth0`. |
| `num_vfs` | Yes | — | Desired number of VFs on this PF. |
| `autoload_driver` | No | `true` | If `false`, this role attempts to unbind each created VF from whatever driver auto-probed it, leaving it driver-less — a common precursor to manual/VFIO/vendor-specific binding done by a later role (e.g. `qemu/vfio`). |
| `vf_driver` | No | none | If set (e.g. `"vfio-pci"`), this role attempts to bind each created VF to this driver via `driver_override` plus a probe. Best-effort — see [Known limitations](#known-limitations). |

### `sriov_persist` (required, default `false`)

If `true`, generate and enable a persistent systemd unit per configured
PF so `sriov_numvfs` is reapplied on every boot. If `false`, the
configuration applied by this run is **not** persisted and will be lost
on reboot. See [Persistence](#persistence).

### Safety and behavior controls

| Variable | Default | Description |
|---|---|---|
| `sriov_setup_allow_disruptive_reconfigure` | `false` | Allow the disruptive reset-to-0-then-reconfigure path described in the safety notes above. |
| `sriov_setup_fail_fast` | `false` | If `true`, fail the whole play immediately on any single interface's validation error. If `false`, skip that interface with a warning and continue processing the rest of `sriov_interfaces`. |
| `sriov_setup_systemd_unit_dir` | `/etc/systemd/system` | Directory for generated systemd units. |
| `sriov_setup_persist_method` | `systemd` | Placeholder for a future distro-specific persistence mechanism; `systemd` is the only one currently implemented. |

## Example playbook

```yaml
- name: Configure SR-IOV VFs on two PFs
  hosts: dpu_capable_hosts
  become: true
  roles:
    - role: sriov_setup
      vars:
        sriov_interfaces:
          - pf_name: eth0
            num_vfs: 8
            autoload_driver: true
          - pf_name: eth1
            num_vfs: 4
            autoload_driver: false
            vf_driver: vfio-pci
        sriov_persist: true
```

### First-time setup on a fresh PF (num_vfs currently 0)

```yaml
- name: First-time SR-IOV setup
  hosts: new_dpu_host
  become: true
  roles:
    - role: sriov_setup
      vars:
        sriov_interfaces:
          - pf_name: ens3f0
            num_vfs: 16
        sriov_persist: false   # try it out first; persist once confirmed
```

### Deliberately allowing a disruptive reconfiguration

```yaml
- name: Reconfigure VF count on a PF that already has VFs in use
  hosts: dpu_host
  become: true
  roles:
    - role: sriov_setup
      vars:
        sriov_interfaces:
          - pf_name: ens3f0
            num_vfs: 32   # was previously 16 -- this WILL destroy/recreate all 16 existing VFs
        sriov_setup_allow_disruptive_reconfigure: true   # confirm nothing depends on the current VFs first!
```

### Preparing VFs for VFIO passthrough (no host driver bound)

```yaml
- name: Prepare VFs for VFIO passthrough into KVM guests
  hosts: dpu_host
  become: true
  roles:
    - role: sriov_setup
      vars:
        sriov_interfaces:
          - pf_name: ens3f0
            num_vfs: 8
            autoload_driver: false
            vf_driver: vfio-pci
```

### Assessing without changing anything disruptive (fail-fast off, no persistence)

```yaml
- name: Assess SR-IOV readiness across a fleet without persisting
  hosts: candidate_hosts
  become: true
  roles:
    - role: sriov_setup
      vars:
        sriov_interfaces:
          - pf_name: eth0
            num_vfs: 4
        sriov_persist: false
        sriov_setup_fail_fast: false
```

## Output

The role registers a per-run fact, `sriov_setup_all_results`, and prints
it via `ansible.builtin.debug` at the end of the play. Each entry looks
like:

```json
{
  "pf_name": "eth1",
  "requested_num_vfs": 4,
  "valid": true,
  "error": null,
  "persisted": true,
  "vfs": [
    { "vf_index": 0, "pci_address": "0000:03:00.2", "netdev": "eth1v0" },
    { "vf_index": 1, "pci_address": "0000:03:00.3", "netdev": null },
    { "vf_index": 2, "pci_address": "0000:03:00.4", "netdev": null },
    { "vf_index": 3, "pci_address": "0000:03:00.5", "netdev": null }
  ]
}
```

A `netdev` of `null` means no netdev-creating driver had bound to that VF
by the time this role checked (common when `autoload_driver: false` or
`vf_driver: vfio-pci` was requested — VFIO-bound VFs intentionally have no
netdev).

## Persistence

When `sriov_persist: true`, this role renders one systemd unit per PF —
`sriov-numvfs-<pf_name>.service` in `sriov_setup_systemd_unit_dir` — and
enables it. The unit's `ExecStart` mirrors the same idempotent,
disruption-aware logic used at runtime by `tasks/set_numvfs.yml`: it only
resets to `0` first if the current value is non-zero and different from
the target.

Caveats:

- **Interface names requiring `systemd-escape` are not specially
  handled.** The generated unit's `After=`/`Wants=` lines reference
  `sys-subsystem-net-devices-<pf_name>.device`. Most real-world interface
  names (e.g. `eth0`, `ens3f0`, `enp3s0f0np0`) work fine unescaped, but an
  interface name containing characters that require escaping (e.g. a
  colon) will not resolve correctly. Verify with
  `systemd-escape --path /sys/subsystem/net/devices/<pf_name>` if in
  doubt.
- **This role does not remove a previously generated unit if
  `sriov_persist` is later set back to `false`.** Remove
  `/etc/systemd/system/sriov-numvfs-<pf_name>.service` manually (and run
  `systemctl disable --now`) if you no longer want persistence for that
  PF.
- Persistence is currently **systemd-only**; see
  `sriov_setup_persist_method` in `defaults/main.yml` for the placeholder
  reserved for a future distro-specific alternative.

## Known limitations

- **VF driver binding (`autoload_driver`/`vf_driver`) is best-effort and
  generic.** It uses the standard `driver_override` + unbind/probe sysfs
  mechanism, which is not guaranteed to behave identically across every
  vendor driver. For anything production-critical, cross-check the result
  against `docs/vendor-readiness/vendor-capability-matrix.md` and
  vendor-specific tooling, and consider using a `vendor_*` role instead
  for vendor-specific binding requirements.
- **No rollback on partial failure.** If setting `sriov_numvfs` succeeds
  but VF driver binding or persistence fails partway through, this role
  does not automatically revert `sriov_numvfs` — it reports the failure
  and leaves the host in its current state for manual review.
- **PF link flap on first VF creation is possible** and is not treated as
  an error by this role (see safety notes above).
- **This role does not itself install any packages** (e.g. `ethtool`,
  `iproute2`) — it assumes those are already present, typically via
  `ansible/roles/host_base`.
- **Systemd-escape edge cases in generated units are not handled** (see
  [Persistence](#persistence)).

## Testing

Role-level testing is intended to live under `ci/molecule/` (per the
top-level repository layout), ideally against a simulated SR-IOV device
(e.g. kernel `netdevsim`) so this role's logic can be exercised in CI
without physical hardware. No Molecule scenario exists for this role yet
— see `ci/molecule/README.md` and `ci/kind-simulated-dpu/README.md`.
