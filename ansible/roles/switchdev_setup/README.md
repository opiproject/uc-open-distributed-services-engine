# switchdev_setup

Prepares switchdev eswitch mode (where supported) on one or more physical
functions (PFs), using `devlink`. Designed to be vendor-neutral: the task
logic never branches on detected hardware vendor. Vendor-specific quirks
are expressed as data under `switchdev_setup_vendor_hooks` and only
applied when a device entry opts into them.

> ⚠ **Safety notes**
>
> - Changing eswitch mode is disruptive to the PF's networking while the
>   change is in progress, and can briefly flap the link. Do not run this
>   role over a management connection that transits the PF being
>   reconfigured.
> - Some drivers/firmware require VFs to be unbound before an eswitch mode
>   change will succeed (`require_vfs_unbound: true`); others do not care.
>   This role does not guess -- it only unbinds when you ask it to.
> - **VFs unbound by this role are not automatically rebound.** Rebinding
>   is left to a vendor-specific hook, a downstream role (e.g.
>   `ansible/roles/sriov_setup`), or a manual step. See "Known
>   limitations" below.
> - Not every vendor/firmware/driver combination supports switchdev mode
>   at all. Where this role cannot confirm support, it reports a clear
>   per-device error rather than assuming success. See
>   `docs/vendor-readiness/vendor-capability-matrix.md` for the current,
>   deliberately conservative, per-vendor switchdev support assessment
>   (mostly rated `discovery-required` or `likely`, not `supported`, as of
>   this writing).

## What this role does

For each device in `switchdev_devices`:

1. **Detect the devlink path** for the PF (`devlink dev show pci/<addr>`),
   resolving either a PF interface name or a PCI address to a devlink
   handle.
2. **Display the current eswitch mode** (`devlink dev eswitch show`).
3. **Optionally unbind VFs** if `require_vfs_unbound: true`.
4. **Set the eswitch mode** (`devlink dev eswitch set ... mode <mode>`),
   running any configured vendor pre/post hook commands around the
   change, and re-reading the mode afterwards to confirm it took effect.
5. **Verify representor ports appear**, by asking `devlink port show` for
   ports with `flavour pcivf` (rather than guessing a vendor-specific
   netdev naming convention).
6. **Records a per-device result** into an aggregate list, which is
   written to a facts file at the end of the run.

Devices are processed independently: one device's failure does not stop
processing of the others unless `switchdev_setup_fail_fast: true`.

## Requirements

- `devlink` (from `iproute2`) must be installed and usable. This role
  does not install packages -- see `ansible/roles/host_base`.
