# DH4 ACC bring-up: hands-on triage session

The step-by-step companion to
[`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) §6.1.2.

Everything here runs on the **IMC** (`ssh root@172.22.4.4`, prompt
`[root@ipu-imc ~]#`), not the host and not the ACC.

**Status:** sessions 0–26B are complete. Stop here.

The ACC Fedora 43 kernel runs (console, `efi=noruntime`, dracut). It
never remounts iSCSI root, so `fn_id 4` never appears. Further
initramfs/BLS surgery is not justified: this ostree image is not
Intel's ACC OS, and session 26B's `idpf` inject + `net-lib.sh` stub
did not produce a Linux iSCSI login. Live log:
[`../../example-output/acc-dh4-triage-sessions-output.txt`](../../example-output/acc-dh4-triage-sessions-output.txt).

## A. What sessions 0 and 1 established

### A.1 The ACC kernel command line is malformed

`/mnt/imc/acc_variable/acc-config.json` on DH4 contains:

```
"boot_params" : "... root=/dev/sda rw netroot=iscsi:192.168.0.1::::iqn.e2000:acc initrdmem=, acpi=force"
```

`initrdmem=,` has an empty base and an empty size. The template that
generates it, in `/usr/bin/scripts/set_acc_kernel_cmdline.sh`, is:

```sh
bmd_data=$(/usr/bin/bmd_conf -p | grep -A 3 "ARM_MEMORY_IMAGE_ID_INITRD_IMG")
initrd_base=$(grep "\s*base = 0x[[:xdigit:]+]" <<< "${bmd_data}" | awk '{print $3}')
initrd_size=$(grep "\s*size = 0x[[:xdigit:]+]" <<< "${bmd_data}" | awk '{print $3}')
CMD_LINE="... initrdmem=${initrd_base},${initrd_size} acpi=force"
```

Both variables came out empty, which means `bmd_conf -p` had no
`ARM_MEMORY_IMAGE_ID_INITRD_IMG` entry (or no `base`/`size` under it)
when the command line was last generated. The ACC is therefore told to
load its initrd from a memory range that was never specified — and
that initrd is what carries iSCSI root support, without which
`root=/dev/sda` over `netroot=iscsi:` cannot mount.

This same malformed value appears in the 9 September capture, so it
has been broken across every boot we have evidence for, and it
persists because `acc_variable` is on the persistent volume.

Intel's own provisioning script treats a stale BMD as a known problem
and clears it (`acc_os_partition_provision.sh`):

```sh
# BMD issue workaround (making sure BMD will be regenerated to most recent version)
/usr/bin/imc-scripts/clear_bmd_config
rm -f /work/cfg/memory/reserved-memory.json
```

**This is now the leading candidate**, ahead of the clock race. It is
a specific, visible defect with a documented remedy, and it explains
the symptom directly: ACC UEFI gets a bad initrd spec, the OS never
comes up, `fn_id 4` never registers, `192.168.0.2` never answers.

### A.2 `BootType: "Other"` is not a value the tooling accepts

`acc-uefi-boot-config.json` holds `"BootType": "Other"`. The setter
script `/usr/bin/imc-scripts/set_acc_uefi_boot_config` declares:

```python
boot_type_choices = ["LocalIscsiBoot", "LocalIscsiBoot_ISO", "DramBoot", "InternalUefiShell", "Pxe", "Http"]
state_choices     = ["ContinuousOverride", "OneTimeOverride", "OneTimeOverrideSynced", "OneTimeOverrideUpdated"]
```

`Other` is not in that list, so it cannot have been written by the
supported path. For iSCSI boot, Intel's reference procedure sets:

```sh
/usr/bin/imc-scripts/set_acc_uefi_boot_config --reset --BootType "LocalIscsiBoot"
```

Caveat worth holding: it is possible the ACC UEFI writes status back
into this file and `Other` is a report rather than a request. Either
way, resetting it to the reference value is correct and cheap.

### A.3 The three unknowns, answered

**Unknown 1 — triggering netboot without a reboot.** The boot-time
driver is `/etc/init.d/run_default_init_app`, and
`/usr/bin/imc-scripts/force_start_init_app` is a one-line wrapper that
calls it with `--force`.

> **Corrected in session 3.** This section originally concluded that
> the daemon was not running, based on `ps -ef | grep ipu_daemon`
> returning nothing. That was wrong: the binary is **`ipumgmtd`**,
> while `ipu_daemon` is only the journal identifier. It runs from boot
> under a systemd unit described as "Intel IPU Daemon". Grep for
> `ipumgmtd`, not `ipu_daemon`. See §B.2 for the consequence.

**Unknown 2 — boot options.** `set_acc_kernel_cmdline.sh` supports
`-b <iscsi | ram | ramdisk | custom>`, with `-a` to make the option
active and `-f` to force-regenerate the command line.

An important correction to the earlier plan: **there is no separate
"local NVMe boot" option.** `LocalIscsiBoot` *is* the local-disk path
— the IMC runs an iSCSI target at `192.168.0.1` serving
`/dev/nvme0n1p4` (partlabel `acc-rl`, 43.9 GB) to the ACC. The real
alternatives are `ram`/`ramdisk` (`DramBoot`), backed by
`/mnt/imc/acc-os.iso` (2.6 GB, dated 13 May) or
`/mnt/imc/acc/ramdisk/acc-os-image.bin`.

**Unknown 3 — ACC console.** `/dev/mev_imc_acc` is char device
`10, 126`, a misc device, not a tty. The only tty is `/dev/ttyS0`,
which is the IMC's own console. **There is no ACC console reachable
over SSH.** Getting ACC-side boot output needs the physical serial
header, which is a question for the lab maintainer.

### A.4 Corrections to §6.1.2 of the runbook

- **Setting `--VPortId 1` is probably wrong.** Intel's reference
  procedure uses `--reset`, which leaves `VPortId` at its default of
  `0`. The `ACC_VPORT_ID=0x1` that `ipu_daemon` polls for is a
  different namespace. Dropped from the plan; do not change it
  speculatively.
- **"Boot from local NVMe instead of netbooting" was based on a false
  premise** — see A.3. Replaced by the `ram`/`ramdisk` fallback.
- **The ACC console step is not actionable over SSH** — see A.3.

## B. What sessions 2–4 established

### B.1 The BMD defect was real, is root-caused, and is fixed

Session 2 confirmed the BMD had **no** `ARM_MEMORY_IMAGE_ID_INITRD_IMG`
node and no `ARM_MEMORY_IMAGE_ID_LINUX_ROOTFS` node. After
`clear_bmd_config`, removing `/work/cfg/memory/reserved-memory.json`
and a reboot, the node is back:

```
node_id = (108) ARM_MEMORY_IMAGE_ID_INITRD_IMG
base = 0x440c000000
size = 0x8000000
```

and `set_acc_kernel_cmdline.sh -b iscsi -a -f` now generates
`initrdmem=0x440c000000,0x8000000`.

**Root cause:** the custom `/work/cfg/memory/reserved-memory.json` on
this box was invalidating that node. In the old BMD, node 18 was
`ARM_MEMORY_IMAGE_ID_INVALID` at base `0x440c000000`, size
`0x8000000` — the identical region. The file's `acc_ramdisk` entry
expands RAM-disk memory to 13760 MB and its comment says it leaves
"at least 256 MB of free memory in ACC primary memory", so the layout
was tight enough to squeeze the initrd node out. Someone's large-ISO
boot tuning cost the iSCSI boot path its initrd.

`acc-uefi-boot-config.json` is also now correct:
`State: ContinuousOverride`, `BootType: LocalIscsiBoot`,
`VPortId: 0`, `Error: ""`.

**The ACC still does not boot.** Session 4, after a reboot with both
fixes in place: no `fn_id: 0x4`, `192.168.0.2` still unreachable.

### B.2 The clock test did not actually run

`force_start_init_app` failed:

```
#### Running: ipumgmtd -m 0x595EF -p 0x1000 cli_mbx -p 0x40000 0 enp0s1f0
nohup: appending output to 'nohup.out'
ERROR: init app failed with exit code: 255
```

Most likely a second `ipumgmtd` conflicting with the instance already
running from boot (see the correction in §A.3). It died before the
netboot stage: no `tar` warnings and no `LanCP` messages in that run.

So **the clock hypothesis remains untested.** Session 4's reboot does
not test it either — a reboot returns the clock to epoch, and the same
`tar`-then-`LanCP` sequence duly reappears. Session 7 does it properly.

The real error text from `ipumgmtd` went to `nohup.out`; session 5
reads it.

### B.3 Side effect: the ramdisk fallback is currently unavailable

Deleting `reserved-memory.json` also removed the RAM-disk allocation.
The force-start output shows
`Skipping Node#13: node_id(255) base(0x4414000000) size(0x0)`, where
that region was previously `ARM_MEMORY_IMAGE_ID_RAMDISK` at 13.7 GB.

iSCSI boot does not need that region, so leaving the file deleted is
correct while iSCSI boot is the goal. It has to be restored — in a
corrected form — before the `ramdisk`/`DramBoot` fallback in session 9
is possible.

### B.4 What the boot source looks like

All healthy, which narrows things usefully:

- `tgtd` is running; target `iqn.e2000:acc` is `State: ready` with
  LUN 1 at 47119 MB backed by `/dev/nvme0n1p4`, ACL `192.168.0.2`.
- `enp0s1f0` holds `192.168.0.1/24`, link up.
- `/dev/nvme0n1p4` holds a GPT (protective MBR present, `PTTYPE="gpt"`),
  so the ACC image is there rather than zeroed.
- `/mnt/imc/acc/ramdisk/acc-os-image.bin -> /mnt/imc/acc-os.iso` exists.

**But `I_T nexus information:` is empty** — no iSCSI initiator has
ever logged in. The ACC is not reaching UEFI networking at all, which
points at something before UEFI rather than at the boot source.

### B.5 The frontier is now the reset handshake

The last event before silence is unchanged across every capture:

```
LanCP[678]: [sm_rst_fp_pre_hw_cleanup_handle_event/461] WARNING: reset state machine returned unsupported event: type=3, opcode=0x1
```

It fires about a second after the IMC `tar`-extracts the ACC's ATF and
UEFI images, i.e. when it tries to release the ACC to start executing.
That is upstream of UEFI, upstream of the kernel command line, and
upstream of both defects fixed in B.1. Treat this as the thing to
explain from here.

### B.6 Two unexplained non-default overrides

`/work/cfg/config.json` sets `init_app_acc_nboot_stage: "0"` and
`init_app_acc_nboot_net_name: "enp0s1f0"`. The shipped defaults in
`/etc/init.d/run_default_init_app` are:

```sh
# (0) when IDPF interface is up, (1) when Network Subsystem has been initialized, (2) Immediately
ACC_NETBOOT_STAGE=1
# Default ACC netboot interface is enaintc10c4i0 (icc_net)
ACC_NETBOOT_INTERFACE="enaintc10c4i0"
```

Nobody has explained why DH4 deviates, and the BMD does carry an
`ARM_MEMORY_REGION_ID_ICC_NET` node. Session 6 tests reverting them.

## C. What sessions 5-7 established

### C.1 The ACC logged in to the iSCSI target

This is the first genuinely new behaviour in the whole exercise. After
session 7's force-start, `tgtadm` shows:

```
    I_T nexus information:
        I_T nexus: 1
            Initiator: iqn.1986-03.com.intel:e2000.acc-client.0000000040b4 alias: none
            Connection: 1
                IP Address: 192.168.0.2
```

Every prior capture — sessions 0, 2 and 6 included — showed `I_T nexus
information:` followed immediately by `LUN information:`, i.e. nothing
attached. So in session 7 the ACC came out of reset, executed ATF,
reached UEFI, brought up its netboot interface, took the address
`192.168.0.2`, discovered the target and completed an iSCSI login.
Everything §B.5 called the frontier was cleared.

The reset handshake is no longer the frontier.

### C.2 ...but it still does not reach Linux

`fn_id: 0x4` is still absent from `cli_client` and `ping 192.168.0.2`
still loses every packet. Both are consistent with the ACC sitting in
UEFI rather than having failed: a UEFI iSCSI initiator answers TCP on
port 3260 but generally does not answer ICMP, and `fn_id: 0x4` only
registers once the ACC's Linux IDPF driver loads.

So the ACC is somewhere between "UEFI has the LUN attached" and "Linux
is running". Session 8 is about finding out which.

### C.3 The clock looked like the differentiator — it is not (see §E.1)

Session 7 is the only run in this whole exercise where the netboot was
triggered with the clock already correct (`System clock synchronized:
yes`, `Wed Sep 16 07:49:10 PDT 2026`), because `ipu.service` was
stopped first so `force_start_init_app` could actually take the
device. It is also the only run that produced an iSCSI login.

That was not proof, and it turned out to be wrong. **§E.1 falsifies
it:** a later boot ran its netboot window at epoch, tar warnings and
all, and the ACC logged in anyway. Keep this section only as a record
of why the theory looked good for a few hours.

### C.4 Reverting the netboot overrides did nothing, and was undone

`enaintc10c4i0` does exist on DH4 (`UP`, `LOWER_UP`, MTU 4074), so
§B.6's overrides were not compensating for a missing interface.
Reverting both to the shipped defaults and rebooting changed nothing:
no `fn_id: 0x4`, no ping, empty `I_T nexus`.

Note that session 7's force-start logged `ACC_NETBOOT_STAGE=0` and
`ACC_NETBOOT_INTERFACE=enp0s1f0`, meaning `config.json.bak` had been
restored by then. **The successful run used the original overrides,
stage 0 on `enp0s1f0`.** Worth confirming before session 9, since it
means the overrides are part of the recipe that worked.

### C.5 `nohup.out` was never found, and `ipu.service` is the unit

`nohup.out` is not in `/root`, `/`, or `/work`, so the exit-255 message
from §B.2 is still unread — `force_start_init_app` runs the daemon from
some other working directory. A `find` will settle it.

The unit is `ipu.service` ("Intel IPU Daemon",
`ExecStart=/etc/ipu/ipu_daemon`), whose cgroup holds `ipumgmtd`,
`port-setup.sh` and both `tgtd` processes. Stopping it is what made
session 7 possible.

### C.6 What the reset warning was actually reporting

`systemctl status ipu.service` exposed the detail behind §B.5's
one-line warning:

```
LanCP[678]: [disable_fxp_rsc_flow/1929] ERR: Failed to disable RSC context on vport
LanCP[678]: [disable_fxp_rsc_flow_all_vports/1952] ERR: Err = 0x1 disable FXP RSC flow for vport =1   (also 2, 3)
LanCP[678]: [cp_disable_all_vport_rsc_cfg/1477] ERR: Failed to initiate closure on RSC flow
LanCP[678]: [cp_pre_hw_drain_cleanup/3478] ERR: Failed to disable FXP and RSC flow for pf=4
LanCP[678]: [icpf_rdma_is_func_valid/4689] ERR: no vf dev found
```

`pf=4` is the ACC. The IMC was failing to quiesce the ACC's FXP and RSC
flows before resetting it, which is what left the reset state machine
in a state it called "unsupported". Given §C.1 this is worth keeping
for the upstream report but is no longer the blocker.

## D. What sessions 8-9 established

### D.1 The ACC loaded ~84 MB and then went silent

Session 8's reads are unambiguous once you put them together:

| Check | Result |
| --- | --- |
| `tgtadm` nexus | present, initiator at `192.168.0.2` |
| `ss -tnp \| grep 3260` | `ESTAB 192.168.0.1:3260 192.168.0.2:1753` |
| `ip neigh show` | `192.168.0.2 dev enp0s1f0 FAILED` |
| `/proc/diskstats` nvme0n1p4, 30 s apart | `164232` sectors both times |
| `tcpdump host 192.168.0.2`, 30 s | 0 packets captured |

164232 sectors is roughly 84 MB — the right order of magnitude for a
kernel plus initrd pulled off the LUN. Then nothing: no reads, no
packets, and a **failed** ARP entry, meaning the ACC is not even
answering `who-has` any more.

The `ESTAB` socket does not contradict that. TCP holds `ESTAB` forever
if neither side sends anything and no keepalives are configured, so the
socket is a fossil of a session that is no longer being used.

So the sequence is: ACC comes out of reset, runs ATF, reaches UEFI,
brings up networking, logs in to iSCSI, reads its boot image, and then
stops. That is exactly the profile of UEFI handing off to a kernel that
dies before it can bring its own networking up — UEFI's network stack
goes away at `ExitBootServices`, and if the kernel panics or hangs
before the ACC-side IDPF driver loads, the card goes silent with the
LUN still nominally attached.

**The frontier has moved again: it is now the ACC's Linux boot, not the
reset handshake and not iSCSI.** This is the first point in the whole
exercise where §B.1's `initrdmem` fix is actually on the critical path.

### D.2 The ACC attached on a plain reboot, with no force-start

Session 9 rebooted the IMC and, before touching anything, found the
nexus already present at 08:48:52 on an 18-minute-old boot:

```
[root@ipu-imc ~]# tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'   # expect empty after a cold boot
    I_T nexus information:
        I_T nexus: 1
            Initiator: iqn.1986-03.com.intel:e2000.acc-client.0000000040b4 alias: none
```

Nobody stopped `ipu.service` and nobody ran `force_start_init_app`.
Either the ACC survived the IMC reboot and its initiator reconnected
when `tgtd` came back, or it now attaches on a normal boot.

Either reading weakens §C.3. The clock cannot be the whole story if a
cold boot — where the netboot window is at epoch as always — produces
the same login. The boot journal for that boot would have settled it,
and it is now lost (§D.4). Re-establish this on the next clean boot
before drawing any conclusion.

### D.3 `systemctl stop ipu.service` kills the ACC's boot disk

This is the operational trap, and the session 9 procedure as written
walked straight into it.

`tgtd` runs inside the `ipu.service` cgroup (§C.5). Stopping the unit
therefore SIGKILLs it:

```
08:50:42 ipu-imc systemd[1]: ipu.service: Killing process 1269 (tgtd) with signal SIGKILL.
08:50:42 ipu-imc systemd[1]: ipu.service: Killing process 1270 (tgtd) with signal SIGKILL.
```

In session 7 that was harmless, because the ACC had not started. In
session 9 the ACC already had a live session against that target, so
stopping the unit pulled the boot disk out from under a mid-boot ACC.
Every `tgtadm` call afterwards returns `failed to send request hdr to
tgt daemon`.

**Never stop `ipu.service` while `I_T nexus` is non-empty.** Check
first, every time.

### D.4 The IMC wedged, and it is a kernel-level hang

Around the same window the boot-time `LanCP[677]` tore its own control
plane down and failed to rebuild it:

```
08:49:08  cp_pre_hw_drain_cleanup/3478  ERR: Failed to disable FXP and RSC flow for pf=5
08:49:12  cplib_memdev_exit/260         ERR: Could not take mutex lock
08:49:12  deinit_cp_malloc_mgr/802      WARNING: found unfree memeory  (774576 bytes)
08:49:15  cplib_init/1703               ERR: failed to wait for pthread barrier completion
08:49:15  cplib_init/1711               ERR: Failed to initialize RDMA event
08:49:15  init_cpf_fxp_gbl_cfg_data     ERR: failed to load wcm cfg data, err: -1
```

Then `force_start_init_app` started a second `ipumgmtd` on top of that,
and a few minutes later:

```
08:58:59  systemd-logind.service: Watchdog timeout (limit 3min)!
09:00:30  systemd-logind.service: Killing process 139 (systemd-logind) with signal SIGKILL.
09:02:00  systemd-logind.service: Processes still around after SIGKILL. Ignoring.
```

**Surviving SIGKILL means uninterruptible sleep.** A process in `D`
state is blocked inside the kernel and cannot be signalled at all, so
this is not a stuck userspace daemon — something in the IMC kernel is
blocked, almost certainly on the device/mailbox path that LanCP left
half-initialised.

`systemd-logind` being gone is why SSH stops working: `pam_systemd`
calls `org.freedesktop.login1` during session setup, and with logind
wedged that call hangs rather than fails. Existing sessions keep
working; new ones do not. See [Recovering a wedged IMC](#recovering-a-wedged-imc).

## E. Where the investigation actually stands

Four things came out of the last pass. Two close questions that had
been open for weeks, and two open a new and much more specific one.

### E.1 The clock hypothesis is dead

```
[root@ipu-imc ~]# grep -cE 'in the future' /work/boot-*.log
4
[root@ipu-imc ~]# tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'
    I_T nexus information:
        I_T nexus: 1
            Initiator: iqn.1986-03.com.intel:e2000.acc-client.0000000040b4 alias: none
```

That boot extracted the ACC's ATF images at epoch — all four `tar: ...
time stamp ... is ... s in the future` warnings present — and the ACC
logged in to iSCSI regardless. The RTC-less epoch clock does not
prevent ACC boot. §6.1.1 of the runbook can be closed as **falsified**,
and the firmware-NDA blocker it implied goes away with it.

### E.2 The ACC is hung, not crash-looping

Thirty samples of `/proc/diskstats` over fifteen minutes, from 10:04 to
10:19, all read `164232` sectors. No second boot attempt, no retry, no
reset. Combined with `acc_watchdog_timer: 60` in `acc-config.json`,
which should have fired long ago against a dead core, the most likely
reading is that the ACC kernel is **running and waiting for
something** rather than panicked.

Given the boot parameters in §E.3, the obvious candidate is the
initramfs sitting in its "waiting for root device" loop: it would keep
the watchdog serviced, issue no further LUN reads, and put nothing on
the wire. That matches every observation.

### E.3 The iSCSI boot parameters give the ACC no console

This is the actionable finding. `set_acc_kernel_cmdline.sh` builds two
different command lines, and they are not equivalent:

```sh
# ramdisk branch (line 297)
CMD_LINE="ip=...:${COMM_IFACE}:off root=/dev/mbd0 rw mbd.device=${rootfs_size}@${rootfs_base} console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force"

# iscsi branch (line 305)
CMD_LINE="ip=...:${COMM_IFACE}:off root=/dev/sda rw netroot=iscsi:${COMM_IMC_IP}::::iqn.e2000:acc initrdmem=${initrd_base},${initrd_size} acpi=force"
```

The ramdisk path hands the ACC kernel a console at
`uart8250,mmio32,0x2058400000`. The iSCSI path hands it nothing at all.
DH4 boots the iSCSI path, which is why a kernel that hangs in early
userspace does so in total silence.

`0x2058400000` is not a guess to be validated — it is the vendor's own
value for the ACC UART, sitting in the vendor's own script. Compare the
IMC's `earlycon=uart8250,mmio32,0x2008400000` in its kernel command
line. Session 10 puts it on the iSCSI command line.

### E.4 `ACC_NETBOOT_INTERFACE` is used for two unrelated things

`run_default_init_app` passes the same string to both complexes:

```sh
# line 935 - which IMC interface ipumgmtd serves netboot on
INIT_APP_OPT_EXT="$INIT_APP_OPT_EXT -p 0x40000 $ACC_NETBOOT_STAGE $ACC_NETBOOT_INTERFACE"

# line 419 - and the device name written into the ACC's kernel ip= parameter
/usr/bin/scripts/set_acc_kernel_cmdline.sh -a -f -b "${TARGET_ACC_BOOT_OPTION}" -i "${ACC_NETBOOT_INTERFACE}"
```

So DH4's `/work/cfg/config.json` override does two jobs at once, and
the resulting ACC command line reads:

```
ip=192.168.0.2:::255.255.255.0::enp0s1f0:off root=/dev/sda rw netroot=iscsi:192.168.0.1::::iqn.e2000:acc initrdmem=0x440c000000,0x8000000 acpi=force
```

That only works if the ACC names its end of the link `enp0s1f0` too. If
it does not, the ACC kernel cannot configure an interface, cannot reach
`192.168.0.1`, cannot attach `/dev/sda`, and waits forever — exactly
§E.2.

This is plausible but **unproven**, and reverting the override is not a
valid test on its own: §C.4 showed the shipped default
`enaintc10c4i0` stops the ACC attaching at all, so DH4 needs
`enp0s1f0` on the IMC side. The two uses have to be decoupled, which
session 11 covers — but only after session 10, because the console
answers this question directly and guessing does not.

### E.5 Neither console path is open yet

- `/dev/mev_imc_acc` exists (char 10,126) but is a misc device, not a
  tty. `/sys/class/tty` lists only `ttyS0` (the IMC's own console) and
  `ttyFAM0`. There is no ACC tty on the IMC.
- Redfish answers on 8443 and reports `Intel IPU Redfish Service`,
  `RedfishVersion 1.21.0`, but `/redfish/v1/Systems` and
  `/redfish/v1/Managers` both return
  `Base.1.18.ResourceAtUriUnauthorized`. It needs credentials nobody
  has asked the lab maintainer for yet.

## F. The ACC console exists and is already on the IMC

### F.1 `ttyFAM0` is at the ACC's earlycon address

Session 10's `dmesg` produced the single most useful line in this
whole exercise:

```
[    0.690915] 2058400000.uartfam: ttyFAM0 at MMIO 0x2058400000 (irq = 0, base_baud = 0) is a DW_apb_uart FAM
[    0.693883] dw-fam 2058400000.uartfam: poll=10ms epass=5
```

`0x2058400000` is exactly the address in
`earlycon=uart8250,mmio32,0x2058400000` — the ACC console parameter
from the vendor's ramdisk command line in §E.3. The IMC has a driver
bound to that UART and exposes it as `/dev/ttyFAM0`.

That device has been listed in `/sys/class/tty` in every capture since
§E.5, alongside the IMC's own `ttyS0`, and nobody matched it to the
address in the script. It is not a physical header, it is not behind
the BMC, and it does not need Redfish credentials. It is a character
device on a machine you already have a root shell on.

Whether it carries ATF and UEFI output as well as Linux output is
untested — those stages configure their consoles independently of the
kernel command line. Reading it costs nothing, so find out first
(session 11).

### F.2 The `acc-config.json` edit did not survive the reboot

```
[root@ipu-imc ~]# cat /mnt/imc/acc_variable/acc-config.json
        "boot_params" : "... initrdmem=0x440c000000,0x8000000 acpi=force"
```

The console parameters are gone. `run_default_init_app` regenerates
`boot_params` on every boot by calling `set_acc_kernel_cmdline.sh -a
-f`, which rewrites the whole string from the template in §E.3.

Patching the generator instead does not help on its own either: the
IMC roots on a ramdisk (`root=/dev/ram0` in its kernel command line),
so everything under `/usr/bin` is rebuilt from the image at every boot.
Only `/work` and `/mnt/imc` persist.

The way through is the drop-in directory. `/work/scripts/pre_init_app.sh`
sources everything in `/work/scripts/pre_init_app.d/*.sh`, and it runs
*before* the init app regenerates the command line:

```sh
#### Running  /work/scripts/pre_init_app.sh
...
WARNING: ACC_NETBOOT_STAGE=0 due to ...
INFO: Booting ACC with default OS over iSCSI
```

So a drop-in can re-patch the generator on the volatile rootfs each
boot, and the init app then generates a command line that already has
the console in it. Session 12 does this.

## G. The console works, and the capture was hijacking the boot

### G.1 `/dev/ttyFAM0` carries the full ACC boot

209 KB of it, starting from the second-stage bootloader:

```
NOTICE:  BL31: v2.6(release):
NOTICE:  BL31: Built : 07:08:42, Mar 31 2025
UEFI firmware (version  built at 07:05:10 on Mar 18 2025)
Intel(R) IPU Embedded UEFI FW 1.7.4 for AArch64
System Memory size: 17920 MB
```

§F.1 is confirmed. ATF and UEFI write to this UART without needing any
kernel parameter, so the ACC has been narrating its own boot into a
device on the IMC for the entire investigation.

### G.2 The ACC's disk is intact and UEFI can read it

The shell's mapping table resolves the iSCSI LUN to a real GPT disk:

```
FS0:  Alias(s):HD0n13a13ffk1:;BLK1:
      PcieRoot(0x0)/Pci(0x1,0x0)/MAC(0000000040B4,0x1)/IPv4(192.168.0.1)/iSCSI(iqn.e2000:acc,...)/HD(1,GPT,22ADAD9D-...,0x800,0x12C000)
BLK2: .../HD(2,GPT,77EA1C8A-...,0x12C800,0x200000)
BLK3: .../HD(3,GPT,C4F29FE6-...,0x32C800,0x5497000)
```

Three partitions — roughly 600 MB, 1 GB and 45 GB — totalling the
47119 MB `tgtadm` reports for the LUN. `FS0:` means UEFI mounted the
first one as a filesystem. The ACC OS image is not missing, truncated
or corrupt, which retires the re-provisioning fallback.

### G.3 The `cat` in the drop-in fed the ACC its own output

This is the important one. Session 11 recorded the device's default
line settings:

```
speed 9600 baud; ...
isig icanon iexten echo echoe echok ...
```

**`echo` and `icanon` are on by default.** Session 11 cleared them with
`stty raw -echo`, but termios reverts when the last file descriptor
closes, and the session 12 drop-in runs a bare `cat` with no `stty`. So
the IMC's line discipline echoed every byte the ACC sent straight back
to the ACC as keystrokes.

The log shows the loop unambiguously — UEFI's own earlier output
arriving at its shell prompt as commands:

```
Shell> System Memory size: 17920 MB
'System' is not recognized as an internal or external command, operable program, or script file.
Shell> Press F2 to display Boot Device List
'Press' is not recognized as an internal or external command, operable program, or script file.
```

and then degenerating into amplified garbage as each round of output
echoed back and generated more output.

**The echo arrives during UEFI's "Press F2 ... Press F7 or ESC"
window**, which is a keypress prompt. That is almost certainly what
aborted the automatic boot and dropped the ACC into the UEFI
Interactive Shell instead of booting `FS0:`.

So session 12's result is not "the ACC drops to a UEFI shell". It is
"our instrumentation pressed keys during the boot prompt". Session 13
fixes it.

### G.4 `fn_id: 0x4` now appears — from UEFI, not Linux

`cli_client -q -c | grep 'fn_id: 0x4'` returning four lines is real and
new: the ACC's PCIe function is registered with the IMC control plane
and has vports. But the ACC in these captures is sitting at a UEFI
shell prompt, so this is UEFI's network stack, not a booted Linux.

It still matters. `port-setup.sh` spins waiting for exactly `fn_id: 0x4
... vport_id 0x1` before it programs the VSI group (§C.5), so that
machinery can now fire. Do not read it as "the ACC booted".

## H. The ACC reaches GRUB and starts booting Fedora

### H.1 A clean console changes the whole picture

`grep -c 'is not recognized'` returns 0, the log is 2512 bytes instead
of 209 KB, and with nobody typing at it the ACC does not stop at a UEFI
shell at all. It auto-boots:

```
NOTICE:  BL31: v2.6(release):
UEFI firmware (version  built at 07:05:10 on Mar 18 2025)
Intel(R) IPU Embedded UEFI FW 1.7.4 for AArch64
System Memory size: 17920 MB
Press F2 to display Boot Device List
Press F7 or ESC to enter UEFI Setup Menu ...........
                                              GRUB version 2.12
   *Fedora Linux 43 (Forty Three) (ostree:0)
    UEFI Firmware Settings
   The highlighted entry will be executed automatically in 1s.
  Booting `Fedora Linux 43 (Forty Three) (ostree:0)'
```

§G.3 was right: the interactive shell was our own keystrokes. Left
alone, UEFI boots the ESP, GRUB 2.12 comes up, its one-second timer
expires, and it hands off to the kernel.

**The ACC OS is Fedora Linux 43, deployed with ostree.** Nobody in this
investigation knew that until now.

The log ends at `Booting ...`. Nothing after.

### H.2 `acc-config.json` `boot_params` is probably not used on this path

This reframes several earlier sections. The boot chain is UEFI → GRUB →
Fedora, and **GRUB supplies the kernel command line** from its own
config on the ACC's boot partition. The `ip=`, `root=/dev/sda`,
`netroot=iscsi:` and `initrdmem=` string in `acc-config.json` looks like
it belongs to the direct-kernel/DramBoot path, not the one DH4 takes.

Consequences:

- The `initrdmem=,` defect in §B.1 is still a genuine bug worth
  reporting, but it was probably never what stopped this board booting.
- §E.4, the theory that `enp0s1f0` in the ACC's `ip=` parameter is
  wrong, is **downgraded**. If GRUB owns the command line, that
  parameter never reaches the kernel. Do not spend time on it until
  §H.3 is resolved.
- Adding `console=` to `boot_params` in sessions 10 and 12 was very
  likely a no-op. What fixed the boot was removing the echo, not the
  parameter. The console output visible so far is ATF and UEFI writing
  to the UART on their own initiative.

That last point is the one that matters: **the Fedora kernel has no
`console=` argument pointing at this UART**, which is why the log stops
dead at GRUB's handoff.

### H.3 Two possibilities, not yet distinguished

Silence after `Booting ...` means one of:

1. **Fedora is booting or has booted**, and simply is not writing to
   this UART because its command line does not mention it. In that case
   the ACC may be further along than anyone realises.
2. **The kernel dies at or shortly after handoff**, silently, as
   before.

There is also a third, duller possibility worth ruling out first: the
background `cat` died, in which case the absence of output means
nothing at all. Session 14 checks all three.

## I. What sessions 14-15 established

### I.1 Fedora is not running

Both before and after the session 14 reboot:

| Check | Result |
| --- | --- |
| iSCSI initiator | `iqn.1986-03.com.intel:e2000.acc-client...` only |
| `nvme0n1p4` sectors read | frozen at `164232` (~80 MiB) |
| `ping 192.168.0.2` | Destination Host Unreachable |
| `fn_id: 0x4` | absent |
| ARP | `INCOMPLETE` then `FAILED` |

A booted Fedora on an iSCSI root would have opened a Red Hat IQN,
kept reading the LUN, and answered ARP. None of that happens. The
ACC reaches GRUB and then the kernel does not take over the disk.

`fn_id: 0x4` appearing in session 12 was UEFI sitting at a shell
with its network stack up. Once GRUB actually boots, UEFI calls
`ExitBootServices`, that stack is torn down, and Linux never
replaces it — so the function disappearing is consistent, not a
regression.

### I.2 The console capture dies at the handoff

`ps` showed no `cat` on `ttyFAM0` either before or after the reboot.
The log grew from 2512 to 5024 bytes — exactly two copies of the
same ATF → UEFI → GRUB sequence — and then stopped. So either the
kernel writes nothing to this UART, or `cat` hits EOF when the ACC
reprograms the UART at kernel entry and exits.

Until the logger restarts itself, silence after `Booting ...` cannot
be trusted as "the kernel produced no output".

### I.3 Session 15 never saw the Fedora filesystems

`losetup -Pf --show /dev/nvme0n1p4` created **`/dev/loop1`**. The
commands then inspected `/dev/loop0`, which is already the 28 MiB
`acc_variable` image mounted at `/mnt/imc/acc_variable`. There is
no `loop0p2`, the mount failed, and nothing was edited.

The GPT from §G.2 is still the map:

| Part | Start / size (sectors) | Likely role |
| --- | --- | --- |
| p1 | `0x800` / `0x12C000` (~600 MiB) | ESP (`FS0:`) |
| p2 | `0x12C800` / `0x200000` (1 GiB) | `/boot` |
| p3 | `0x32C800` / `0x5497000` (~42 GiB) | ostree root |

Session 16 mounts `loop1p1` and `loop1p2` **read-only** and stops
there. Do not remount read-write until you have read the command
line.

## J. The IMC cannot mount the ACC filesystems

`losetup -a` / `lsblk` confirmed the layout we inferred from UEFI:

```
/dev/loop1: [0005]:127 (/dev/nvme0n1p4)   43.9G
├─loop1p1  600M
├─loop1p2    1G
└─loop1p3 42.3G
```

`mount -o ro` then said:

```
mount: /mnt/accp1: unknown filesystem type 'vfat'.
mount: /mnt/accp2: unknown filesystem type 'xfs'.
```

So the kernel **detected** the types (blkid/libblkid works) but this
IMC image has no `vfat` or `xfs` driver. The empty `/mnt/accp1` and
`/mnt/accp2` listings are the empty mount-points, not empty disks.
`find` and `cat loader/entries` were looking at directories that were
never mounted.

Fedora 43 putting `/boot` on XFS is expected. The IMC ramdisk is not a
Fedora userspace; it cannot be used as a file server for that disk
until a module is loaded or the image is read somewhere else.

## K. The kernel command line GRUB actually uses

`grep -a` of `/dev/loop1p2` (and the same image mounted on DH4) found
repeated BLS `options` / `kernelopts` lines. The live ostree variant
is:

```
options root=/dev/mapper/rhel-root rd.lvm.lv=rhel/root rd.iscsi.firmware rw boot=UUID=99621220-3b7e-4729-a85e-974dea9b2b71 ip=192.168.0.2:::255.255.255.0::enp0s1f0:off netroot=iscsi:192.168.0.1::::iqn.e2000:acc acpi=force ostree=/ostree/boot.1/default/5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/0
```

The non-ostree copies are the same minus `ostree=` / `boot=UUID`, plus
`crashkernel=` and sometimes hugepages. The kernel file named in the
same region is `vmlinuz-5.14.0-570.32.1.el9_6.aarch64` — RHEL 9.6 —
even though GRUB's menu said Fedora 43 ostree.

What that string does and does not contain:

| Present | Meaning |
| --- | --- |
| `netroot=iscsi:192.168.0.1::::iqn.e2000:acc` | dracut should reopen the IMC target |
| `rd.iscsi.firmware` | also try iBFT from UEFI |
| `ip=192.168.0.2:::255.255.255.0::enp0s1f0:off` | static IP, **only** if a Linux interface is named `enp0s1f0` |
| `root=/dev/mapper/rhel-root` + `rd.lvm.lv=rhel/root` | root is an LVM LV, not `/dev/sda` |
| no `console=` / `earlycon=` for `0x2058400000` | kernel stays silent on `ttyFAM0` |

So §E.4 (`enp0s1f0` on the ACC kernel) is back on the path that
actually boots. `acc-config.json` `boot_params` was a red herring for
this board; GRUB's copy of the same `ip=` is what the kernel sees.

The host mount landed on `/mnt/accboot/loader.1/entries/ostree-1.conf`.
`find` could not enter `grub2` without root. Session 18 reads those
files with `sudo` and does not write anything back yet.

Confirmed contents (session 18):

```
title Fedora Linux 43 (Forty Three) (ostree:0)
version 1
options root=/dev/mapper/rhel-root rd.lvm.lv=rhel/root rd.iscsi.firmware rw boot=UUID=99621220-3b7e-4729-a85e-974dea9b2b71 ip=192.168.0.2:::255.255.255.0::enp0s1f0:off netroot=iscsi:192.168.0.1::::iqn.e2000:acc acpi=force ostree=/ostree/boot.1/default/5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/0
linux /boot/ostree/default-5447b60d.../vmlinuz-7.0.4-100.fc43.aarch64
initrd /boot/ostree/default-5447b60d.../initramfs-7.0.4-100.fc43.aarch64.img
fdtdir /boot/ostree/default-5447b60d.../dtb
```

The kernel that GRUB loads is **Fedora 43** `7.0.4-100.fc43.aarch64`.
The RHEL 5.14 names in the `strings` dump are leftovers on the same
`/boot`. Root is still an LVM LV named `rhel/root`. There is no ACC
UART `console=`.

## Recovering a wedged IMC

Work down this list. Each step is cheaper and less disruptive than the
one after it.

**1. Do not close any SSH session you still have open.** An established
session does not need `logind` and is the cheapest way back in.

**2. Check whether the IMC kernel is alive at all**, from the host:

```sh
ping -c5 <imc-mgmt-ip>
nc -vz <imc-mgmt-ip> 22
```

Ping replying means the kernel is servicing interrupts and only part of
it is stuck. No reply means it is much further gone.

**3. Try SSH and actually wait.** With `logind` hung, `pam_systemd`
blocks on a D-Bus call that times out at 25 seconds and then usually
lets the session through anyway. Give it a full minute or two before
concluding it failed:

```sh
ssh -v root@<imc-mgmt-ip>
```

The verbose output tells you whether you are stuck at TCP, at auth, or
after auth. Stuck *after* auth is the `logind` case and is survivable.

**4. Try Redfish.** `redfish.service` is a separate unit and does not
depend on `logind`:

```sh
curl -sk https://<imc-mgmt-ip>:8443/redfish/v1/Systems
```

If this answers, the IMC is healthier than SSH suggests, and Redfish
can also drive a reset.

**5. Serial console,** if the lab has the IMC's UART broken out. The
kernel command line is `console=ttyS0,460800`. This is the only path
that still works once `sshd` is affected, and it is the one that would
show a kernel stack trace for whatever is in `D` state.

**6. Power-cycle the board.** If the above all fail, the card needs its
power domain reset.

> **Rebooting the x86 host is not that.** A warm host reboot leaves
> PCIe slot power up, so the IMC keeps running exactly as wedged as it
> is now. Worse, on platforms that assert `PERST#` across a warm reboot
> it hands the card a host-reset event, and the code path that handles
> it is `cp_pre_hw_drain_cleanup` — the one already failing above.

What actually resets the IMC:

- a BMC-driven reset of the card, if DH4's BMC is reachable (there is a
  `bmc-imc` interface on the IMC side, so the path exists);
- a Redfish reset action, if step 4 answered;
- a **full AC power cycle** of DH4: power off, wait ~30 s for the slot
  rails to drain, power on. This is the reliable one, and on a shared
  community-lab box it is a maintainer action — ask, do not just do it.

`imc_reset` and `ipu_reboot.service` exist on the IMC but both need a
working shell, so they are only useful if steps 1-5 got you one.

## Ground rules

DH4 is a shared community-lab box. Confirm nobody else is mid-run
(`who`, `last -n 20`, lab channel) before changing anything. Change
one variable at a time. Log to `/work`, which is persistent:

```sh
script -a /work/triage-$(date +%s).log
```

Note `file` is not installed on this image; use `head -c` or `od`
instead where you would reach for it.

## Session 2 — is the boot source even there? (read-only) — **done, see §B.4**

No changes, no reboot. This session can invalidate everything else, so
run it first.

**The BMD table — directly tests A.1:**

```sh
/usr/bin/bmd_conf -p
/usr/bin/bmd_conf -p | grep -A 3 "ARM_MEMORY_IMAGE_ID_INITRD_IMG"
/usr/bin/bmd_conf -p | grep -A 3 "ARM_MEMORY_IMAGE_ID_LINUX_ROOTFS"
cat /work/cfg/memory/reserved-memory.json 2>/dev/null
```

If `ARM_MEMORY_IMAGE_ID_INITRD_IMG` is absent, or present without a
`base = 0x...` / `size = 0x...` pair, **A.1 is confirmed** and session
3 is the fix.

**Is the iSCSI target actually serving?** If `tgtd` is not running, or
the LUN is not backed, the ACC has nothing to boot from regardless of
anything else:

```sh
ps -ef | grep -i tgtd
tgtadm --lld iscsi --op show --mode target
/etc/init.d/tgtd status 2>/dev/null
ip addr show enp0s1f0        # expect 192.168.0.1
```

**Does the ACC root partition hold a real image?** `lsblk` shows
`nvme0n1p4` with no FSTYPE and no LABEL, while its siblings are
labelled ext4. That may be normal — the provisioning script `dd`s a
full disk image with a nested partition table onto it — but an empty
or zeroed partition would explain everything:

```sh
blkid /dev/nvme0n1p4
od -A x -t x1z -N 512 /dev/nvme0n1p4 | head -40
partx -s /dev/nvme0n1p4 2>/dev/null
ls -la /mnt/imc/acc/ /mnt/imc/acc/iscsi/ /mnt/imc/acc/ramdisk/
```

A first sector of all zeros means there is no bootable image on
`acc-rl`, which makes session 5 (provisioning) the path rather than
session 3.

**Read the re-trigger helper before session 3 depends on it:**

```sh
cat /usr/bin/imc-scripts/force_start_init_app
sed -n '1,80p' /etc/init.d/run_default_init_app
grep -niE 'nboot|netboot|initrdmem|tgtd|iqn' /etc/init.d/run_default_init_app | head -40
```

## Session 3 — regenerate the command line, then re-trigger (no reboot) — **done, see §B.1–B.2**

Assumes session 2 confirmed a missing or stale BMD initrd entry.

**Step 1 — try regenerating the command line from the current BMD.**
`-f` forces a rewrite even though the current option is already
`iscsi`:

```sh
cat /mnt/imc/acc_variable/acc-config.json          # before
/usr/bin/scripts/set_acc_kernel_cmdline.sh -b iscsi -a -f
cat /mnt/imc/acc_variable/acc-config.json          # after
```

Read `initrdmem=` in the result:

- Now `initrdmem=0x...,0x...` → the BMD was fine and only the stored
  command line was stale. Go to step 3.
- Still `initrdmem=,` → the BMD genuinely lacks the entry. Go to
  step 2.

**Step 2 — clear the BMD so it regenerates.** This is Intel's own
workaround, lifted from `acc_os_partition_provision.sh`:

```sh
/usr/bin/imc-scripts/clear_bmd_config
rm -f /work/cfg/memory/reserved-memory.json
reboot
```

After it returns, re-check and regenerate:

```sh
/usr/bin/bmd_conf -p | grep -A 3 "ARM_MEMORY_IMAGE_ID_INITRD_IMG"
/usr/bin/scripts/set_acc_kernel_cmdline.sh -b iscsi -a -f
cat /mnt/imc/acc_variable/acc-config.json
```

**Step 3 — set the boot config to the reference value** (fixes A.2):

```sh
/usr/bin/imc-scripts/set_acc_uefi_boot_config --reset --BootType "LocalIscsiBoot"
cat /mnt/imc/acc_variable/acc-uefi-boot-config.json
/usr/bin/imc-scripts/enable_acc_boot
```

Expect `State: ContinuousOverride`, `BootType: LocalIscsiBoot`,
`VPortId: 0`, `Error: ""`.

**Step 4 — the decisive test, with the clock already correct.**
Confirm the clock first, then re-trigger using whatever session 2's
read of `force_start_init_app` showed:

```sh
timedatectl status | grep -i synchronized      # expect: yes
chronyc tracking | head -3
/usr/bin/imc-scripts/force_start_init_app      # confirm semantics first
sleep 120
cli_client -q -c | grep -i 'fn_id: 0x4'
ping -c3 192.168.0.2
journalctl -o short-iso --no-pager --since '-5 min' | grep -iE 'acc|tar|iscsi|primary|reset'
```

**How to read the result:**

- ACC appears → fixed. Record which of steps 1–3 did it.
- ACC absent, and **no** `tar: ... in the future` warnings this time
  (because the clock is correct) → the clock hypothesis in §6.1.1 is
  **falsified**. Stop waiting on firmware entirely.
- ACC absent but `tar` warnings still present despite a good clock →
  the extract is reading timestamps from somewhere other than the
  system clock; capture and report.

Whatever happens, this run separates the clock question from the BMD
question, which no previous attempt has done.

## Session 4 — reboot with the corrected configuration — **done, ACC still absent**

Only if session 3 changed things but the ACC did not come up from a
warm re-trigger. Some of this state is consumed by ACC UEFI at power-on
rather than on demand.

```sh
reboot
# wait ~3 minutes
cli_client -q -c | grep -i 'fn_id: 0x4'
ping -c3 192.168.0.2
cat /mnt/imc/acc_variable/acc-config.json | grep initrdmem
journalctl -b -o short-iso --no-pager | grep -iE 'acc|idpf|iscsi|primary|reset|tar'
```

Capture this journal carefully — it is the first boot in which the
command line and boot type are both known-good, so its contents are
the most informative evidence we will have produced.

---

# Next steps — start here

The image is already mounted on DH4 at `/mnt/accboot`. Start at
session 18: `sudo cat` the ostree BLS entry. Do not write the image
back, and do not reboot, until that file is in the capture.

## Session 5 — why the init app exited 255 (read-only) — **done, see §C.5**

The actual `ipumgmtd` error went to `nohup.out` in whatever directory
you ran `force_start_init_app` from, most likely `/root`:

```sh
ls -l ~/nohup.out /nohup.out /work/nohup.out 2>/dev/null
cat ~/nohup.out
```

Then establish what is already running and under which unit, since
§A.3's original claim was wrong:

```sh
ps -ef | grep -i ipumgmtd
systemctl list-units --all | grep -i ipu
systemctl status "$(systemctl list-units --all --plain --no-legend \
  | grep -i 'Intel IPU Daemon' | awk '{print $1}')"
```

**Stop condition:** if `nohup.out` says the device, mailbox or a lock
is already held, B.2 is confirmed and session 7's stop-first approach
is required. If it says something else entirely, that message is
probably more interesting than anything else in this document — read
it before continuing.

## Session 6 — revert the two non-default netboot overrides — **done, see §C.4**

Tests §B.6. Back the file up first, because `/work` is persistent and
this is the only copy:

```sh
cp /work/cfg/config.json /work/cfg/config.json.bak
cat /work/cfg/config.json
```

First check whether the default interface even exists on this board —
if `enaintc10c4i0` is absent, the override to `enp0s1f0` was probably
necessary and only the stage is worth changing:

```sh
ip -br link show
ip link show enaintc10c4i0 2>&1 | head -2
```

Then revert. `cfg_boot_options` takes `<name> <value>`:

```sh
/usr/bin/imc-scripts/cfg_boot_options init_app_acc_nboot_stage "1"
# only if enaintc10c4i0 exists:
/usr/bin/imc-scripts/cfg_boot_options init_app_acc_nboot_net_name "enaintc10c4i0"
cat /work/cfg/config.json
reboot
```

After the reboot:

```sh
cli_client -q -c | grep -i 'fn_id: 0x4'
ping -c3 192.168.0.2
tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'
journalctl -b -o short-iso --no-pager | grep -iE 'acc|iscsi|primary|reset|tar|nboot'
```

Watch the `WARNING: ACC_NETBOOT_STAGE=` line in the boot log to confirm
the new value took effect. If the ACC still does not appear, restore
the backup so you are not carrying an untested change into later
sessions:

```sh
cp /work/cfg/config.json.bak /work/cfg/config.json
```

## Session 7 — the clock test, properly — **done, iSCSI login, see §C.1-C.3**

The point is a netboot attempt with the clock already correct.
`force_start_init_app` cannot do that while `ipumgmtd` holds the
device, so the running instance has to stop first.

**This drops the IMC control plane for the duration. Confirm with the
lab channel before running it on a shared box.**

```sh
timedatectl status | grep -i synchronized      # must say: yes
chronyc tracking | head -3
date                                            # sanity: real 2026 date

systemctl stop <unit-from-session-5>
ps -ef | grep -i ipumgmtd                       # expect: gone
/usr/bin/imc-scripts/force_start_init_app
```

Watch for the netboot stage actually running this time — you should
see `INFO: Booting ACC with default OS over iSCSI` followed by the
`tar` extraction, which did **not** happen in the failed attempt.

```sh
sleep 120
cli_client -q -c | grep -i 'fn_id: 0x4'
ping -c3 192.168.0.2
tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'
journalctl -o short-iso --no-pager --since '-5 min' | grep -iE 'acc|tar|iscsi|primary|reset'
```

**How to read it:**

- No `tar: ... in the future` warnings, and the ACC still absent →
  the clock hypothesis is **falsified**. Record that in
  [`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) §6.1.1 and
  stop treating firmware as the blocker.
- ACC appears → hypothesis **confirmed**, and re-triggering after NTP
  convergence is your workaround.
- `tar` warnings still present despite a correct clock → the
  timestamps come from somewhere other than the system clock; capture
  and report.

Either way, check whether `I_T nexus` gained an entry. An iSCSI login
would mean the ACC reached UEFI networking for the first time, which
moves the frontier even if it still fails afterwards.

## Session 8 — how far did the ACC actually get? (read-only) — **done, see §D.1**

Run this **before** rebooting or re-triggering anything. Session 9
destroys the state that session 7 left behind, and that state is the
only direct evidence we have ever had of a live ACC.

The question is whether the ACC is sitting in UEFI loading its kernel,
or logged in once and then died.

**Is the nexus live or a leftover?**

```sh
tgtadm --lld iscsi --op show --mode target
ss -tnp | grep 3260
ip neigh show | grep 192.168.0.2
```

An `ESTAB` socket to port 3260 from `192.168.0.2` plus an ARP entry
means the ACC is powered and its network stack is running right now.
If `tgtd` still lists the nexus but the socket is gone, the ACC logged
in and then disappeared — a different and worse story.

**Is it reading the LUN?** This is the most useful single signal. An
initiator that attached the disk and stalled reads nothing; one that is
loading a kernel reads tens of megabytes.

```sh
grep nvme0n1p4 /proc/diskstats; sleep 30; grep nvme0n1p4 /proc/diskstats
```

Field 6 is sectors read. Climbing across the window means the ACC is
still pulling its boot image. Frozen means it has stopped asking.

If `tcpdump` is present on this image, watching the wire for half a
minute says more than any log here:

```sh
timeout 30 tcpdump -ni any host 192.168.0.2 -c 200
```

**What the IMC saw around the login:**

```sh
journalctl -o short-iso --no-pager --since '-90 min' \
  | grep -iE 'acc|iscsi|tgtd|lancp|sm_rst|nexus|login'
cat /mnt/imc/acc_variable/acc-uefi-boot-config.json
```

`Error` populating would be a gift. It has been empty through every
failure so far because ACC UEFI never ran; now that it does, it may
have something to say.

**And finally locate the daemon's stdout**, still unread from §C.5:

```sh
find / -xdev -name nohup.out -exec ls -l {} \; 2>/dev/null
```

**Record two things before moving on:** whether the nexus is live, and
whether sectors are climbing. Sessions 9 and 10 branch on them.

## Session 9 — reproduce it, and isolate which change did it — **done, see §D.2-D.4 and §E.1**

Session 7 changed two things at once: the clock was correct, and
`ipu.service` was stopped so the force-start could actually take the
device. Before anything else, confirm what the config file held at the
time — session 7's log printed stage `0` / `enp0s1f0`, the *original*
overrides, not session 6's reverted values (§C.4):

```sh
cat /work/cfg/config.json
diff /work/cfg/config.json /work/cfg/config.json.bak
```

Use whatever session 7 used. Do not change it again in this session.

> **Stop if the nexus is non-empty.** `tgtd` lives in the
> `ipu.service` cgroup, so stopping the unit SIGKILLs the ACC's boot
> disk (§D.3). On the first attempt at this session that wedged the IMC
> badly enough to need a power cycle (§D.4). If the ACC has already
> attached, you do not need to force-start anything — go straight to
> the session 8 checks and then to session 10.

**Run A — plain repeat.** Prove it is not a one-off:

```sh
reboot
# once it is back, wait for the clock
until timedatectl status | grep -q 'synchronized: yes'; do sleep 10; done
date

# capture the boot journal BEFORE anything else - this is what §D.2 lost
journalctl -b -o short-iso --no-pager > /work/boot-$(date +%s).log
grep -cE 'in the future' /work/boot-*.log      # was the netboot window at epoch?

tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'
```

If that nexus is **empty**, the ACC did not attach on its own and the
force-start is safe:

```sh
systemctl stop ipu.service
ps -ef | grep -i ipumgmtd
/usr/bin/imc-scripts/force_start_init_app
```

If it is **populated**, §D.2 repeated: the ACC attaches without
intervention, the clock story in §C.3 is wrong or incomplete, and the
saved boot journal tells you whether the netboot ran at epoch. Leave
the service alone.

Session 7's SSH died at `post_init_app.sh` with `client_loop: send
disconnect`, because that script deletes and rewrites `port-setup.sh`
and reconfigures the ports your session rides on. Run it detached so
the next one survives, and so you can read the output that was lost:

```sh
screen -S acc      # or: tmux new -s acc
# run force_start_init_app inside it, detach with ctrl-a d
```

On reconnect check `uptime` — that also settles whether the IMC really
rebooted in session 7 or just dropped the link. Then repeat the
session 8 checks. Same result means the recipe is real.

**Run B — the control.** Only if run A reproduced *and* the nexus was
empty beforehand. This separates "correct clock" from "clean restart of
the daemon" by doing the identical sequence with a deliberately wrong
clock:

```sh
tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'   # must be empty before you proceed
systemctl stop ipu.service chronyd
timedatectl set-ntp false
date -s '1970-01-01 00:02:00'
/usr/bin/imc-scripts/force_start_init_app        # inside screen/tmux
# look for the tar '... in the future' warnings, then:
tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'
```

No login at epoch and a login with a real date confirms the clock as
the cause, and turns §6.1.1 of the runbook from hypothesis into
finding.

Put time back before you leave, whatever the outcome:

```sh
timedatectl set-ntp true
systemctl start chronyd
chronyc makestep
date
```

Tell the lab channel first. You are deliberately breaking time on a
shared box, and anything holding a TLS session or a build will notice.

## Session 10 — give the ACC a console — **done; edit did not persist, see §F.2**

Everything else is guesswork until this works. The ACC is a running
processor that produces no output, and §E.3 shows the vendor's own
tooling already knows where its UART is — it just does not wire it up
on the iSCSI boot path.

Back up the config, because `/mnt/imc/acc_variable` is the ACC's
persistent EFI variable store:

```sh
cp /mnt/imc/acc_variable/acc-config.json /work/acc-config.json.bak
cat /work/acc-config.json.bak
```

Append the two parameters the ramdisk branch uses. Keep everything
already there — `initrdmem` especially:

```sh
sed -i 's|acpi=force|console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force|' \
  /mnt/imc/acc_variable/acc-config.json
cat /mnt/imc/acc_variable/acc-config.json
```

The result should read:

```
ip=192.168.0.2:::255.255.255.0::enp0s1f0:off root=/dev/sda rw netroot=iscsi:192.168.0.1::::iqn.e2000:acc initrdmem=0x440c000000,0x8000000 console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force
```

> **Do not run `set_acc_kernel_cmdline.sh` after this.** It regenerates
> `boot_params` wholesale from the template in §E.3 and will silently
> drop the console parameters. If anything re-runs it — and
> `run_default_init_app` does, at line 419, whenever `acc-config.json`
> stops mentioning `ACC_NETBOOT_INTERFACE` — re-apply the `sed` and
> check before every boot.

Then reboot and find where the output went. There are three candidates
and they cost nothing to check in order:

```sh
reboot
# after it is back:
cat /work/acc-config.json.bak                       # confirm your edit survived
cat /mnt/imc/acc_variable/acc-config.json
dmesg | grep -iE 'ttyS|uart|console|acc'
ls /sys/class/tty/                                   # did a new tty appear?
journalctl -b --no-pager | grep -iE 'mev_imc_acc|uart|console'
```

If nothing surfaces on the IMC, the UART is physical and the question
goes to the lab maintainer (§"In parallel"). Either way you will know,
which is more than is true today.

**Why this is worth doing before anything else:** if the console
produces even one line, it distinguishes between a kernel that panics
at entry, one that unpacks the initrd and fails, and one that reaches
dracut and waits for `/dev/sda`. Those need completely different fixes,
and no IMC-side measurement can tell them apart.

## Session 11 — read `/dev/ttyFAM0` (do this first) — **done, idle read was empty**

Zero risk, no reboot, no service restart. The IMC's own console is
`ttyS0`, so nothing on the IMC is using this device.

```sh
ls -l /dev/ttyFAM0
stty -F /dev/ttyFAM0 -a
timeout 60 cat -v /dev/ttyFAM0 | tee /work/ttyfam0-idle.log
```

`cat -v` matters — it makes control characters visible instead of
letting them scramble your terminal.

The ACC is currently hung mid-boot (§E.2), so an idle read may show
nothing. That is not a failure; it means the ACC is not writing *now*.
The real test is reading across an ACC boot, which is session 12.

If the output is garbled rather than empty, the line discipline needs
setting first:

```sh
stty -F /dev/ttyFAM0 raw -echo 115200
timeout 60 cat -v /dev/ttyFAM0
```

Note `base_baud = 0` and `irq = 0` in the probe line — this is a polled
pseudo-UART, so the baud rate may well be ignored. Try it plain before
fighting with `stty`.

**Anything at all coming out of this device changes the whole
investigation**, because it would mean ATF or UEFI are writing to it
even without the kernel parameter, and you would have visibility into
stages that have never been observable.

## Session 12 — make the console survive a reboot, and capture it — **done, see §G; the `cat` needs `stty -echo`**

Implements §F.2. This creates one new file and touches no vendor file
persistently, so backing out is `rm`.

```sh
mkdir -p /work/scripts/pre_init_app.d
```

Create `/work/scripts/pre_init_app.d/99-acc-console.sh`:

```sh
#!/bin/sh
# Add a console to the ACC's iSCSI kernel command line. The generator
# rebuilds boot_params every boot and the IMC rootfs is a ramdisk, so
# both the patch and this file have to be re-applied from /work.
sed -i 's|initrdmem=${initrd_base},${initrd_size} acpi=force|initrdmem=${initrd_base},${initrd_size} console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force|' \
  /usr/bin/scripts/set_acc_kernel_cmdline.sh

# Capture the ACC console from before the netboot until something kills it.
[ -c /dev/ttyFAM0 ] && \
  setsid sh -c 'exec cat /dev/ttyFAM0' >> /work/acc-console.log 2>&1 &
```

The `sed` is single-quoted, so `${initrd_base}` stays literal and
matches the script source rather than expanding. `initrdmem=` appears
only in the iSCSI branch, so the ramdisk branch is untouched.

Then:

```sh
chmod +x /work/scripts/pre_init_app.d/99-acc-console.sh
reboot
```

After the reboot, verify the patch took before you read anything into
the result:

```sh
grep -c 'uartfam\|2058400000' /usr/bin/scripts/set_acc_kernel_cmdline.sh
grep boot_params /mnt/imc/acc_variable/acc-config.json
```

`boot_params` must now contain `console=ttyS0,115200
earlycon=uart8250,mmio32,0x2058400000`. If it does not, the drop-in did
not run or ran too late — check the boot log for `#### Running
/work/scripts/pre_init_app.sh` and the ordering around it.

Then read what the ACC said:

```sh
wc -c /work/acc-console.log
cat -v /work/acc-console.log
```

**How to read it:**

- Nothing at all → the ACC is not writing to this UART, and the console
  hunt moves to Redfish (session 14) and the lab maintainer.
- ATF / UEFI banners but no Linux → the kernel is dying before it can
  attach a console, which points at the initrd or the handoff itself.
- Kernel boot messages ending in a panic → read the panic. Done
  guessing.
- Kernel boot messages that stop at something like `dracut-initqueue:
  Warning: Could not boot` or a wait for `/dev/sda` → §E.4 is confirmed
  and session 13 is the fix.

## Session 13 — stop the capture typing into the ACC — **done, see §H; ACC now reaches GRUB**

Fixes §G.3. Until this is done every boot gets hijacked by the
instrumentation, so nothing else is measurable.

Keep the loop log as evidence and start clean:

```sh
mv /work/acc-console.log /work/acc-console-echoloop.log
```

Replace `/work/scripts/pre_init_app.d/99-acc-console.sh` with:

```sh
#!/bin/sh
# Add a console to the ACC's iSCSI kernel command line. The generator
# rebuilds boot_params every boot and the IMC rootfs is a ramdisk, so
# both the patch and this file have to be re-applied from /work.
sed -i 's|initrdmem=${initrd_base},${initrd_size} acpi=force|initrdmem=${initrd_base},${initrd_size} console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force|' \
  /usr/bin/scripts/set_acc_kernel_cmdline.sh

# Capture the ACC console READ-ONLY. The tty defaults to icanon+echo,
# which feeds every byte the ACC sends back to it as keystrokes and
# lands UEFI in its shell. Hold the fd open across the stty so the
# settings cannot revert between the two commands.
[ -c /dev/ttyFAM0 ] && setsid sh -c '
  exec 3< /dev/ttyFAM0
  stty -F /dev/ttyFAM0 raw -echo -echoe -echok -echoctl -echoke 115200
  exec cat <&3
' >> /work/acc-console.log 2>&1 &
```

`raw` clears `icanon`, `isig` and `opost`; it does **not** clear `echo`
on GNU coreutils, which is why `-echo` is listed separately along with
the four echo helpers. Opening fd 3 first and only then running `stty`
is what stops the settings reverting in the gap between the two.

```sh
chmod +x /work/scripts/pre_init_app.d/99-acc-console.sh
reboot
```

After the reboot, the single check that matters:

```sh
grep -c 'is not recognized' /work/acc-console.log     # must be 0
wc -c /work/acc-console.log
cat -v /work/acc-console.log
```

Any non-zero count means the loop is still live and the `stty` did not
take — do not interpret anything else in the log until it reads 0.

**Then read what the ACC does when nobody is typing at it:**

- Kernel messages after the UEFI banner → you are past UEFI for the
  first time, and whatever the kernel says is the answer to §E.2.
- Still `Shell>`, but clean and idle → UEFI is genuinely configured to
  boot the shell rather than `FS0:`, and session 14 sorts that out.
- A panic or a hang partway through kernel boot → read it, and §E.4
  stops being a theory either way.

## Session 14 — did Fedora actually boot? (read-only) — **done, see §I.1; it did not**

Resolves §H.3. No reboot, nothing destructive. **Run the first check
before drawing any conclusion from the silent log**, because a dead
capture and a dead kernel look identical:

```sh
ps -ef | grep -i ttyFAM0        # is the cat still alive?
wc -c /work/acc-console.log     # has it grown since you last looked?
```

If the `cat` is gone, restart it and reboot before going further —
everything below assumes the log is trustworthy.

**The decisive check is the iSCSI initiator name.** UEFI logs in as
`iqn.1986-03.com.intel:e2000.acc-client...`. A booted Fedora running
`iscsid` would open its own session with a completely different IQN,
almost certainly `iqn.1994-05.com.redhat:...`:

```sh
tgtadm --lld iscsi --op show --mode target | grep -A6 'I_T nexus'
```

A Red Hat IQN, or two nexuses, means **Fedora is up and has mounted its
root filesystem over iSCSI**. That would make this bring-up essentially
finished and only the console invisible.

Then the supporting evidence:

```sh
grep nvme0n1p4 /proc/diskstats; sleep 30; grep nvme0n1p4 /proc/diskstats
ping -c3 192.168.0.2
cli_client -q -c | grep -i 'fn_id: 0x4'
ip neigh show 192.168.0.2
```

A running Fedora on an iSCSI root generates continuous reads, so the
sector count climbing well past 164232 is close to proof on its own.
Ping answering means the kernel brought up networking, which UEFI's
stack never did (§D.1).

**How to read the combination:**

- Red Hat IQN + climbing reads + ping → booted. Go to session 15 only
  to make the console visible for future work, and start planning the
  actual ODSE bring-up on top.
- Intel IQN only, reads flat, no ping → the kernel is dying at handoff.
  Session 15 is then mandatory, because you cannot debug it blind.
- Reads climbing but no ping → it is booting right now. Wait and
  re-check before concluding anything.

## Session 15 — put a console on the Fedora kernel — **not done; wrong loop device, see §I.3**

§H.2 explains why the console stops at GRUB: the kernel command line
comes from GRUB's config on the ACC's boot partition, not from
`acc-config.json`, and it has no `console=` for this UART.

The ACC's disk is `/dev/nvme0n1p4` on the IMC — the same block device
`tgtd` serves — so the IMC can read its partitions directly.

> **Do not mount it read-write while the ACC is running.** Two
> operating systems writing one filesystem corrupts it. Survey
> read-only first, and only remount read-write once the ACC is
> confirmed halted.

```sh
losetup -Pf --show /dev/nvme0n1p4      # prints e.g. /dev/loop0, with loop0p1..p3
lsblk /dev/loop0
mkdir -p /mnt/accboot
mount -o ro /dev/loop0p2 /mnt/accboot  # p2 is the ~1 GB partition, likely /boot
ls /mnt/accboot
ls /mnt/accboot/loader/entries/
cat /mnt/accboot/loader/entries/*.conf
```

Fedora with ostree uses Boot Loader Specification entries, so expect an
`options` line in each `.conf`. That is the kernel command line GRUB
actually uses.

Once you have confirmed the ACC is not running, remount and append the
console arguments to the `options` line:

```sh
mount -o remount,rw /mnt/accboot
cp -a /mnt/accboot/loader/entries /work/acc-bls-entries.bak
sed -i 's|^options .*|& console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000|' \
  /mnt/accboot/loader/entries/*.conf
grep ^options /mnt/accboot/loader/entries/*.conf
```

While you are in there, give yourself room to interrupt GRUB next time.
A one-second timeout is unusable over this link:

```sh
grep -n 'timeout' /mnt/accboot/grub2/grub.cfg
# set timeout=10 if you can do it safely
```

Then unmount cleanly before letting the ACC touch the disk again:

```sh
umount /mnt/accboot
losetup -d /dev/loop0
```

Reboot and read the log. You should now see kernel messages after
`Booting 'Fedora Linux 43 ...'` — and whatever they say is the answer
this investigation has been trying to get at since §E.2.

If mounting turns out to be unsafe or the partition layout is not what
you expect, the fallback is to interrupt GRUB interactively instead.
The console is bidirectional, so with a longer timeout you can press a
key to hold the menu, `e` to edit the entry, append the same two
arguments to the `linux` line, and `Ctrl-X` to boot. That is per-boot
only, but it proves the fix before you make it permanent.

## Session 16 — read the Fedora command line (read-only) — **done; IMC cannot mount vfat/xfs, see §J**

This is the one remaining cheap step. No reboot, no writes. Two jobs:
keep the console logger from exiting at handoff, and actually look at
the files session 15 missed.

**1. See which loop device is which, and clean up if needed:**

```sh
losetup -a
lsblk
```

`loop0` should still be `acc_variable`. If `loop1` is already bound to
`nvme0n1p4` from session 15, use it. If not:

```sh
losetup -Pf --show /dev/nvme0n1p4
```

Use **that** name below, not `loop0`.

**2. Mount the ESP and `/boot` read-only:**

```sh
# replace loop1 with whatever losetup printed
lsblk /dev/loop1
mkdir -p /mnt/accp1 /mnt/accp2
mount -o ro /dev/loop1p1 /mnt/accp1
mount -o ro /dev/loop1p2 /mnt/accp2
ls -la /mnt/accp1 /mnt/accp2
find /mnt/accp1 /mnt/accp2 -iname '*.conf' -o -iname 'grub.cfg' -o -iname 'grubenv' | head -50
```

Then dump whatever command line GRUB is actually using:

```sh
cat /mnt/accp1/loader/entries/*.conf 2>/dev/null
cat /mnt/accp2/loader/entries/*.conf 2>/dev/null
grep -R --include='*.conf' --include='grub.cfg' -nE '^(options|linux|initrd) ' \
  /mnt/accp1 /mnt/accp2 2>/dev/null | head -80
```

What you are looking for on the `options` / `linux` line:

- `rd.iscsi.` / `netroot=iscsi` / `ip=` — without these the kernel
  cannot reopen the LUN after UEFI tears down iSCSI, which matches
  §I.1 exactly.
- `console=` / `earlycon=` pointing at `0x2058400000` or `ttyS0` —
  without these the kernel is silent on `ttyFAM0` even if it is
  running.
- `root=` — ostree usually uses `ostree=` plus a mapper or UUID, not
  `/dev/sda`.

Copy the full `options` line into the capture file. That single string
is the next decision.

Unmount when done, leave the disk alone:

```sh
umount /mnt/accp2 /mnt/accp1
# only detach the ACC loop, never loop0 / acc_variable
losetup -d /dev/loop1
```

**3. Optional, if you still have energy — make the console logger
survive UART reprogramming:**

Replace the `exec cat <&3` in
`/work/scripts/pre_init_app.d/99-acc-console.sh` with a restart loop
so EOF at kernel entry does not kill the capture:

```sh
[ -c /dev/ttyFAM0 ] && setsid sh -c '
  exec 3<> /dev/ttyFAM0
  stty -F /dev/ttyFAM0 raw -echo -echoe -echok -echoctl -echoke 115200
  while :; do cat <&3; sleep 0.2; done
' >> /work/acc-console.log 2>&1 &
```

Do **not** reboot just to test this tonight. It starts mattering the
next time you boot anyway.

**Stop after the command-line dump.** Do not edit BLS, do not change
`acc-config.json`, do not touch Redfish. Session 17 is only worth
opening once you have that string.

## Session 17 — get the GRUB command line without mounting — **done, see §K**

Three attempts, cheapest first. Stop when you have a full `options` or
`linux` line.

**1. Load the modules, then remount.** The IMC may have them and just
not autoload them:

```sh
cat /proc/filesystems
ls /lib/modules/$(uname -r)/kernel/fs 2>/dev/null
modprobe nls_cp437 nls_iso8859-1 fat vfat
modprobe xfs
cat /proc/filesystems | grep -iE 'fat|xfs'
```

If `vfat` and `xfs` now appear, go back to session 16's mount commands
on `loop1p1` / `loop1p2`. If `modprobe` says the module is missing,
skip to step 2. Do not spend time building a kernel.

**2. `strings` the `/boot` partition.** BLS entries and `grub.cfg` are
plain text sitting in the XFS filesystem. No mount needed:

```sh
# confirm loop1 is still nvme0n1p4
losetup -a
strings -n 8 /dev/loop1p2 | grep -E 'ostree=|rd.iscsi|netroot=|root=|console=|earlycon=|^options |linuxefi |linux /' | head -80
```

If that is noisy, dump a copy you can grep on later (1 GiB, `/work` is
3 GiB):

```sh
dd if=/dev/loop1p2 of=/work/acc-p2-boot.img bs=1M status=progress
strings -n 8 /work/acc-p2-boot.img | grep -E 'ostree=|rd.iscsi|netroot=|console=|earlycon=' | head -80
```

The line you want looks like Fedora ostree BLS, for example
`options root=UUID=... ostree=/ostree/boot.1/...` plus whatever iSCSI
and console arguments are or are not there.

Same trick on the ESP if needed:

```sh
strings -n 8 /dev/loop1p1 | grep -iE 'EFI|fedora|grub|BOOTAA64' | head -40
```

**3. If `strings` is not installed or the output is unreadable, copy
p2 to DH4 and mount it there.** The host is a normal Linux box and
will have `xfs` / `vfat`:

```sh
# on DH4, not on the IMC
ssh root@172.22.4.4 'dd if=/dev/loop1p2 bs=1M' > /tmp/acc-p2-boot.img
sudo mkdir -p /mnt/accboot
sudo mount -o loop,ro /tmp/acc-p2-boot.img /mnt/accboot
find /mnt/accboot -iname '*.conf' -o -iname 'grub.cfg'
```

Leave `loop1` attached until that `dd` finishes. Still read-only.

Once you have the command line, the decision is mechanical:

- No `rd.iscsi` / `netroot=iscsi` / `ip=` → the kernel cannot reopen
  the LUN after UEFI exits. That is the session 14 failure, and the
  next edit (when you have energy) is that BLS `options` line, done
  from the host, not from the IMC.
- No `console=` / `earlycon=` → kernel remains silent on `ttyFAM0`
  even if it is running. Add those too.
- Both present and the kernel still dies → then you need console
  output, and the restart-loop logger in session 16 step 3 is the
  next boot, not more config guessing.

## Session 18 — read the mounted BLS entry (on DH4, sudo) — **done**

The image is already at `/mnt/accboot`. `find` without sudo missed
`grub2` and did not print `ostree-1.conf` contents.

If the terminal looks broken (`xterm-256color` repeating), run `reset`
first. That was binary from `grep -a` leaking into the shell.

```sh
# on DH4
reset
sudo ls -la /mnt/accboot
sudo cat /mnt/accboot/loader.1/entries/ostree-1.conf
sudo find /mnt/accboot -iname '*.conf' -o -iname 'grub.cfg' -o -iname 'grubenv'
sudo cat /mnt/accboot/grub2/grubenv 2>/dev/null
```

Paste `ostree-1.conf` in full. Confirm it matches §K. Still do not
write, umount, or reboot.

## Session 19 — put a console on the Fedora command line

On DH4, confirm the paste wrapping was the terminal, not the file:

```sh
sudo cat -A /mnt/accboot/loader.1/entries/ostree-1.conf
```

One `options` line, `$` only at EOL. Then append the ACC UART on the
already-mounted **copy** (`/tmp/acc-p2-boot.img`), not the live LUN:

```sh
sudo cp -a /mnt/accboot/loader.1/entries/ostree-1.conf /tmp/ostree-1.conf.bak
sudo sed -i 's/ acpi=force / console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force /' \
  /mnt/accboot/loader.1/entries/ostree-1.conf
sudo cat /mnt/accboot/loader.1/entries/ostree-1.conf
sudo umount /mnt/accboot
```

Write back and reboot the IMC immediately (iSCSI still has the LUN):

```sh
ssh root@172.22.4.4 'dd of=/dev/loop1p2 bs=1M conv=fsync' < /tmp/acc-p2-boot.img
ssh root@172.22.4.4 reboot
```

Then:

```sh
ssh root@172.22.4.4 'wc -c /work/acc-console.log; tail -c 8000 /work/acc-console.log | cat -v'
```

Do not change `ip=` until dracut is readable.

## Session 19 — put a console on the Fedora command line — **not applied; mount was ro**

`cat -A` confirmed a single clean `options` line (no stray spaces).
`sed -i` then failed:

```
sed: couldn't open temporary file /mnt/accboot/loader.1/entries/sed9whGFC: Read-only file system
```

The image had been mounted `-o loop,ro`. The file was unchanged. The
following `dd` wrote the **original** 1 GiB back to `loop1p2`. After
reboot, `/work/acc-console.log` is 7536 bytes — three copies of
ATF → UEFI → GRUB → `Booting Fedora`, still no kernel text.

## Session 20 — same edit, writable mount

The copy at `/tmp/acc-p2-boot.img` is still the right place. Remount
it read-write, then the same `sed`:

```sh
# on DH4
mount | grep accboot || true
sudo mkdir -p /mnt/accboot
sudo mount -o loop,rw,nouuid /tmp/acc-p2-boot.img /mnt/accboot
sudo cp -a /mnt/accboot/loader.1/entries/ostree-1.conf /tmp/ostree-1.conf.bak
sudo sed -i 's/ acpi=force / console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force /' \
  /mnt/accboot/loader.1/entries/ostree-1.conf
grep -E 'console=|earlycon=' /mnt/accboot/loader.1/entries/ostree-1.conf
sudo umount /mnt/accboot
```

`grep` must print `console=ttyS0,115200` **before** you `dd`. If the
mount says the log is dirty, copy the image first:

```sh
cp /tmp/acc-p2-boot.img /tmp/acc-p2-boot-rw.img
sudo mount -o loop,rw,nouuid /tmp/acc-p2-boot-rw.img /mnt/accboot
# ... sed as above, umount, then dd the -rw.img
```

Then:

```sh
ssh root@172.22.4.4 'dd of=/dev/loop1p2 bs=1M conv=fsync' < /tmp/acc-p2-boot.img
ssh root@172.22.4.4 reboot
```

(Use `acc-p2-boot-rw.img` on the `dd` stdin if that is the file you
edited.) After SSH is back:

```sh
ssh root@172.22.4.4 'grep -c Booting /work/acc-console.log; tail -c 12000 /work/acc-console.log | cat -v'
```

## Session 20 — same edit, writable mount — **sed succeeded; kernel still silent**

The `options` line on the image now contains
`console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000`. That
`dd` used `/tmp/acc-p2-boot.img` (the file that was edited). After
reboot, `Booting` appears four times and there is still no kernel
text.

Two things can still hide the kernel: the live LUN might not contain
that string (stale `loop1` after the session 19 reboot), and the
`ttyFAM0` `cat` still exits at handoff (§I.2). Session 21 checks
both before any further BLS edits.

## Session 21 — prove the edit is on the LUN, and keep the logger alive

**1. On the IMC**, no loop device needed — `nvme0n1p4` is the whole
ACC disk:

```sh
grep -a 'earlycon=uart8250,mmio32,0x2058400000' /dev/nvme0n1p4 | head -5
```

If that prints nothing, GRUB never saw the edit. Re-`losetup -Pf`
and `dd` again. If it prints the `options` line, the BLS change is
on disk.

**2. Make the console logger survive UART reprogramming.** Skip this
until session 22's log is in. It was not needed while the LUN still
lacked `console=`.

## Session 21 — prove the edit is on the LUN — **done**

`losetup -Pf` created `/dev/loop1` with p1/p2/p3. `dd` of
`/tmp/acc-p2-boot.img` to `/dev/loop1p2` succeeded. `grep -a` on
`/dev/nvme0n1p4` then printed the full `options` line including
`console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000`. The
edit is on the disk GRUB reads.

## Session 22 — read the boot that has console= (already rebooted)

No writes. The IMC is back; `/dev/loop1` will be gone again and that
is fine.

```sh
ps -ef | grep -i ttyFAM0
wc -c /work/acc-console.log
grep -c 'Booting' /work/acc-console.log
tail -c 20000 /work/acc-console.log | cat -v
```

Then the same liveness checks as session 14:

```sh
tgtadm --lld iscsi --op show --mode target | grep -A6 'I_T nexus'
grep nvme0n1p4 /proc/diskstats
ping -c3 192.168.0.2
cli_client -q -c | grep -i 'fn_id: 0x4'
```

**How to read it:**

- Kernel lines after `Booting Fedora` (dracut, panic, `waiting for
  root`) → paste them. That is the answer.
- Log still ends at `Booting Fedora` **and** `ps` shows no `cat` on
  `ttyFAM0` → the logger exited at handoff. Then session 23 is the
  restart loop (it was skipped on purpose until the LUN was correct).
- Log still ends at `Booting Fedora` **and** `cat` is still running →
  Fedora is not writing to this UART. Different `console=` next, not
  more IMC plumbing.
- Red Hat / Fedora IQN, climbing diskstats, or ping → Linux took
  over the LUN even if the console is quiet.

## Session 22 — read the boot that has console= — **done; kernel panic in EFI runtime map**

The logger died again (`ps` only shows `grep`), but it kept enough:
`/work/acc-console.log` is 25748 bytes and contains a full Fedora
boot through panic.

```
Linux version 7.0.4-100.fc43.aarch64 ...
earlycon: uart8250 at MMIO32 0x0000002058400000
efi: EFI v2.7 by EDK II
ACPI: iBFT ...
ACPI: SPCR: console: uart,mmio32,0x2058400000,115200
Kernel command line: ... console=ttyS0,115200 earlycon=uart8250,mmio32,0x2058400000 acpi=force ostree=...
Remapping and enabling EFI services.
kernel BUG at arch/arm64/mm/mmu.c:190!
 pc : alloc_init_cont_pte+0x17c/0x280
 efi_create_mapping -> efi_virtmap_init -> arm_enable_runtime_services
Kernel panic - not syncing: Attempted to kill init!
```

PID 1 (`swapper/0`) dies in an early initcall while building EFI
runtime page tables for phys `0x4400700000` (ACC DRAM next to the
BMD window). The kernel never reaches dracut, iSCSI reconnect, or
`enp0s1f0`. iBFT is already in the ACPI tables; runtime EFI is not
required to continue.

iSCSI is still the UEFI initiator; diskstats still ~80 MiB; no ping;
no `fn_id: 0x4`. Expected after this panic.

## Session 23 — `efi=noruntime` on the same BLS line

Same write path as session 21. Do **not** skip `losetup` or the
pre-reboot `grep` on `nvme0n1p4`.

On DH4, remount the image that already has `console=` and add the
parameter:

```sh
sudo mount -o loop,rw,nouuid /tmp/acc-p2-boot.img /mnt/accboot
sudo sed -i 's/ acpi=force / efi=noruntime acpi=force /' \
  /mnt/accboot/loader.1/entries/ostree-1.conf
grep -E 'efi=noruntime|earlycon=' /mnt/accboot/loader.1/entries/ostree-1.conf
sudo umount /mnt/accboot
```

On the IMC:

```sh
LOOP=$(losetup -Pf --show /dev/nvme0n1p4)
echo LOOP=$LOOP
lsblk "$LOOP"
```

From DH4 (substitute the printed loop, usually `/dev/loop1p2`):

```sh
ssh root@172.22.4.4 "dd of=/dev/loop1p2 bs=1M conv=fsync" < /tmp/acc-p2-boot.img
```

On the IMC **before reboot**. Do **not** grep bare `efi=noruntime` —
that string is also in the kernel's `noefi` help text inside
`vmlinuz` (`already be achieved by using the efi=noruntime option,
but it could be`). `head` will show that comment even when the BLS
line was never written.

Use a fragment that only exists on the `options` line:

```sh
grep -a 'efi=noruntime acpi=force' /dev/nvme0n1p4 | head -3
```

Only if that prints the `options` line (it must include
`root=/dev/mapper/rhel-root` and `earlycon=`). If you only see the
`noefi` comment, or grep is empty, stop: remount
`/tmp/acc-p2-boot.img` on DH4, `cat` `loader.1/entries/ostree-1.conf`,
re-run the `sed` if `efi=noruntime` is missing, `umount`, `losetup`
again, `dd` again, then re-grep.

```sh
reboot
```

After SSH is back, same read as session 22:

```sh
tail -c 30000 /work/acc-console.log | cat -v
tgtadm --lld iscsi --op show --mode target | grep -A6 'I_T nexus'
```

If it panics in the same `efi_create_mapping` path, try `noefi`
instead of `efi=noruntime` (stronger; still usually leaves iBFT in
the already-copied ACPI tables). If it gets past EFI into dracut,
that log is the next failure — not this one.

## Session 23 — `efi=noruntime` on the same BLS line — **done; do not losetup again**

The first `dd` of this session went to `/dev/loop1p2` **before**
`losetup`. Same trap as session 19: that node was not the `/boot`
partition, so `grep -a 'efi=noruntime' /dev/nvme0n1p4` only hit the
`noefi` help text in `vmlinuz`.

`losetup -Pf --show /dev/nvme0n1p4` was then run twice without
`losetup -d`. First call created `/dev/loop1`. Second call created
`/dev/loop2` on the **same** `/dev/nvme0n1p4`. No reboot is involved;
the next free loop number is used whenever the backing device is
already attached. `lsblk` for both shows the same 600M / 1G / 42.3G
layout.

The second `dd` to `/dev/loop2p2` landed. Unique grep on the LUN
printed the BLS `options` line (twice — likely the xfs file plus a
journal/old extent) including `efi=noruntime acpi=force`. Paste wrap
in the capture ate characters (`orce`, `runtime acpi=force`); the
search string that matched is the real gate.

`loop1` and `loop2` can both stay attached until reboot. Do **not**
run `losetup -Pf` a third time, and do **not** `dd` to a guessed
`loopNp2`.

## Session 24 — reboot and read the `efi=noruntime` boot

No writes. No `losetup`. Reboot the IMC:

```sh
losetup -a
reboot
```

`losetup -a` should show `loop0` = `acc_variable` and both `loop1`
and `loop2` on `nvme0n1p4`. That is expected. After SSH is back:

```sh
tail -c 30000 /work/acc-console.log | cat -v
tgtadm --lld iscsi --op show --mode target | grep -A6 'I_T nexus'
grep nvme0n1p4 /proc/diskstats
ping -c3 192.168.0.2
cli_client -q -c | grep -i 'fn_id: 0x4'
```

**How to read it:**

- Command line contains `efi=noruntime` and there is **no**
  `Remapping and enabling EFI services` / `efi_create_mapping` /
  `mmu.c:190` → EFI runtime is skipped. Whatever follows (dracut,
  iSCSI, `enp0s1f0`) is the new problem — paste that log.
- Same `kernel BUG at arch/arm64/mm/mmu.c:190` → `efi=noruntime` did
  not take (wrong BLS file) or is not enough. Next edit is `noefi` on
  the same `options` line, same `losetup`+`dd`+unique-grep path.
- Command line lacks `efi=noruntime` → GRUB did not read this BLS
  entry; stop and recapture `ostree-1.conf` from `loop2p2` on DH4.

## Session 24 — reboot and read the `efi=noruntime` boot — **done; past EFI, stuck in dracut**

`losetup -a` showed `loop0` = `acc_variable` and both `loop1` and
`loop2` on `nvme0n1p4`, then reboot. The `tail -c 30000` of
`/work/acc-console.log` is only the last minutes of dracut timeout
spam. The full log is
[`../../example-output/acc-console.output`](../../example-output/acc-console.output):
an older session-22 panic, then this boot.

This boot's command line has `efi=noruntime`. The kernel prints
`EFI runtime services will be disabled.` (no `efi_create_mapping`,
no `mmu.c:190`). All 16 CPUs come up. `ttyS0` is `INTC1006:00` at
`0x2058400000`. systemd starts Fedora 43 `dracut-107-8.fc43`.

Then iSCSI root setup fails:

```
iBFT detected.
iscsi: registered transport (tcp)
90-parse-iscsiroot.sh: line 151: /lib/net-lib.sh: No such file or directory
/sbin/iscsiroot: line 10: /lib/net-lib.sh: No such file or directory
nvme 0000:00:01.1: error -ENODEV: probe failed
Warning: Could not boot.
Warning: /dev/mapper/rhel-root does not exist
```

PCI already listed `0000:00:01.0` as `8086:1452` (IDPF ethernet, the
same ID the IMC binds with `idpf` → `enp0s1f0`). There is **no**
`idpf` probe, no netdev, no Linux iSCSI login. IMC `I_T nexus` is
still the UEFI initiator; diskstats still ~80 MiB; no ping; no
`fn_id: 0x4`. Expected: Linux never opened the target.

Two stacked reasons iSCSI cannot reconnect, before `enp0s1f0` vs
`enaintc10c4i0` even matters:

1. Dracut's iscsi scripts abort because `/lib/net-lib.sh` is not in
   this initramfs (Fedora 43 dropped network-legacy; `95iscsi` still
   sources it).
2. Nothing binds `8086:1452`, so there is no NIC for iBFT or
   `ip=...enp0s1f0:off` to use. `:off` would also skip autoconfig
   even if the name existed.

Do **not** reboot. Do **not** stop `ipu.service`. The ACC is sitting
in the dracut emergency shell; that is harmless. Session 25 asks
whether `idpf` and `net-lib.sh` are in the initramfs GRUB already
loaded.

## Session 25 — list the Fedora initramfs (DH4, read-only)

No IMC writes. No reboot. Mount the same `/boot` image used for the
BLS edits and list the initramfs that matches the running kernel
(`7.0.4-100.fc43.aarch64`).

```sh
sudo mkdir -p /mnt/accboot
sudo mount -o loop,ro,nouuid /tmp/acc-p2-boot.img /mnt/accboot
INITRD=/mnt/accboot/ostree/default-5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/initramfs-7.0.4-100.fc43.aarch64.img
ls -l "$INITRD"
file "$INITRD"
```

If `lsinitrd` exists:

```sh
lsinitrd "$INITRD" | grep -iE 'net-lib\.sh|idpf|iscsi_ibft|iscsi_tcp|icc_net|40network|35network|95iscsi|network-legacy' | head -120
```

If it does not, extract the trailing zstd cpio (Fedora early+main
layout) and list those names:

```sh
python3 - "$INITRD" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
data = p.read_bytes()
idx = data.find(b"\x28\xb5\x2f\xfd")
print("zstd_offset", idx, "bytes", len(data))
Path("/tmp/acc-initrd.zst").write_bytes(data[idx:] if idx >= 0 else data)
PY
zstd -d -c /tmp/acc-initrd.zst | cpio -t 2>/dev/null \
  | grep -iE 'net-lib\.sh|idpf|iscsi_ibft|iscsi_tcp|icc_net' | head -120
```

Leave `/mnt/accboot` mounted. Session 26 uses it. Do **not** reboot
yet. If you must unmount, remount `rw` at the start of 26.

**How to read it, then run exactly one of 26A / 26B:**

- Grep shows `idpf` (any `idpf.ko*`) → **26A**. The module is in the
  initramfs and was never loaded.
- Grep does **not** show `idpf` → **26B**. Try to inject the Fedora
  aarch64 module. If that download fails, stop; do not reboot.
- `net-lib.sh` is already known missing from session 24. Both 26A and
  26B drop a stub so `95iscsi` can start. Firmware iSCSI
  (`rd.iscsi.firmware` / iBFT) does not need the real `45net-lib`.

## Session 26 — load `idpf` and let `iscsiroot` start (pick 26A or 26B)

No IMC reboot until the unique `grep` on `nvme0n1p4` prints the new
`options` line. After session 24, loop devices are gone; `losetup
-Pf` once and use **that** `loopNp2`. Do not guess `loop1` vs `loop2`.

### 26A — session 25 listed `idpf`

On DH4, `/mnt/accboot` still mounted (or remount `rw`):

```sh
sudo mount -o loop,rw,nouuid /tmp/acc-p2-boot.img /mnt/accboot
BLS=/mnt/accboot/loader.1/entries/ostree-1.conf
INITRD=/mnt/accboot/ostree/default-5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/initramfs-7.0.4-100.fc43.aarch64.img
sudo grep -E 'efi=noruntime|rd.driver.pre' "$BLS"
sudo sed -i 's/ efi=noruntime / rd.driver.pre=idpf efi=noruntime /' "$BLS"
sudo grep -E 'rd.driver.pre=idpf|efi=noruntime' "$BLS"
```

If `sed` did not insert `rd.driver.pre=idpf` (already present, or the
` efi=noruntime ` pattern missed), stop and `cat "$BLS"` instead of
writing the LUN.

Unpack the trailing zstd cpio, add `/lib/net-lib.sh`, repack. This
does **not** replace the early cpio:

```sh
python3 - "$INITRD" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
data = p.read_bytes()
idx = data.find(b"\x28\xb5\x2f\xfd")
if idx < 0:
    raise SystemExit("no zstd magic")
Path("/tmp/acc-initrd-early.cpio").write_bytes(data[:idx])
Path("/tmp/acc-initrd.zst").write_bytes(data[idx:])
print("early", idx, "zstd", len(data) - idx)
PY
rm -rf /tmp/acc-initrd-root
mkdir /tmp/acc-initrd-root
zstd -d -c /tmp/acc-initrd.zst | (cd /tmp/acc-initrd-root && sudo cpio -id --no-absolute-filenames)
sudo tee /tmp/acc-initrd-root/lib/net-lib.sh >/dev/null <<'EOF'
#!/bin/sh
# Stub: Fedora 43 95iscsi sources this and dies if it is absent.
# handle_firmware (rd.iscsi.firmware) does not need parse_iscsi_root.
parse_iscsi_root() { return 1; }
is_ip() { return 0; }
all_ifaces_setup() { return 0; }
EOF
sudo chmod 755 /tmp/acc-initrd-root/lib/net-lib.sh
(cd /tmp/acc-initrd-root && sudo find . | sudo cpio -H newc -o --quiet) \
  | zstd -19 -T0 > /tmp/acc-initrd-main.zst
cat /tmp/acc-initrd-early.cpio /tmp/acc-initrd-main.zst | sudo tee "$INITRD" >/dev/null
sudo sync
```

Confirm both edits on the image:

```sh
grep rd.driver.pre=idpf "$BLS"
zstd -d -c /tmp/acc-initrd-main.zst | cpio -t 2>/dev/null | grep net-lib.sh
sudo umount /mnt/accboot
```

Then the session-23 write path. On the IMC:

```sh
losetup -a
LOOP=$(losetup -Pf --show /dev/nvme0n1p4)
echo LOOP=$LOOP
lsblk "$LOOP"
```

From DH4 (substitute the printed loop, usually `loop1` after a reboot):

```sh
ssh root@172.22.4.4 "dd of=${LOOP}p2 bs=1M conv=fsync" < /tmp/acc-p2-boot.img
```

If `$LOOP` is not in that DH4 shell, type the path `lsblk` printed,
e.g. `/dev/loop1p2`. On the IMC **before reboot**:

```sh
grep -a 'rd.driver.pre=idpf efi=noruntime' /dev/nvme0n1p4 | head -3
```

Only if that prints the `options` line (`root=/dev/mapper/rhel-root`
and `earlycon=`):

```sh
reboot
```

After SSH is back:

```sh
tail -c 40000 /work/acc-console.log | cat -v
tgtadm --lld iscsi --op show --mode target | grep -A8 'I_T nexus'
grep nvme0n1p4 /proc/diskstats
ping -c3 192.168.0.2
cli_client -q -c | grep -i 'fn_id: 0x4'
```

**How to read 26A:**

- `idpf 0000:00:01.0` probe, and iSCSI from a **non-UEFI** IQN or
  climbing diskstats → Linux has the LUN. Paste that log; that is a
  new problem (LVM, `enp0s1f0`, ostree).
- `idpf` probe but still `net-lib.sh` or `/dev/mapper/rhel-root does
  not exist` → NIC is up, firmware login failed. Paste the new
  `iscsiadm` / `ibft` lines; do not edit `enp0s1f0` yet.
- Still no `idpf` line → `rd.driver.pre` did not load the module
  (wrong initramfs written, or the `.ko` does not match this PCI ID).
  Stop.

### 26B — session 25 listed **no** `idpf`

Do **not** jump to 26A. Run the blocks below **in order** on DH4
unless a block says IMC. Stop if any check fails. Do not reboot until
step 8's `grep` prints the `options` line.

The host kernel is x86_64; this RPM is aarch64. Live
`dl.fedoraproject.org` trees do **not** have `7.0.4-100.fc43` (404).
Koji does. Verified 2026-09-17: HTTP 200, `application/x-rpm`,
43032613 bytes, contains `idpf.ko.xz` plus `libeth` / `libie`.

#### 26B.1 Download and extract `idpf` from Koji

```sh
mkdir -p /tmp/fc43-kmod && cd /tmp/fc43-kmod
KVER=7.0.4-100.fc43.aarch64
RPM=kernel-modules-core-7.0.4-100.fc43.aarch64.rpm
curl -fL -O "https://kojipkgs.fedoraproject.org/packages/kernel/7.0.4/100.fc43/aarch64/$RPM"
file "$RPM"
ls -l "$RPM"
rpm2cpio "$RPM" | cpio -idmv \
  "./lib/modules/$KVER/kernel/drivers/net/ethernet/intel/idpf/*" \
  "./lib/modules/$KVER/kernel/drivers/net/ethernet/intel/libeth/*" \
  "./lib/modules/$KVER/kernel/drivers/net/ethernet/intel/libie/*"
find ./lib/modules/$KVER/kernel/drivers/net/ethernet/intel -type f
```

`find` must print at least:

```
./lib/modules/7.0.4-100.fc43.aarch64/kernel/drivers/net/ethernet/intel/idpf/idpf.ko.xz
./lib/modules/7.0.4-100.fc43.aarch64/kernel/drivers/net/ethernet/intel/libeth/libeth.ko.xz
./lib/modules/7.0.4-100.fc43.aarch64/kernel/drivers/net/ethernet/intel/libie/libie.ko.xz
```

If `curl` failed, `file` is not RPM, or `find` is empty: **stop here.
Do not continue. Do not reboot.**

#### 26B.2 Unmount the session-25 **ro** mount, remount **rw**, edit BLS

Session 25 left `/mnt/accboot` mounted `ro`. A second `mount -o rw`
does **not** remount; it prints `already mounted` and `sed -i` then
fails with `Read-only file system`. That is why `rd.driver.pre=idpf`
is missing: the `s/ efi=noruntime / ... /` pattern matches, but sed
cannot write.

```sh
cd ~
sudo umount /mnt/accboot
mount | grep accboot || echo "unmounted"
sudo mkdir -p /mnt/accboot
sudo mount -o loop,rw,nouuid /tmp/acc-p2-boot.img /mnt/accboot
mount | grep accboot
BLS=/mnt/accboot/loader.1/entries/ostree-1.conf
INITRD=/mnt/accboot/ostree/default-5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/initramfs-7.0.4-100.fc43.aarch64.img
ls -l "$BLS" "$INITRD"
sudo grep -E 'efi=noruntime|rd.driver.pre' "$BLS"
sudo sed -i 's/ efi=noruntime / rd.driver.pre=idpf efi=noruntime /' "$BLS"
sudo grep -E 'rd.driver.pre=idpf|efi=noruntime' "$BLS"
```

`mount | grep accboot` must show `rw`. If umount says target is busy,
`cd ~` and umount again. The second `grep` must show
`rd.driver.pre=idpf` on the `options` line. If sed again says
`Read-only file system`, stop — the remount did not take.

#### 26B.3 Unpack the initramfs (keep the early cpio, unpack the zstd part)

The initramfs is `600 root` (session 25: `file` said `no read
permission` without sudo). Run this whole block as the same user, with
`sudo` on the python, the extract, and the `ls`. Re-set `INITRD` if
this is a new shell.

```sh
INITRD=/mnt/accboot/ostree/default-5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/initramfs-7.0.4-100.fc43.aarch64.img
sudo python3 - "$INITRD" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
data = p.read_bytes()
idx = data.find(b"\x28\xb5\x2f\xfd")
if idx < 0:
    raise SystemExit("no zstd magic")
Path("/tmp/acc-initrd-early.cpio").write_bytes(data[:idx])
Path("/tmp/acc-initrd.zst").write_bytes(data[idx:])
print("early", idx, "zstd", len(data) - idx)
PY
sudo rm -rf /tmp/acc-initrd-root
sudo mkdir /tmp/acc-initrd-root
sudo zstd -d -c /tmp/acc-initrd.zst | sudo cpio -D /tmp/acc-initrd-root -id --no-absolute-filenames
sudo ls /tmp/acc-initrd-root/lib /tmp/acc-initrd-root/usr/lib 2>/dev/null
sudo ls /tmp/acc-initrd-root/lib/modules /tmp/acc-initrd-root/usr/lib/modules 2>/dev/null
```

If `cpio` does not accept `-D`, use:

```sh
sudo zstd -d -c /tmp/acc-initrd.zst | (sudo sh -c 'cd /tmp/acc-initrd-root && cpio -id --no-absolute-filenames')
```

#### 26B.4 Add `/lib/net-lib.sh` inside the unpacked initramfs

```sh
sudo tee /tmp/acc-initrd-root/lib/net-lib.sh >/dev/null <<'EOF'
#!/bin/sh
# Stub: Fedora 43 95iscsi sources this and dies if it is absent.
# handle_firmware (rd.iscsi.firmware) does not need parse_iscsi_root.
parse_iscsi_root() { return 1; }
is_ip() { return 0; }
all_ifaces_setup() { return 0; }
EOF
sudo chmod 755 /tmp/acc-initrd-root/lib/net-lib.sh
ls -l /tmp/acc-initrd-root/lib/net-lib.sh
```

#### 26B.5 Copy `idpf` / `libeth` / `libie` into that same unpacked tree

Session 25 listed modules under `usr/lib/modules/...`, not
`lib/modules`. Use that prefix. The Koji RPM uses `.ko.xz`.

```sh
KVER=7.0.4-100.fc43.aarch64
MODDST=/tmp/acc-initrd-root/usr/lib/modules/$KVER
ls -d "$MODDST" /tmp/acc-initrd-root/lib/modules/$KVER 2>/dev/null
sudo mkdir -p "$MODDST/kernel/drivers/net/ethernet/intel"
sudo cp -a /tmp/fc43-kmod/lib/modules/$KVER/kernel/drivers/net/ethernet/intel/idpf \
           /tmp/fc43-kmod/lib/modules/$KVER/kernel/drivers/net/ethernet/intel/libeth \
           /tmp/fc43-kmod/lib/modules/$KVER/kernel/drivers/net/ethernet/intel/libie \
           "$MODDST/kernel/drivers/net/ethernet/intel/"
ls -l "$MODDST/kernel/drivers/net/ethernet/intel/idpf/idpf.ko.xz"
if [ -f "$MODDST/modules.dep" ] && ! grep -q idpf "$MODDST/modules.dep"; then
  sudo tee -a "$MODDST/modules.dep" >/dev/null <<'EOF'
kernel/drivers/net/ethernet/intel/idpf/idpf.ko.xz: kernel/drivers/net/ethernet/intel/libeth/libeth.ko.xz kernel/drivers/net/ethernet/intel/libie/libie.ko.xz
kernel/drivers/net/ethernet/intel/libeth/libeth.ko.xz:
kernel/drivers/net/ethernet/intel/libie/libie.ko.xz:
EOF
fi
grep idpf "$MODDST/modules.dep" || echo "NOTE: no modules.dep yet; rd.driver.pre=idpf still loads by filename"
```

#### 26B.6 Repack the initramfs back onto the boot image

```sh
INITRD=/mnt/accboot/ostree/default-5447b60d8641064a204e258b935157b340622fe696e96ef1f457e5e017ec7da6/initramfs-7.0.4-100.fc43.aarch64.img
(cd /tmp/acc-initrd-root && sudo find . | sudo cpio -H newc -o --quiet) \
  | zstd -19 -T0 > /tmp/acc-initrd-main.zst
cat /tmp/acc-initrd-early.cpio /tmp/acc-initrd-main.zst | sudo tee "$INITRD" >/dev/null
sudo sync
```

#### 26B.7 Confirm the image, then unmount

```sh
BLS=/mnt/accboot/loader.1/entries/ostree-1.conf
grep rd.driver.pre=idpf "$BLS"
zstd -d -c /tmp/acc-initrd-main.zst | cpio -t 2>/dev/null | grep -E 'net-lib.sh|idpf.ko'
sudo umount /mnt/accboot
```

The `cpio -t` grep must show both `net-lib.sh` and `idpf.ko.xz`. If
either is missing: **stop**. Do not `dd`.

#### 26B.8 Write `/boot` to the LUN (IMC + DH4)

On the IMC:

```sh
losetup -a
LOOP=$(losetup -Pf --show /dev/nvme0n1p4)
echo LOOP=$LOOP
lsblk "$LOOP"
```

From DH4, type the partition `lsblk` printed (example `/dev/loop1p2`).
Do not guess `loop1` vs `loop2`. `$LOOP` is an IMC variable; it is not
set in the DH4 shell unless you copy it:

```sh
ssh root@172.22.4.4 "dd of=/dev/loop1p2 bs=1M conv=fsync" < /tmp/acc-p2-boot.img
```

On the IMC **before reboot**:

```sh
grep -a 'rd.driver.pre=idpf efi=noruntime' /dev/nvme0n1p4 | head -3
```

That must print the BLS `options` line (`root=/dev/mapper/rhel-root`
and `earlycon=`). If it does not: **stop**. Do not reboot.

```sh
reboot
```

#### 26B.9 After SSH is back, read the boot

```sh
tail -c 40000 /work/acc-console.log | cat -v
tgtadm --lld iscsi --op show --mode target | grep -A8 'I_T nexus'
grep nvme0n1p4 /proc/diskstats
ping -c3 192.168.0.2
cli_client -q -c | grep -i 'fn_id: 0x4'
```

**How to read 26B:**

- `idpf 0000:00:01.0` probe, and iSCSI from a **non-UEFI** IQN or
  climbing diskstats → Linux has the LUN. Paste that log.
- `idpf` probe but still `/dev/mapper/rhel-root does not exist` → NIC
  is up, firmware login failed. Paste the new `iscsiadm` / `ibft`
  lines; do not edit `enp0s1f0` yet.
- Still no `idpf` line → the module did not load. Stop.

## Session 26B — **done; still no Linux iSCSI; stop**

The Koji RPM extract, BLS `rd.driver.pre=idpf`, initramfs inject, and
LUN `grep` all landed. After reboot: same dracut emergency shell,
`/dev/mapper/rhel-root does not exist`, UEFI initiator only, ~80 MiB
read, no ping, no `fn_id: 0x4`. The timeout hooks no longer include
`iscsi_started.sh` (session 24 did): the `parse_iscsi_root` stub
returns 1 and skips installing firmware iSCSI. There is no session 27.
Unblocking `fn_id 4` needs Intel's ACC OS image (`acc-partition.img`),
not more Fedora initramfs edits.

## Session 21 leftover (logger) — only if session 22 is silent and cat is dead

```sh
[ -c /dev/ttyFAM0 ] && setsid sh -c '
  exec 3<> /dev/ttyFAM0
  stty -F /dev/ttyFAM0 raw -echo -echoe -echok -echoctl -echoke 115200
  while :; do cat <&3; sleep 0.2; done
' >> /work/acc-console.log 2>&1 &
```

Then reboot. After SSH is back:

```sh
ps -ef | grep ttyFAM0
wc -c /work/acc-console.log
tail -c 16000 /work/acc-console.log | cat -v
```

`ps` must show the `cat` / `setsid` still running **after** GRUB.
If it does, and the log still stops at `Booting Fedora`, Fedora is
not writing to this UART and the next change is a different
`console=` (not another IMC-side theory).

On DH4, `umount /mnt/accboot` if the extra `-rw.img` mount is still
up.

## Held in reserve — decouple the netboot interface from the ACC's `ip=`

§K confirms GRUB **does** pass `ip=...enp0s1f0`. Changing
`acc-config.json` will not affect this boot path. Any interface-name
fix has to be the BLS `options` line, from a **writable** mount of
the boot image on DH4, and only after session 18.

The theory is that `enp0s1f0` in the ACC's `ip=` parameter names an
interface that exists on the IMC and possibly not on the ACC, so the
ACC kernel configures nothing and dracut waits forever for a root
device it can never reach.

Do **not** test this by reverting `/work/cfg/config.json`. §C.4 showed
the shipped default stops the ACC attaching at all, because the same
variable also selects the IMC-side netboot interface. Change only the
ACC's copy:

```sh
cp /mnt/imc/acc_variable/acc-config.json /work/acc-config.json.pre-s11
```

The cheapest variant first — an **empty** device field, which makes the
kernel's ipconfig try every interface it has rather than insisting on
one name:

```sh
sed -i 's|::enp0s1f0:off|:::off|' /mnt/imc/acc_variable/acc-config.json
grep boot_params /mnt/imc/acc_variable/acc-config.json
reboot
```

After the reboot, the §D.1 checks tell you whether it changed anything:

```sh
tgtadm --lld iscsi --op show --mode target | grep -A4 'I_T nexus'
grep nvme0n1p4 /proc/diskstats; sleep 60; grep nvme0n1p4 /proc/diskstats
cli_client -q -c | grep -i 'fn_id: 0x4'
```

**A second burst of LUN reads is the result you want.** It would mean
the ACC kernel got networking up, re-attached the target from Linux
rather than UEFI, and started reading its root filesystem — a stage
past anything seen so far. The sector count climbing again is the
signal, not `fn_id: 0x4`, which comes later.

If the empty device field makes no difference, try `enaintc10c4i0` in
the ACC's copy only, leaving `/work/cfg/config.json` on `enp0s1f0`:

```sh
sed -i 's|:::off|::enaintc10c4i0:off|' /mnt/imc/acc_variable/acc-config.json
```

Restore from `/work/acc-config.json.pre-s11` when done. Note that
§F.2 applies here too: any edit to `acc-config.json` is wiped at the
next boot, so this change belongs in the same
`/work/scripts/pre_init_app.d/99-acc-console.sh` drop-in as a second
`sed` against the generator, not as a hand edit to the JSON.

## Session 17 — Redfish with credentials

§E.5 left this half-open. The service is running and healthy; it just
refuses anonymous reads:

```
{"error":{... "Please create session with valid user name and password." ...}}
```

Ask the lab maintainer for the IMC Redfish account, then:

```sh
curl -sk -u <user>:<pass> https://127.0.0.1:8443/redfish/v1/Systems | python3 -m json.tool
curl -sk -u <user>:<pass> https://127.0.0.1:8443/redfish/v1/Managers | python3 -m json.tool
```

What you are looking for, in rough order of usefulness:

1. `SerialConsole` or `SerialInterfaces` on the Manager — serial over
   LAN to the ACC would make session 10 unnecessary and every session
   after it far easier.
2. `LogServices` — Intel's implementation may already be buffering ACC
   boot output where nobody has looked.
3. A `Systems` entry for the ACC with `Status` and `PowerState`. An
   independent read of whether the ACC is powered and running, rather
   than inferring it from ARP and disk counters.
4. `Actions/ComputerSystem.Reset` on that entry — a supported way to
   restart just the ACC, instead of rebooting the IMC or power-cycling
   the board.

This is also the answer to the §D.4 recovery problem: if Redfish can
reset the ACC, a wedged ACC stops costing a full power cycle.

## Held in reserve — the reset handshake (§B.5, §C.6)

Session 7 cleared this handshake, so it is no longer the blocker. Keep
the commands for the upstream report, or in case sessions 9–10 show the
login was a fluke.

```sh
journalctl -b --no-pager | grep -iE 'lancp|sm_rst|cleanup|mev_imc|vfio|ucma'
dmesg | grep -iE 'mev|acc|reset|lancp'
cat /mnt/imc/acc_variable/acc-uefi-boot-config.json    # did Error populate after a failed boot?
ls -la /mnt/imc/acc_variable/
```

Three things worth pinning down, none of which we can answer from the
IMC-side logs captured so far:

1. **Does `Error` ever populate?** It has stayed `""`/`"None"` through
   every failed boot, which suggests ACC UEFI is not running far
   enough to report, rather than reporting a failure.
2. **What does `LanCP` consider event `type=3, opcode=0x1`?** The
   message says "unsupported", which reads like a state-machine
   mismatch between IMC and ACC firmware versions rather than a
   transient.
3. **Is there more verbose tracing available?** `lttng_setting.sh`
   exists in `imc-scripts` and `run_default_init_app` has an
   `ENABLE_SCRIPT_DEBUG_MODE` flag and LTTng profiling hooks. Read
   both before enabling anything.

Also re-check the `imc_reset` helper — do **not** run it without
understanding it, but read what it does:

```sh
cat /usr/bin/imc-scripts/imc_reset
```

## Held in reserve — fallbacks

Both of these are now lower priority than sessions 8–10: they exist to
answer "is the boot source broken?", and §C.1 suggests it is not.

**RAM/ramdisk boot** isolates whether the fault is the boot *source*
or the bring-up *mechanism*. It is currently **blocked** by §B.3: the
RAM-disk BMD region is 0 bytes since `reserved-memory.json` was
deleted. To use this path you must first restore a corrected file that
allocates `acc_ramdisk` **without** displacing
`ARM_MEMORY_IMAGE_ID_INITRD_IMG` at `0x440c000000`–`0x4414000000`. The
original contents are in the session 2 capture; the `acc_ramdisk` size
needs reducing so both fit. Verify with `bmd_conf -p` after a reboot
that *both* nodes are present before going further:

```sh
# after restoring a corrected /work/cfg/memory/reserved-memory.json and rebooting
/usr/bin/bmd_conf -p | grep -A3 -E 'INITRD_IMG|RAMDISK'
/usr/bin/scripts/set_acc_kernel_cmdline.sh -b ramdisk -a -f
/usr/bin/imc-scripts/set_acc_uefi_boot_config --reset --BootType "DramBoot"
/usr/bin/imc-scripts/enable_acc_boot
reboot
```

If the ACC boots from RAM but not iSCSI, the fault is the target or
the partition image. Set it back to `iscsi`/`LocalIscsiBoot`
afterwards.

**Re-provision the ACC OS partition.** Last resort: destructive,
repartitions `/dev/nvme0n1p4`, and needs `acc-partition.img` fetched
over SSH from a machine you nominate — which may be as hard to obtain
as the firmware:

```sh
/usr/bin/imc-scripts/acc_os_partition_provision.sh
```

Read the script first (it is in the session 1 capture). It prompts for
an IP, username and remote path, shuts the ACC down, kills `tgtd`,
unmounts `/mnt/acc`, may repartition and reboot mid-way, and disables
ACC secure boot at the end. Do not start it without the image in hand
and the lab maintainer's agreement.

## Held in reserve — remaining read-only checks

```sh
/usr/bin/imc-scripts/cfg_acc_secure_boot          # read it first; --create writes a request
ls -la /mnt/imc/acc_variable/push/ 2>/dev/null
sed -n '60,95p' /usr/bin/scripts/start_imc_idpf.sh   # the 'line 77' integer-compare bug
```

`cfg_acc_secure_boot` without arguments writes request and response
JSON files and then calls `clear_acc_boot_errors`, so it is not purely
inspective. Read it before invoking.

## In parallel — the questions that cost nothing

For the lab maintainer, in priority order:

> 1. **Does anything else on the box read `/dev/ttyFAM0`?** It is the
>    ACC UART at `0x2058400000` (§F.1) and we intend to hold it open
>    across boots. Worth knowing before we contend with a vendor
>    consumer nobody has noticed.
> 2. **What are the IMC Redfish credentials?** The service is up on
>    8443 and rejects anonymous reads (§E.5). It may already expose ACC
>    serial-over-LAN, log services, and a targeted ACC reset.
> 3. **Why does DH4 override `init_app_acc_nboot_net_name` to
>    `enp0s1f0`?** Whoever set it knows whether `icc_net` is broken on
>    this unit, which decides whether §E.4 is a real lead.
> 4. Has DH4's ACC ever come up? Do the other E2100 units in the lab
>    boot their ACCs on the same IMC image?
> 5. Do you have `acc-partition.img` for this release?

A working sibling on identical firmware exonerates firmware and makes
this unit-specific. Question 1 is worth more than the rest combined.

## What to record

Fold outcomes back into
[`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) §6.1 as they
land — especially session 9's result, which is what turns the clock
story from a correlation into a cause.

## Escalating

The BMD finding in §B.1 is worth reporting upstream **regardless of
how the rest turns out**. It is a concrete, root-caused defect: a
supported configuration file (`reserved-memory.json`, tuning RAM-disk
size for large ISO boot) silently invalidates the
`ARM_MEMORY_IMAGE_ID_INITRD_IMG` BMD node, which makes
`set_acc_kernel_cmdline.sh` emit `initrdmem=,` and breaks iSCSI boot.
It reproduces across captures a week apart, has a clear before/after,
and needs no theory about clocks. Intel's own provisioning script
already carries a "BMD issue workaround" for adjacent symptoms.

Be clear in the report that **fixing it did not bring the ACC up** —
the failure persists at the reset handshake (§B.5) — so the two are
separate issues.

Attach:

- before/after `bmd_conf -p`, showing node 18 as
  `ARM_MEMORY_IMAGE_ID_INVALID` at `0x440c000000` and then as
  `ARM_MEMORY_IMAGE_ID_INITRD_IMG` at the same address
- the `reserved-memory.json` that caused it
- before/after `acc-config.json` (`initrdmem=,` →
  `initrdmem=0x440c000000,0x8000000`)
- `acc-uefi-boot-config.json` before and after, including the
  out-of-range `BootType: "Other"`
- the session 4 boot journal, the first with both fixes in place
- the `LanCP ... reset state machine returned unsupported event:
  type=3, opcode=0x1` warning together with the
  `cp_pre_hw_drain_cleanup ... pf=4` errors behind it (§C.6)
- the `tgtadm` before/after: an empty `I_T nexus` on every cold boot,
  and a login from `iqn.1986-03.com.intel:e2000.acc-client...` at
  `192.168.0.2` after the session 7 force-start (§C.1)

If session 9 confirms the clock, that is a second reportable defect and
arguably the more serious one: on an RTC-less IMC the ACC netboot is
triggered before NTP converges, and the resulting epoch clock stops the
ACC from booting at all.
