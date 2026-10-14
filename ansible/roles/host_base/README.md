# host_base

Prepares a Linux host for DPU/IPU offload experiments: checks
virtualization and IOMMU readiness, and installs baseline, networking,
QEMU/KVM+libvirt, Open vSwitch, and OVN packages — across multiple Linux
distribution families, using variables and conditionals rather than
assuming a single distro.

This role is intended to run **before** `ansible/roles/sriov_setup`,
`ansible/roles/switchdev_setup`, `ansible/roles/ovs_setup`, and
`ansible/roles/ovn_setup` in a playbook, per the sequencing described in
[`docs/architecture/overview.md`](../../../docs/architecture/overview.md)
§8. It does not itself configure SR-IOV VFs, switchdev mode, OVS bridges,
or OVN — those are deliberately out of scope here and belong to the roles
above.

> **Conservative by design.** This role checks and warns by default; it
> does not fail the play on missing virtualization/IOMMU support unless
> explicitly configured to. Distro coverage for RedHat- and Debian-family
> hosts is more complete than for other families — see
> [Distro coverage](#distro-coverage) below, and treat any gap flagged as
> a "placeholder" in `defaults/main.yml` as **unverified**, not wrong.

## What this role does

1. **Checks virtualization support** — CPU flags (`vmx`/`svm`) and
   `/dev/kvm` presence (`tasks/checks_virtualization.yml`).
2. **Checks the IOMMU kernel command line** — verifies vendor-appropriate
   IOMMU parameters (`intel_iommu=on`/`amd_iommu=on` plus `iommu=pt`) are
   present on `/proc/cmdline` (`tasks/checks_iommu.yml`). Non-x86
   platforms (e.g. aarch64/SMMU) are explicitly flagged as an
   unimplemented placeholder rather than silently skipped or guessed at.
3. **Installs baseline packages** (`curl`, `vim`, `tar`, `chrony`, etc.).
4. **Installs networking tools**: `iproute2`, `ethtool`, `pciutils`,
   `jq`, `python3`.
5. **Installs QEMU/KVM and libvirt packages.**
6. **Installs Open vSwitch and OVN packages.**
7. **Optionally configures 2MB hugepages** via a persistent sysctl
   drop-in (disabled by default).
8. **Starts and enables libvirtd**, if requested.
9. **Starts and enables Open vSwitch**, if requested.

Every step above is individually toggleable — see
[Role variables](#role-variables).

## Requirements

- Ansible 2.14+.
- No external collections required — every task uses only
  `ansible.builtin` modules, deliberately, to minimize dependencies.
- Target hosts must be reachable and running a Linux distribution; this
  role does nothing useful on non-Linux targets.
- Sudo/root privileges on the target host (package installation, sysctl,
  and service management all require elevated privileges in the normal
  case).

## Role variables

All variables live in `defaults/main.yml` and are safe to override at the
play, inventory, or `group_vars`/`host_vars` level.

### Feature toggles

| Variable | Default | Description |
|---|---|---|
| `host_base_check_virtualization` | `true` | Run the virtualization support check. |
| `host_base_fail_on_missing_virtualization` | `false` | Fail the play (instead of only warning) if virtualization support is not detected. |
| `host_base_check_iommu` | `true` | Run the IOMMU kernel command-line check. |
| `host_base_fail_on_missing_iommu` | `false` | Fail the play (instead of only warning) if expected IOMMU parameters are missing. |
| `host_base_install_baseline_packages` | `true` | Install the baseline package group. |
| `host_base_install_network_tools` | `true` | Install `iproute2`/`ethtool`/`pciutils`/`jq`/`python3`. |
| `host_base_install_kvm` | `true` | Install QEMU/KVM and libvirt packages. |
| `host_base_install_ovs` | `true` | Install Open vSwitch packages. |
| `host_base_install_ovn` | `true` | Install OVN packages. |
| `host_base_configure_hugepages` | `false` | Configure 2MB hugepages via sysctl. Opt-in, since it changes host memory allocation. |
| `host_base_start_libvirtd` | `true` | Start and enable the libvirtd service. |
| `host_base_start_openvswitch` | `true` | Start and enable the Open vSwitch service. |

### Package installation behavior

| Variable | Default | Description |
|---|---|---|
| `host_base_package_state` | `present` | Passed to the `package` module's `state` for every group (e.g. set to `latest` to always upgrade). |

### IOMMU expectations

| Variable | Default | Description |
|---|---|---|
| `host_base_iommu_params_by_vendor` | see `defaults/main.yml` | Dict keyed by detected CPU vendor (`intel`, `amd`) listing expected kernel command-line parameters. No `unknown` key is defined on purpose — unrecognized vendors fall back to an explicit placeholder message instead of a guess. |

### Distro-aware package/service name maps

Each of the following is a dict keyed by `ansible_facts.os_family`
(`RedHat`, `Debian`, `Suse`), with a `default` fallback key used (and
flagged via a warning) for any other/unrecognized OS family:

| Variable | Covers |
|---|---|
| `host_base_baseline_packages_by_family` | Baseline packages. |
| `host_base_network_tools_packages_by_family` | Networking tools. |
| `host_base_kvm_packages_by_family` | QEMU/KVM and libvirt. |
| `host_base_ovs_packages_by_family` | Open vSwitch. |
| `host_base_ovn_packages_by_family` | OVN. |
| `host_base_libvirtd_service_name_by_family` | libvirtd service name. |
| `host_base_openvswitch_service_name_by_family` | Open vSwitch service name. |

### Hugepages (optional)

| Variable | Default | Description |
|---|---|---|
| `host_base_hugepage_size_kb` | `2048` | Hugepage size in KB. **Only `2048` (2MB) is actually configured** by this role; any other value produces an explicit placeholder message explaining that larger sizes need kernel command-line parameters and a reboot instead. |
| `host_base_hugepage_count` | `0` | Number of hugepages to reserve. `0` (the default) configures nothing. |

## Distro coverage

| OS family | Baseline | Network tools | KVM/libvirt | OVS | OVN | Confidence |
|---|---|---|---|---|---|---|
| `RedHat` (RHEL/CentOS/Fedora/Rocky/Alma) | Defined | Defined | Defined | Defined | Defined (placeholder naming) | Package *names* are standard; **OVN package availability/naming still needs confirmation per release** — see the comment in `defaults/main.yml`. |
| `Debian` (Debian/Ubuntu) | Defined | Defined | Defined | Defined | Defined (placeholder naming) | Same caveat as above for OVN. |
| `Suse` (openSUSE/SLES) | Defined (placeholder) | Defined (placeholder) | Defined (placeholder) | Empty (placeholder) | Empty (placeholder) | **Not verified on real hardware by this project.** Confirm every package name before relying on this for SUSE. |
| Anything else | `default` fallback (mostly empty lists) | `default` fallback (non-empty, minimal) | Empty | Empty | Empty | **Unverified.** A warning is emitted at runtime for any OS family not explicitly listed. |

This table should be updated whenever a distro/package combination is
actually exercised and confirmed — do not remove a "placeholder" label
without a corresponding test result (see `ci/molecule/`).

## Example playbook

```yaml
- name: Prepare hosts for DPU/IPU offload experiments
  hosts: dpu_capable_hosts
  become: true
  roles:
    - role: host_base
```

### Checks only, no package changes (dry assessment of a fleet)

```yaml
- name: Assess host readiness without changing anything
  hosts: candidate_hosts
  become: true
  roles:
    - role: host_base
      vars:
        host_base_install_baseline_packages: false
        host_base_install_network_tools: false
        host_base_install_kvm: false
        host_base_install_ovs: false
        host_base_install_ovn: false
        host_base_start_libvirtd: false
        host_base_start_openvswitch: false
```

### Strict mode: fail the play if virtualization/IOMMU are not ready

```yaml
- name: Provision a host that must already be offload-ready
  hosts: production_dpu_hosts
  become: true
  roles:
    - role: host_base
      vars:
        host_base_fail_on_missing_virtualization: true
        host_base_fail_on_missing_iommu: true
```

### Enable 2MB hugepages

```yaml
- name: Prepare hosts with hugepages for DPDK/OVS-DPDK workloads
  hosts: dpdk_hosts
  become: true
  roles:
    - role: host_base
      vars:
        host_base_configure_hugepages: true
        host_base_hugepage_count: 2048   # 2048 x 2MB = 4GB reserved
```

### Skip OVN packages (host will only run OVS, no OVN chassis)

```yaml
- name: Prepare an OVS-only host
  hosts: ovs_only_hosts
  become: true
  roles:
    - role: host_base
      vars:
        host_base_install_ovn: false
```

## Known limitations

- **IOMMU/virtualization checks are read-only assessments, not
  remediation.** This role reports gaps; it does not modify bootloader
  configuration or firmware settings. Closing a gap (e.g. adding
  `intel_iommu=on` to GRUB) is a separate, host-specific change outside
  this role's scope, followed by a reboot and a re-run to verify.
- **1GB (or other non-2MB) hugepages are not configured**, only reported
  as a placeholder — see `tasks/hugepages.yml`.
- **OVN package naming/availability is the least-verified package group**
  across all covered distro families — confirm against your actual target
  release before depending on it.
- **SUSE support is a placeholder across every package group.** It has
  not been exercised against real SUSE hosts by this project.
- **No support for immutable/image-based distros** (e.g. Fedora CoreOS,
  Flatcar) is implemented or claimed.

## Testing

Role-level testing is intended to live under `ci/molecule/` (per the
top-level repository layout) with one scenario exercising this role
against each supported OS family container/image. No Molecule scenario
exists for this role yet — see `ci/molecule/README.md`.
