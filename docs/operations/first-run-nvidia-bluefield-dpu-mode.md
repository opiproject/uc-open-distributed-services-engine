# First-run runbook: NVIDIA BlueField DPU mode

If you have not run ODSE yet, start at
[`../getting-started.md`](../getting-started.md). Conditional flowchart
(and the OPI answer): [`user-doc-flow.md`](user-doc-flow.md). This page is
the NVIDIA hardware-adjacent follow-on.

Status: Draft  
Scope: First hardware-adjacent dry-run of the VM-to-VM lab on **NVIDIA
BlueField in DPU mode**. Placeholders only — replace every `<ANGLE>`
value from discovery output before `--apply`.  
Companions: [`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md),
[`labctl-usage.md`](labctl-usage.md),
[`preflight-readiness.md`](preflight-readiness.md),
[`dpu-remote-execution.md`](dpu-remote-execution.md),
[`nvidia-bluefield-dpu-bringup.md`](nvidia-bluefield-dpu-bringup.md),
[`dpu-side-vf-to-representor-mapping.md`](dpu-side-vf-to-representor-mapping.md),
[`iommu-vfio-host-setup.md`](iommu-vfio-host-setup.md),
[`cleanup-and-rollback.md`](cleanup-and-rollback.md)

**Before filling `nvidia.datapath`/`ovs.datapath_type` for real**, see
[`nvidia-bluefield-dpu-bringup.md`](nvidia-bluefield-dpu-bringup.md) —
it covers preparing the BlueField Arm side (BFB/DOCA image) and exactly
how to confirm both OVS-Kernel and OVS-DOCA datapath types are actually
available before trusting either one to work under `--apply`.

This profile is **`nvidia-bluefield-dpu-mode`**. OVS / OVS-DOCA runs on
the **DPU** (BlueField Arm OS). Host OVS is **not** the forwarding
owner. The host owns VM lifecycle and VF passthrough. The DPU owns
representors and forwarding.

Copy `config/lab-nvidia-vm2vm.example.yaml` to
`config/lab-nvidia-vm2vm.yaml` and fill placeholders before any
destructive stage. Default remains dry-run until
`safety.dry_run_default: false` **and** `--apply` **and** the apply
readiness gate.

---

## 1. Objective

Run the vDSE-like path on one server and one BlueField:

two SR-IOV VFs → two KVM/QEMU VMs (VFIO passthrough) → DPU-side
representors on a DPU OVS/OVS-DOCA bridge → VM-to-VM traffic → DPU-side
offload evidence → safe cleanup.

Do not treat Kind, a demo ping, or host `ovs-vsctl` as hardware
certification or DPU offload proof.

## 2. Assumptions

- BlueField is in **DPU mode** (not NIC/host-switchdev).
- You can SSH to the DPU Arm OS (`dpu.management_ip`, `dpu.ssh_user`).
- Host has KVM/QEMU/libvirt and will **not** attach representors to
  host OVS for this profile.
- Config file is a filled copy of the NVIDIA example, not the example
  itself, before `--apply`.
- Placeholders below are documentation ranges / names, not inventory.

## 3. Safety warnings

- Default is dry-run. `--apply` is refused unless
  `safety.dry_run_default: false` and the apply readiness gate passes
  (`APPLY_BLOCKED_BY_READINESS_GATE`).
- `--force` is cleanup-only. It is refused for forwarding / OVS setup.
- Never change `host.management_interface` (`<MGMT_IFACE>`).
- Do not enable `host_forwarding_control` or
  `nvidia.allow_host_ovs_for_forwarding` in this profile.
- Do not delete an OVS bridge unless this run marked it `lab_owned`.
- Unknown facts stay `null`. Never invent `offloaded:yes`.

## 4. Required config profile

```yaml
vendor:
  type: nvidia-bluefield
  profile: nvidia-bluefield-dpu-mode

nvidia:
  mode: dpu
  ovs_location: dpu
  representor_location: dpu
  datapath: ovs-doca          # OVS-DOCA only if DPU ovs-vsctl lists datapath_type=doca
  allow_host_ovs_for_forwarding: false

host_forwarding_control:
  enabled: false

dpu_forwarding_control:
  enabled: true

remote_execution:
  enabled: true
  target: dpu
  dry_run_default: true
```

Filled operator file: `config/lab-nvidia-vm2vm.yaml`  
Example (placeholders): `config/lab-nvidia-vm2vm.example.yaml`  
Profile: `config/profiles/nvidia-bluefield-dpu-mode.yaml`

Host-switchdev is an **advanced non-default** profile
(`nvidia-connectx-host-switchdev`). Do not select it for this first run.

## 5. Host responsibilities

- KVM / QEMU / libvirt
- VF creation on the host PF (`<PF_NAME>` / `<PF_PCI>`) when SR-IOV is
  host-side
- VFIO passthrough of VF PCI addresses into VMs
- Guest lifecycle (`create-vms`, `attach-vfs`, guest SSH for IPs/traffic)
- **Not** OVS forwarding ownership in this profile

## 6. DPU responsibilities

- Representor owner (`pf0vf0` / `pf0vf1` style names from DPU `ip`/`devlink`)
- OVS / OVS-DOCA owner (`ovs.bridge: br-int` unless you set another name)
- Forwarding owner (flows on the DPU bridge)
- Offload evidence source (`ovs-vsctl` / `dpctl` / optional `tc`/`ethtool`
  **on the DPU**)

## 7. What must not happen

- Host OVS owning VF representors for this profile
- Treating `eth0v1` / `enp4s0f0v0` / `pf0sf0` as VF representors
- Copying ConnectX host-switchdev / OVS-TC onto this DPU-mode path
- Claiming `offloaded:yes` from host `ovs-vsctl` or from empty planned probes
- `--apply` before a dry-run plan for that same stage and run id

## 8. Preflight command

```bash
python tools/preflight_readiness.py --config config/lab-nvidia-vm2vm.yaml --json
```

Expect `READY_FOR_DRY_RUN_ONLY` until placeholders and
`safety.dry_run_default` are replaced. `NOT_READY` means stop.

## 9. Validate config command

```bash
python tools/labctl.py vm2vm validate-config \
  --config config/lab-nvidia-vm2vm.yaml --json
```

## 10. Discovery command

```bash
python tools/labctl.py vm2vm discover \
  --config config/lab-nvidia-vm2vm.yaml --json
```

## 11. DPU-side OVS validation command

```bash
python tools/labctl.py vm2vm prepare-ovs \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
```

This plans DPU `ovs-vsctl` via `RemoteCommandRunner`. Host `ovs-vsctl`
is not the DPU datapath. OVS-DOCA is used only if `doca` is in DPU
`datapath_types`.

## 12. DPU-side representor mapping command

```bash
python tools/labctl.py vm2vm discover-representors \
  --config config/lab-nvidia-vm2vm.yaml --json
```

Writes `representor-map.json`. High-confidence NVIDIA mapping is
`devlink flavour=pcivf` agreeing with portname `pf0vf<N>` on the DPU.

## 13. VF creation command

```bash
python tools/labctl.py vm2vm prepare-vfs \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
# only after the apply readiness gate would pass. sriov_numvfs is a
# root-owned sysfs file; labctl does not elevate privileges itself, so
# --apply here needs sudo (see "A fourth, non-labctl gate: OS file
# permissions" in labctl-usage.md):
sudo python tools/labctl.py vm2vm prepare-vfs \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

## 14. VM creation command

```bash
python tools/labctl.py vm2vm create-vms \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
python tools/labctl.py vm2vm create-vms \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

Images: `<VM_IMAGE>`. Hostdevs omitted while `vf_pci` is still unknown.

## 15. VM verification command

```bash
python tools/labctl.py vm2vm verify-vms \
  --config config/lab-nvidia-vm2vm.yaml --json
```

Read-only; always runs its checks for real regardless of `--dry-run`/
`--apply` (there is nothing to plan or mutate). `virsh start`'s
captured result from step 14 can be stale or misleading — this stage
re-queries `virsh domstate` fresh right now and independently probes
SSH on each VM's management NIC using the key already provisioned into
the guest image (see [`vm-image-preparation.md`](vm-image-preparation.md)).
Check `all_ready: true` in the output before attaching a VF in the next
step; the apply readiness gate for step 16 (`attach-vfs --apply`) also
hard-requires that `verify-vms.json` exists for this run (though not
that it fully passed — you may still proceed deliberately with a
`false` verdict, e.g. if SSH isn't wired up yet but you trust domstate
alone). If a VM reports `running: false` or `ssh_reachable: false`
because it is still booting, just re-run this command after a few
seconds — it is a single check, not a poll loop.

## 16. VF assignment command

```bash
python tools/labctl.py vm2vm attach-vfs \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
python tools/labctl.py vm2vm attach-vfs \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

## 17. Configure forwarding command

```bash
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
python tools/labctl.py vm2vm configure-forwarding \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

Requires `discover-representors`. `--force` is **not** valid here.

## 18. Traffic command

Like every other stage, `configure-vm-ips` and `run-traffic` default to
dry-run (planned SSH argv only, `"executed": false`) unless you pass
`--apply`. They are not in `DESTRUCTIVE_STAGES` (no apply-readiness-gate
check, since there is nothing irreversible to guard against — worst case
you re-run them), but that does **not** mean `--apply` is optional if you
actually want IPs assigned and traffic sent:

```bash
# dry-run: plan only, nothing touches the guests
python tools/labctl.py vm2vm configure-vm-ips \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
python tools/labctl.py vm2vm run-traffic \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json

# apply: actually SSH into the guests, assign IPs, and send ping/iperf3 traffic
python tools/labctl.py vm2vm configure-vm-ips \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
python tools/labctl.py vm2vm run-traffic \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

Guest IPs (placeholders): `198.51.100.11/24` and `198.51.100.12/24`.

## 19. Offload validation command

```bash
python tools/labctl.py vm2vm validate-offload \
  --config config/lab-nvidia-vm2vm.yaml --json
```

No `--apply`/`--dry-run` flag here, and that is intentional (not a
doc gap like step 18 used to be): `validate-offload` only runs
read-only inspection commands (`ovs-vsctl show`, `dpctl/show`,
`dpctl/dump-flows`, `ovs-ofctl dump-flows`, `tc -s filter show`,
`ethtool -S`) — nothing it does is destructive, so there is no
apply-readiness gate to satisfy and labctl's own `--apply` flag would
have no effect either way.

In DPU mode this passes `--location dpu` to
`ovs_offload_checker.py`. Host OVS output is not DPU offload proof.
Whether the DPU-side probes above are actually sent over SSH (vs.
planned-only, `"planned": true`, no `stdout`) is controlled by
**`remote_execution.dry_run_default`** in your lab YAML — the same
knob you already set to `false` for `discover-representors` /
`configure-forwarding`. With it `false`, `validate-offload` makes up
to `4 + 2 * <number of interfaces>` sequential real SSH round trips to
the DPU; `labctl` sizes its own subprocess timeout the same way it
does for `discover-representors` (`dpu.command_timeout_seconds`
per call, see
[`dpu-remote-execution.md`](dpu-remote-execution.md)), so a slow
management link needs that one config knob raised, not a code change.
See [`../test-plan/offload-validation.md`](../test-plan/offload-validation.md).

## 20. Report generation command

```bash
python tools/labctl.py vm2vm collect-evidence \
  --config config/lab-nvidia-vm2vm.yaml --json
python tools/report_generator.py \
  --input-dir tests/results/nvidia-bluefield/<RUN_ID>/ \
  --output result-summary.md
```

## 21. Cleanup command

```bash
python tools/labctl.py vm2vm cleanup \
  --config config/lab-nvidia-vm2vm.yaml --dry-run --json
python tools/labctl.py vm2vm cleanup \
  --config config/lab-nvidia-vm2vm.yaml --apply --json
```

`--force` may skip the dry-run-plan check for **cleanup only** (test
leftovers), never for forwarding setup. Cleanup tears down **DPU** OVS
objects this run owned; it does not touch host OVS forwarding or
`<MGMT_IFACE>`.

## 22. Expected result files

Under `tests/results/nvidia-bluefield/<RUN_ID>/`:

| File | Stage |
|---|---|
| `run-metadata.json` | every invocation |
| `validate-config.json` | validate-config |
| `discover.json` | discover |
| `prepare-vfs.json` | prepare-vfs |
| `representor-map.json` | discover-representors |
| `prepare-ovs.json` | prepare-ovs |
| `create-vms.json` | create-vms |
| `verify-vms.json` | verify-vms |
| `attach-vfs.json` | attach-vfs |
| `configure-forwarding.json` | configure-forwarding |
| `run-traffic.json` | run-traffic |
| `offload-validation.json` | validate-offload |
| `collect-evidence.json` | collect-evidence |
| `cleanup.json` | cleanup |
| `apply-readiness-gate.json` | written when `--apply` is blocked |

## 23. Failure triage matrix

| Symptom | Likely cause | What to do |
|---|---|---|
| `APPLY_BLOCKED_BY_READINESS_GATE` | Missing validate-config, hash change, no dry-run plan, DPU SSH fields, or `--force` on forwarding | Read `failed_checks` in the JSON; dry-run the same stage first |
| `Invalid NVIDIA DPU-mode configuration` | Host OVS forwarding enabled or `ovs_location` not `dpu` | Restore `nvidia-bluefield-dpu-mode` fields |
| `STAGE_MISSING_DEPENDENCY` | `configure-forwarding` without representor map | Run `discover-representors` on this `--run-id` |
| `UNKNOWN_BRIDGE_REFUSED` | Cleanup would delete a bridge this run did not own | Set `ovs.lab_owned` / use this run's `prepare-ovs.json` |
| Offload `unknown` / no `offloaded:yes` | Probes planned only, or host OVS inspected by mistake | Confirm `location: dpu` in `offload-validation.json`; inject real DPU samples on hardware |
| Representor `eth0v1` / low confidence | Wrong netdev class or PF1 name used as VF0 | Require high-confidence `pf0vfN` on the DPU |
| `create-vms`/`attach-vfs` JSON shows `"iommu_vfio": {"status": "NOT_READY", ...}` | Kernel command line is missing `intel_iommu=on`/`amd_iommu=on iommu=pt` | See [`iommu-vfio-host-setup.md`](iommu-vfio-host-setup.md); fix and reboot **before** `prepare-vfs --apply` since VFs are not persisted across reboot |
| `prepare-vfs --apply` (or `prepare-switchdev --apply`) fails with `PermissionError: [Errno 13] Permission denied` | `sriov_numvfs` is a root-owned sysfs file; `labctl` writes it directly and never calls `sudo` itself | Re-run the same `--apply` command with `sudo` (`sudo -E env "PATH=$PATH" python3 ...` if using a venv); see "A fourth, non-labctl gate: OS file permissions" in [`labctl-usage.md`](labctl-usage.md) |
| `virsh define`/`create-vms --apply` fails: `Permission denied` opening the disk image | The actual `qemu-system-x86_64` process runs as a non-root user (`qemu`/`libvirt-qemu`, not root); it needs read + directory-traverse access to the whole backing-file chain, not just the top qcow2 | See "Backing-file base images: a different permission model than the top qcow2" in [`vm-image-preparation.md`](vm-image-preparation.md) |
| `create-vms --apply` fails: `operation failed: domain 'odse-vm2vm-vm1' already exists with uuid <other-uuid>` | A prior partial run already `virsh define`d this domain under a different UUID (e.g. a leftover from before `labctl`'s deterministic-UUID fix, or a run whose `start` failed on something unrelated like the base-image permission issue above). `virsh destroy`/`shutdown` only stop a *running* domain; neither removes the persisted definition, so the collision does not go away on its own | `create-vms` now handles this itself: it re-queries `virsh domstate` for the stale domain, and *only if confirmed not running* automatically `virsh undefine`s it and retries `virsh define` (see `_recover_from_define_uuid_collision()` in `tools/labctl.py`) — a plain `virsh destroy` (no manual `undefine` needed) is enough before re-running `create-vms --apply`. If the stale domain is still **running**, `labctl` deliberately leaves it alone and reports so in `vms[].error`; stop it yourself first (`virsh shutdown`/`virsh destroy`), then re-run. `labctl` also no longer calls `virsh start` after any `virsh define` failure it could not resolve — a `create-vms.json` entry with `error` set and `start: null` means the guest was **not** started this run, regardless of what an earlier, unrelated `virsh list` might show |
| `virsh list` shows a domain as `running` right after a `create-vms --apply` that logged a `virsh define` error | Fixed: this was `labctl` proceeding to `virsh start` against a domain definition it never actually confirmed (see previous row) — before this fix, a stale/mismatched pre-existing definition could get started while `define` silently failed. As of this fix, `create-vms` skips `start` entirely whenever `define` fails and could not be auto-recovered | Re-run `create-vms --apply` after resolving the `define` error (see previous row); check `vms[].start` in the JSON output — `null` means it was correctly skipped, an object means it actually ran |
| `verify-vms` (or `configure-vm-ips`) reports `GUEST_SSH_REQUIRED`: `management_ip not set and virsh domifaddr found no address` | The guest's management NIC never got a DHCP lease — almost always a guest-image gap, not a host/libvirt problem; `virsh domstate` correctly shows `running` | See "Management NIC gets no DHCP lease" in [`vm-image-preparation.md`](vm-image-preparation.md#management-nic-gets-no-dhcp-lease) |
| Console login for a cloud-init-built image is impossible (no password; SSH doesn't work either) | `user-data.template` sets `lock_passwd: true` + `ssh_pwauth: false` by design — if cloud-init never actually ran (see next row), there is no password *and* no SSH key, so there is no way in at all | `virsh shutdown`/`destroy` the VM, then `sudo virt-customize -a <qcow2> --root-password password:<temp>` to get a throwaway console login for diagnosis; re-lock with `passwd -l root` once done (see [`vm-image-preparation.md`](vm-image-preparation.md#cloud-init-never-ran-silently)) |
| Console shows `cloud-init status --long` → `status: disabled`, `boot_status_code: disabled_by_generator` | Approach 1 (cloud-init)'s one-time "boot with the seed ISO attached" step (see [`vm-image-preparation.md`](vm-image-preparation.md#approach-1-cloud-init)) was skipped or the CD-ROM never actually attached; cloud-init never ran even once, so no user/SSH-key/network config from `user-data` was ever applied — fails completely silently, no error anywhere | See "Cloud-init never ran (silently)" in [`vm-image-preparation.md`](vm-image-preparation.md#cloud-init-never-ran-silently) — redo the seed-ISO boot properly, or patch the existing qcow2 directly (`virt-customize`, or live over a console you already got in via the row above) |
| `apt-get install` (or equivalent) runs out of disk space on the guest | `--size` only grows the qcow2 *virtual disk*, not the base image's own (often much smaller) partition/filesystem inside it; growing that needs `growpart`+`resize2fs`/`xfs_growfs`, which for approach 1 depends on cloud-init having actually run (see the row above) | See "Not enough disk space to install packages" in [`vm-image-preparation.md`](vm-image-preparation.md#not-enough-disk-space-to-install-packages) — `sh qemu/images/guest-first-boot-growroot.sh` on an already-running guest, or just `growpart`/`parted` + `resize2fs` by hand |
| `configure-vm-ips --apply`/`run-traffic --apply` fail: `sudo: a terminal is required to read the password` | `guest-network-setup.sh` runs over a non-interactive SSH session (no tty); the guest's `ssh_user` either lacks a real NOPASSWD sudo grant, or a base-image default like `Defaults requiretty` forces a tty regardless of NOPASSWD — and `lock_passwd: true` means this user has no password to fall back on either way | See "`sudo: a terminal is required to read the password`" in [`vm-image-preparation.md`](vm-image-preparation.md#sudo-a-terminal-is-required-to-read-the-password) — add a scoped `/etc/sudoers.d/90-<ssh_user>-no-tty` drop-in to the already-running guest; new images built via `prepare-vm-image.sh` do this automatically now |
| `systemctl enable sshd` (or `ssh`) fails: `Failed to enable unit: Refusing to operate on alias name or linked unit file: sshd.service` | Usually harmless: on Debian/Ubuntu, `ssh.service` is the real unit and `sshd.service` is a compatibility alias (or vice versa on some distros); `systemctl enable`/`disable` refuse the alias name specifically, `start`/`stop`/`status` work fine either way | See "`systemctl enable sshd` fails" in [`vm-image-preparation.md`](vm-image-preparation.md) — check `systemctl is-active`/`is-enabled` on both names first; SSH may already be fine |
| `verify-vms`/`configure-vm-ips`/`run-traffic` report the guest unreachable, but `ssh -i <path> user@ip` works fine by hand | No `vms[].ssh_key_path` set in the lab config, so `labctl` falls back to ssh's *default* identity (agent/`~/.ssh/config`/`~/.ssh/id_*`) instead of the key you actually authorized into the guest | See "`verify-vms`/`configure-vm-ips`/`run-traffic` fail with 'Permission denied (publickey)'" in [`vm-image-preparation.md`](vm-image-preparation.md) — set `vms[].ssh_key_path` per VM to the working key path |
| `run-traffic --apply`'s `iperf3-client` command shows `"timed_out": true`, `"returncode": null`, even though `stdout` has real `[SUM] ... Gbits/sec` lines up to the cutoff | Fixed: the local SSH subprocess timeout used to default to `commands.DEFAULT_TIMEOUT_SECONDS` (30s) — the same value as `traffic.duration_seconds`'s own default (30) — so the client always got killed right as the real `-t 30` iperf3 test was finishing server-side, with no margin for SSH connect/auth or iperf3's own startup/summary-print overhead | Update to a build where `stage_run_traffic` sizes the client's timeout as `duration_seconds + 30`; the `timed_out: true` result you already have from before the fix is still a **real, successful** offload/throughput test (the `Gbits/sec` numbers in `stdout` are genuine) — just re-run `run-traffic --apply` if you want a clean, complete JSON capture with the final iperf3 summary and average throughput |
| `OVS_DOCA_MISSING` from `prepare-ovs`, or don't know how to confirm both datapath types are available | `datapath_types` on the DPU may only list `system`, or `doca` is listed but `doca_initialized` is still `false` | See [`nvidia-bluefield-dpu-bringup.md`](nvidia-bluefield-dpu-bringup.md) §0/§3 for the exact `ovs-vsctl list Open_vSwitch` check and activation commands |
| Preflight `READY_FOR_DRY_RUN_ONLY` | Example placeholders / `dry_run_default: true` | Expected until a filled lab YAML is used |
| SSH plan only, `executed: false` | Dry-run default | Do not treat planned argv as live DPU proof |

Operator control point remains `labctl.py`. Do not drive DPU OVS with
ad-hoc host `ovs-vsctl` for this profile.