- Target PFs must already exist (this role does not create VFs -- see
  `ansible/roles/sriov_setup`, which typically runs before or after this
  role depending on your driver's required ordering).
- Root privileges (writes to sysfs, runs `devlink`).

## Inputs

| Variable | Default | Description |
|---|---|---|
| `switchdev_devices` | `[]` | List of devices to configure. See schema below. |
| `switchdev_mode` | `switchdev` | Global target mode (`switchdev` or `legacy`), used unless a device overrides it. |
| `require_vfs_unbound` | `false` | If true, unbind all VF drivers on a PF before changing its eswitch mode. |
| `switchdev_setup_fail_fast` | `false` | If true, fail the whole play on the first device error instead of skipping it with a warning. |
| `switchdev_setup_default_vendor` | `generic` | Vendor hook key used for devices that don't specify their own `vendor`. |
| `switchdev_setup_vendor_hooks` | see `defaults/main.yml` | Vendor-keyed hook definitions (pre/post commands, extra `devlink` args, settle time). |
| `switchdev_setup_facts_dir` | `/etc/ansible/facts.d` | Directory for the output facts file. |
| `switchdev_setup_facts_file_name` | `switchdev_setup.fact` | Filename for the output facts file. |
| `switchdev_setup_refresh_local_facts` | `true` | Re-gather `ansible_local` facts after writing, so the same play can see the new data immediately. |

### `switchdev_devices` schema

Each entry is **either** a plain string, **or** an object:

```yaml
switchdev_devices:
  # Plain string: a PF interface name or a PCI address (either form).
  - eth1
  - "0000:03:00.0"
  - "03:00.1"

  # Object form: allows PCI address, per-device mode override, and vendor hook selection.
  - pci_address: "0000:03:00.1"
  - pf_name: ens3f0
    mode: switchdev
    vendor: nvidia_mellanox_bluefield
```

At least one of `pf_name` / `pci_address` is required in object form.

### Vendor hooks

This role does not detect hardware vendor or branch its own logic on it.
Instead, `switchdev_setup_vendor_hooks` is a plain data structure you can
extend or override:

```yaml
switchdev_setup_vendor_hooks:
  my_custom_card:
    pre_mode_change_commands:
      - "/usr/local/bin/my-card-quirk-before.sh"
    post_mode_change_commands:
      - "/usr/local/bin/my-card-quirk-after.sh"
    eswitch_set_extra_args: "inline-mode transport encap-mode basic"
    representor_settle_seconds: 8
```

Select it per device with `vendor: my_custom_card`, or set
`switchdev_setup_default_vendor: my_custom_card` to apply it to every
device that doesn't specify its own `vendor`.

The `nvidia_mellanox_bluefield`, `amd_pensando`, `intel_ipu`, and
`marvell_octeon` entries shipped in `defaults/main.yml` are **illustrative
placeholders only** -- they currently apply no extra arguments or
commands beyond the `generic` defaults, because this project has not yet
validated vendor-specific eswitch requirements against real hardware.
Treat them as extension points to fill in, not as confirmed working
configuration.

## Output: facts file

Results are written as an Ansible custom facts file at
`{{ switchdev_setup_facts_dir }}/{{ switchdev_setup_facts_file_name }}`
(default: `/etc/ansible/facts.d/switchdev_setup.fact`). Because this uses
the standard Ansible local-facts convention, a subsequent fact gather
exposes the content as `ansible_local.switchdev_setup` -- useful for
downstream roles (e.g. `ansible/roles/ovs_setup`) that need to know which
representor ports were created.

Each entry in the array:

```json
{
  "identifier": "ens3f0",
  "pci_address": "0000:03:00.0",
  "pf_name": "ens3f0",
  "requested_mode": "switchdev",
  "previous_mode": "legacy",
  "current_mode": "switchdev",
  "vendor_hook": "generic",
  "vfs_unbound": false,
  "valid": true,
  "error": null,
  "representor_ports": [
    {"handle": "pci/0000:03:00.0/1", "netdev": "eth1v0", "pfnum": 0, "vfnum": 0}
  ]
}
```

## Example playbooks

### Enable switchdev mode on a single PF

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: switchdev_setup
      vars:
        switchdev_devices:
          - eth1
        switchdev_mode: switchdev
```

### Multiple PFs, unbinding VFs first

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: switchdev_setup
      vars:
        require_vfs_unbound: true
        switchdev_devices:
          - "0000:03:00.0"
          - "0000:03:00.1"
        switchdev_mode: switchdev
```

### Per-device mode override and vendor hook

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: switchdev_setup
      vars:
        switchdev_devices:
          - pf_name: ens3f0
            mode: switchdev
            vendor: nvidia_mellanox_bluefield
          - pf_name: ens3f1
            mode: legacy
```

### Revert to legacy mode

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: switchdev_setup
      vars:
        switchdev_devices:
          - eth1
        switchdev_mode: legacy
```

### Fail fast on any error (e.g. in CI/pre-flight checks)

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: switchdev_setup
      vars:
        switchdev_setup_fail_fast: true
        switchdev_devices:
          - eth1
```

## Known limitations

- **Representor verification is a heuristic, not a guarantee.** This role
  checks that `devlink port show` reports `flavour pcivf` entries for the
  PF after the mode change, and compares the count against the number of
  VFs present. A mismatch is reported as a warning, not a hard failure,
  because drivers can take extra time to create representors or may
  require the VFs to be rebound first -- this role does not retry or poll
  beyond the single `representor_settle_seconds` wait.
- **No automatic VF rebind.** If `require_vfs_unbound: true` caused VFs to
  be unbound, this role does not rebind them. Handle this via a vendor
  post-hook, a downstream role, or manually.
- **No automatic rollback.** If a mode change partially succeeds (e.g. the
  `devlink` command returns success but the driver silently stays in the
  previous mode), this role reports it as a warning but does not attempt
  to revert.
- **Devlink capability varies by vendor and firmware, not just by
  driver.** A device passing "detect devlink" and "show eswitch mode"
  does not guarantee the subsequent `set` will succeed -- some
  drivers/firmware reject the switchdev mode change outright, or require
  additional flags this role does not know about. See
  `docs/vendor-readiness/vendor-capability-matrix.md`.
- **No persistence across reboot.** This role only changes runtime state;
  re-applying it after every boot (e.g. via a systemd unit or re-running
  the playbook) is left to the operator. Unlike `sriov_setup`, this role
  does not currently generate a persistence unit.
- **PCI/PF name resolution is best-effort.** Reverse-resolving a PF netdev
  name from a PCI address relies on `/sys/bus/pci/devices/<addr>/net`
  existing at the time the role runs; if no driver is currently bound to
  the PF (unlikely, but possible), only the PCI address will be recorded.
