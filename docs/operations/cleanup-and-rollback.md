# Cleanup and rollback for the VM-to-VM single-DPU lab

Status: Draft
Scope: `tools/lab_cleanup.py`, invoked directly or via
`tools/labctl.py vm2vm cleanup`.
Companions: [`labctl-usage.md`](labctl-usage.md),
[`lab-runbook-vm2vm-single-dpu.md`](lab-runbook-vm2vm-single-dpu.md),
[`configure-forwarding.md`](configure-forwarding.md)

```bash
python tools/lab_cleanup.py --config config/lab-nvidia-vm2vm.yaml --dry-run
python tools/lab_cleanup.py --config config/lab-nvidia-vm2vm.yaml --apply

# Equivalent, via the single control point:
python tools/labctl.py vm2vm cleanup --config config/lab-nvidia-vm2vm.yaml --apply --json
```

When `nvidia.mode == dpu`, OVS teardown runs on the **BlueField Arm OS**
via `RemoteCommandRunner`. Host `ovs-vsctl` is not used for forwarding
cleanup. Host eswitch/OVS restart is skipped.

Safety:

- Never delete a bridge unless this run created it (`prepare-ovs.json`
  `lab_owned: true`) or `ovs.lab_owned: true`.
- Never remove a port unless it appears in this run's
  `configure-forwarding` / `discover-representors` evidence (or
  `ovs.ports`).
- Unknown bridge → `UNKNOWN_BRIDGE_REFUSED`.
- Dry-run by default. Do not touch `host.management_interface`.


## 1. What cleanup does

Ten stages, always run in this order (several are read-only or
best-effort and never abort the rest of the run if they fail):

