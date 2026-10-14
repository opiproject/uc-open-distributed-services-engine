# ovs_setup

Configures Open vSwitch for offload experiments: ensures the OVS service
is running, reconciles `other_config:hw-offload`, creates bridges and
ports (including switchdev representor ports), configures
`datapath_type`, and dumps `ovs-vsctl show` / `ovs-appctl dpctl/show` for
inspection after each run.

This role does not install OVS packages (see `ansible/roles/host_base`)
and does not itself configure switchdev/eswitch mode (see
`ansible/roles/switchdev_setup`, which typically runs before this role
when representor ports are involved).

## What this role does

1. **Ensures the OVS service is running.** Attempts to start/enable the
   systemd unit (unless `ovs_setup_manage_service: false`), then always
   performs a functional check (`ovs-vsctl show`) regardless of how the
   service is managed -- that check is what actually gates the rest of
   the role, since OVS lifecycle may not be a normal systemd unit on
   every host (e.g. inside a container on a DPU).
2. **Sets `other_config:hw-offload`** on the `Open_vSwitch` table to
   `true`/`false` based on `ovs_enable_hw_offload`, idempotently.
3. **Creates bridges** listed in `ovs_bridges` (`ovs-vsctl add-br`, if
   not already present).
4. **Adds ports and representors** listed in `ovs_ports` (`ovs-vsctl
   add-port`, plus interface type/options/tag/mtu reconciliation).
5. **Configures `datapath_type`** per bridge, if provided (per-bridge
   override or the global `ovs_datapath_type` default) -- left alone if
   not provided, so OVS's own default (`system`) applies.
6. **Dumps `ovs-vsctl show` and `ovs-appctl dpctl/show`** after
   configuration and displays both.

Every bridge and port is processed independently: one entry's failure
does not stop processing of the rest unless `ovs_setup_fail_fast: true`.
Results are additionally written to an Ansible local facts file (see
"Output" below).

## Requirements

- `ovs-vsctl` and `ovs-appctl` available and a running OVS instance (or
  one this role can start).
- Root privileges.
- For switchdev representor ports: `ansible/roles/switchdev_setup` should
  have already put the relevant PF into switchdev mode and the
  representor netdev must already exist.
- For DPDK (`netdev`) datapath / DPDK interface types: OVS must actually
  be built with DPDK support. This role does not verify that; setting
  `datapath_type=netdev` or `interface_type=dpdk` on an OVS build without
  DPDK support will fail at the `ovs-vsctl set` step with a clear error.

## Inputs

| Variable | Default | Description |
|---|---|---|
| `ovs_bridges` | `[]` | List of bridges to ensure exist. See schema below. |
| `ovs_enable_hw_offload` | `false` | Global `other_config:hw-offload` toggle. |
| `ovs_datapath_type` | `""` | Global default `datapath_type` for bridges that don't specify their own. Empty = leave at OVS default. Any string accepted (`system`, `netdev`, or vendor-specific). |
| `ovs_ports` | `[]` | List of ports/interfaces to ensure exist. See schema below. |
| `ovs_setup_fail_fast` | `false` | If true, fail the whole play on the first bridge/port error. |
| `ovs_setup_manage_service` | `true` | If false, skip starting/enabling a systemd unit (still runs the functional check). |
| `ovs_setup_restart_on_hw_offload_change` | `false` | If true, restart the OVS service when hw-offload actually changes. |
| `ovs_setup_service_name_by_os_family` | see `defaults/main.yml` | Per-OS-family service unit name map. |
| `ovs_setup_facts_dir` / `ovs_setup_facts_file_name` | `/etc/ansible/facts.d` / `ovs_setup.fact` | Output facts file location. |
| `ovs_setup_refresh_local_facts` | `true` | Re-gather `ansible_local` facts after writing, within the same play. |

### `ovs_bridges` schema

```yaml
ovs_bridges:
  # Plain string: bridge name, uses ovs_datapath_type.
  - br-int

  # Object form: per-bridge overrides.
  - name: br-phy
    datapath_type: netdev      # optional; overrides ovs_datapath_type
    fail_mode: secure          # optional: 'standalone' or 'secure'
    external_ids:              # optional
      odse-role: uplink
    set:                       # optional escape hatch, see below
      - "bridge br-phy other-config:hwaddr=00:11:22:33:44:55"
```

### `ovs_ports` schema

```yaml
ovs_ports:
  - bridge: br-int             # required
    port: eth1v0               # required
    interface_type: system     # optional, default 'system'
    options: {}                # optional dict -> options:<key>=<value>
    tag: 100                   # optional VLAN tag
    mtu_request: 9000           # optional
    set:                       # optional escape hatch, see below
      - "interface eth1v0 external-ids:odse-note=example"
```

The `set` list on both bridges and ports is an escape hatch: each string
is passed verbatim as `ovs-vsctl set <string>`, for anything not modeled
explicitly (vendor-specific columns, uncommon settings). Unlike the named
fields above, entries in `set` (and bridge `external_ids`) are applied on
every run and always reported as "changed", since generically diffing
arbitrary raw commands is impractical -- see "Known limitations".

## Output

### Console output

`ovs-vsctl show` and `ovs-appctl dpctl/show` output is displayed via
`debug` at the end of every run, regardless of whether anything changed.