| # | Stage | What it does |
|---|---|---|
| 1 | `stop_traffic_tools` | SSHes into each VM's management NIC and `pkill -f iperf3`. Skipped per-VM if the management host can't be resolved. |
| 2 | `stop_vms` | `virsh destroy <vm>` (stop). `virsh undefine <vm>` only with `--undefine-vms`. |
| 3 | `detach_vfs` | `virsh detach-device` the VF hostdev interface, if `vms[].vf_pci` is known. |
| 4 | `restore_vf_driver` | Checks each VF's current PCI driver; rebinds from `vfio-pci` back to the kernel's default driver only if it is still bound to `vfio-pci` (libvirt's `managed='yes'` normally does this automatically on detach/shutdown). |
| 5 | `remove_ovs_ports` | `ovs-vsctl del-port` for every representor/static port this lab is known to have added (from the same run's `configure-forwarding.json`/`discover-representors.json`, plus `ovs.ports`). |
| 6 | `delete_ovs_bridge` | `ovs-vsctl del-br` for `ovs.bridge`. |
| 7 | `reset_sriov_numvfs` | Writes `0` to `sriov_numvfs` for `dpu.pf_name` — **only** if a matching prior `prepare_vfs.json` state file exists (see §3 safety rule below). |
| 8 | `restore_switchdev_legacy` | **Opt-in** (`--restore-switchdev-legacy`): `devlink dev eswitch set pci/<pf_pci> mode legacy`, only if `dpu.enable_switchdev: true`. |
| 9 | `restart_ovs` | **Opt-in** (`--restart-ovs`): `systemctl restart openvswitch-switch` (falls back to `openvswitch`). |
| 10 | `collect_final_state` | Read-only `dpu_inventory.py` + `ovs-vsctl show`, always run regardless of `--dry-run`/`--apply`, so you can see what is actually left afterward. |

Every stage's command(s), plus a `dry_run`/`skipped`/`message` field,
are recorded in the JSON report — nothing is silently no-op'd without
being visible in the evidence.

## 2. What cleanup does not guarantee

- **It does not delete VM disk images.** `stop_vms` only stops (and,
  with `--undefine-vms`, undefines the libvirt domain); the qcow2/raw
  file under `vms[].image` is never touched. Delete it yourself if you
  want the disk gone.
- **It is not fully reversible.** Removing OVS ports/bridges and
  resetting `sriov_numvfs` are one-way operations from this tool's
  point of view; getting back to a working lab means re-running the
  earlier `labctl` stages (`prepare-vfs`, `prepare-switchdev`,
  `discover-representors`, `prepare-ovs`, `configure-forwarding`), not
  "undoing" cleanup.
- **`reset_sriov_numvfs` refuses more often than you might expect.**
  It only runs if a `prepare_vfs.json` exists in the same run
  directory recording that this run's `labctl vm2vm prepare-vfs`
  configured this PF (see §3). If you point `--run-id` at a run that
  never got that far, or a config file with a different `dpu.pf_pci`
  than what is recorded there, this stage is skipped, not forced.
  This is a **safety floor, not a full identity guarantee**: today's
  vendor enablement backends (`plugins/<vendor>/backend.py`) are
  stubs and do not yet independently confirm PF identity from
  hardware state — the check is limited to comparing the current
  `--config` against what a prior `labctl` run recorded.
- **`detach_vfs`/`restore_vf_driver` are best-effort.** They act only
  when `vms[].vf_pci` is known and the VF is actually still bound to
  `vfio-pci`; if `virsh detach-device` fails (e.g. the exact device
  XML libvirt tracks differs from the minimal fragment this tool
  reconstructs from `vf_pci`), the failure is recorded but does not
  stop the rest of cleanup. See §3 for manual recovery.
- **It never touches `host.management_interface` or
  `host.preserve_interfaces`.** Every stage that manipulates a named
  netdev (representor removal, bridge deletion, `sriov_numvfs` reset)
  checks the interface/bridge/PF name against
  `plugins.base.vm2vm_config.preserved_interfaces()` first and refuses
  if it matches — the same rule `labctl.py` enforces everywhere else.
- **`stop_traffic_tools` only reaches VMs it can SSH into.** If the
  management host can't be resolved (no `management_ip`, no `virsh
  domifaddr` lease), that VM's traffic tools are left running; check
  manually if this matters (e.g. before deleting the VM disk).
- **Stages 8 and 9 are opt-in and host-wide in effect.** `--restart-ovs`
  restarts the OVS service for the *entire host*, not just this lab's
  bridge — do not use it on a host running other OVS-managed traffic.

## 3. Manual recovery

### Failed VF assignment (VM won't start, or VF stuck attached)

1. Check the VF's current driver: `ls -l
   /sys/bus/pci/devices/<vf_pci>/driver`. If it points at `vfio-pci`
   and no VM should be using it, rebind manually:
   ```bash
   echo <vf_pci> > /sys/bus/pci/devices/<vf_pci>/driver/unbind
   echo <vf_pci> > /sys/bus/pci/drivers_probe
   ```
   (This is exactly what `restore_vf_driver` automates — run it by
   hand if the automated stage reported a failure.)
2. If `virsh` still shows the hostdev attached to a domain that won't
   start cleanly:
   ```bash
   virsh destroy <vm-name>          # force stop
   virsh dumpxml <vm-name>          # inspect the exact hostdev entry
   virsh detach-device <vm-name> /path/to/exact-hostdev.xml --config --live
   ```
   Use the domain's *own* `dumpxml` output for the detach XML rather
   than hand-reconstructing it — this sidesteps the exact limitation
   noted in §2 for `detach_vfs`.
3. If the domain definition itself is corrupt, `virsh undefine
   <vm-name>` and re-run `labctl vm2vm create-vms` (needs
   `vms[].vf_pci` resolved again via `discover-representors`).

### Manually deleting an OVS bridge

```bash
ovs-vsctl list-ports br-int        # see what is still attached
ovs-vsctl del-port br-int <port>   # repeat per port if del-br refuses
ovs-vsctl del-br br-int
```
`ovs-vsctl del-br` on a bridge with active ports usually still
succeeds (it removes the ports with it), but delete ports first if you
want to confirm nothing unexpected is attached before the bridge goes
away.

### Restoring `sriov_numvfs`

To recreate the VFs after a reset (or if you need a different count
temporarily):

```bash
cat /sys/class/net/<pf_name>/device/sriov_totalvfs   # max supported
echo <N> > /sys/class/net/<pf_name>/device/sriov_numvfs
```

`N` must be `>= 2` for this lab's use case (`dpu.num_vfs` in the
config). After recreating VFs, re-run `labctl vm2vm prepare-switchdev`
and `discover-representors` — VF-to-representor mappings are not
guaranteed to be identical after a fresh `sriov_numvfs` cycle.

### Switchdev-to-legacy, carefully

Returning a PF from switchdev to legacy eswitch mode
(`--restore-switchdev-legacy`) is destructive to more than just this
lab if the PF is shared:

1. Confirm no other consumer (another VM, another OVS bridge, a
   different test) is relying on switchdev/representors on this PF
   before running it.
2. Remove OVS ports and the bridge **first** (stages 5–6) — switching
   eswitch mode while representors are still attached to a bridge can
   leave OVS/the kernel in an inconsistent state depending on
   driver/firmware version.
3. Run the mode change:
   ```bash
   devlink dev eswitch set pci/<pf_pci> mode legacy
   ```
4. Verify: `devlink dev eswitch show pci/<pf_pci>` should report
   `mode legacy`. Representor netdevs for this PF's VFs will disappear
   once the mode change takes effect.
5. To go back to switchdev later, re-run `labctl vm2vm
   prepare-switchdev` (which requests `mode switchdev` via the vendor
   backend) rather than calling `devlink` by hand, so the same
   discovery/mapping evidence trail is captured.

## 4. Interpreting `cleanup-summary.json`

Every stage entry has either a `command`/`vms`/`results` payload (ran,
even if dry-run), or `"skipped": true` with a human-readable
`"message"` explaining exactly why (missing config field, no matching
prior state file, matches a preserved interface, opt-in flag not
passed). Before trusting a cleanup run as "complete," check:

- `stages.reset_sriov_numvfs` — if `skipped`, the PF's VFs are still
  present; that's often intentional (you didn't pass a matching
  `--run-id`) but confirm it wasn't a mistake.
- `stages.collect_final_state.dpu_inventory` and
  `.ovs_vsctl_show` — the actual post-cleanup state, independent of
  what the earlier stages *attempted*. A `del-br` that "succeeded" in
  dry-run terms tells you nothing about the real host; only stage 10's
  live inventory does.
- `rollback_risks` at the top level — printed to stderr before an
  `--apply` run executes anything destructive, and always present in
  the JSON for later review even in dry-run.