### Facts file

Results are written as an Ansible custom facts file at
`{{ ovs_setup_facts_dir }}/{{ ovs_setup_facts_file_name }}` (default:
`/etc/ansible/facts.d/ovs_setup.fact`), exposed as `ansible_local.ovs_setup`
on subsequent fact gathers:

```json
{
  "hw_offload_enabled": true,
  "bridges": [
    {"name": "br-int", "datapath_type": "", "fail_mode": "secure", "valid": true, "error": null, "changed": true}
  ],
  "ports": [
    {"port": "eth1v0", "bridge": "br-int", "interface_type": "system", "options": {}, "valid": true, "error": null, "changed": true}
  ],
  "vsctl_show": ["...", "..."],
  "dpctl_show": ["...", "..."]
}
```

## Examples

### Kernel datapath (default `system` datapath, plain representor port)

The common case for TC/kernel-based hardware offload: a `system`
datapath bridge with a switchdev representor netdev added as a plain
kernel port. Assumes `ansible/roles/switchdev_setup` has already put the
PF into switchdev mode and `eth1v0` already exists as a representor.

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: ovs_setup
      vars:
        ovs_enable_hw_offload: true
        ovs_bridges:
          - br-int
        ovs_ports:
          - bridge: br-int
            port: eth1v0
            interface_type: system
```

### Userspace (DPDK) datapath

Requires an OVS build with DPDK support. `datapath_type: netdev` on the
bridge, and interfaces added with `interface_type: dpdk`, referencing the
underlying PCI device (or a DPDK representor devarg) via `options`.

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: ovs_setup
      vars:
        ovs_enable_hw_offload: true
        ovs_bridges:
          - name: br-phy
            datapath_type: netdev
        ovs_ports:
          - bridge: br-phy
            port: dpdk-p0
            interface_type: dpdk
            options:
              dpdk-devargs: "0000:03:00.0"
          - bridge: br-phy
            port: dpdk-vf0-representor
            interface_type: dpdk
            options:
              dpdk-devargs: "0000:03:00.0,representor=[0]"
```

### Vendor-specific backend placeholder

`ovs_datapath_type` and `interface_type` accept any string, so a vendor
OVS build/plugin that registers its own datapath or interface type can be
used without any change to this role. Replace `vendor-datapath-example`
and `vendor-iface-example` with the actual values documented by the
vendor's OVS integration -- this role does not validate or know about
them. See `docs/vendor-readiness/vendor-capability-matrix.md`, which
rates OVS hardware-offload integration as `discovery-required` or
`likely` (not confirmed) for each of the four initial vendor tracks.

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: ovs_setup
      vars:
        ovs_enable_hw_offload: true
        ovs_bridges:
          - name: br-vendor
            datapath_type: vendor-datapath-example   # placeholder -- confirm with vendor docs
        ovs_ports:
          - bridge: br-vendor
            port: vendor-port0
            interface_type: vendor-iface-example      # placeholder -- confirm with vendor docs
            options:
              vendor-option-example: "value"           # placeholder -- confirm with vendor docs
```

### Fail fast (e.g. CI / pre-flight checks)

```yaml
- hosts: dpu_hosts
  become: true
  roles:
    - role: ovs_setup
      vars:
        ovs_setup_fail_fast: true
        ovs_bridges:
          - br-int
```

## Known limitations

- **`set` escape hatch and bridge `external_ids` are not idempotency-checked.**
  These are applied via `ovs-vsctl set` on every run and always reported
  as "changed" in Ansible's bookkeeping, even if the value already
  matches -- diffing arbitrary raw commands generically is impractical.
  The other named fields (`datapath_type`, `fail_mode`, interface `type`,
  `options`, `tag`, `mtu_request`, `other_config:hw-offload`) are all
  read-compared-before-write and correctly idempotent.
- **No automatic bridge/port deletion or migration.** This role only
  creates/reconciles what is listed; it does not remove bridges/ports
  that are no longer listed, and refuses (rather than silently moving) a
  port that already belongs to a different bridge than requested.
- **hw-offload restart behavior varies by OVS version.** Some OVS
  versions apply `other_config:hw-offload` to newly-created ports without
  a restart; whether existing ports/flows pick it up without a restart is
  not verified by this role. `ovs_setup_restart_on_hw_offload_change` is
  provided but defaults to `false` since a restart is disruptive.
- **No verification that hardware offload is actually happening.** This
  role sets the configuration that enables offload; it does not inspect
  `ovs-appctl dpctl/dump-flows -m` or driver-level offload counters to
  confirm flows are actually being offloaded to hardware. That is left to
  a separate validation/telemetry step.
- **DPDK/vendor datapath and interface types are not validated in
  advance.** If the OVS build on the host lacks DPDK support, or a
  vendor-specific type/plugin isn't installed, the corresponding
  `ovs-vsctl set` command will fail with whatever error OVS itself
  produces; this role surfaces that error but does not pre-flight-check
  for build support.
- **Representor ports are treated as ordinary ports.** This role does not
  special-case "representor" as a distinct concept -- from OVS's point of
  view a representor netdev (kernel datapath) or a DPDK representor
  devarg (userspace datapath) is just another interface. Correctness
  depends on `ansible/roles/switchdev_setup` (or equivalent) having
  already made the representor exist.
