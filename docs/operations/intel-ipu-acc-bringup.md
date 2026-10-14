# Intel IPU ACC bring-up: preparing and validating the P4/OVS control plane

This is the missing piece between "I can SSH to the IPU" and "I know
what to type into `p4.forwarding_entries` / `forwarding.endpoints[].representor`
in `config/lab-intel-vm2vm.yaml`." It is a **software/config bring-up
runbook for the IPU's Arm Compute Complex (ACC)** — separate from and
prior to [`first-run-intel-vm2vm.md`](first-run-intel-vm2vm.md), which
assumes this is already done.

Everything below is summarized from IPDK's own public documentation
(cited inline) for the P4 Control Plane (`infrap4d`) stack on Intel IPU
E2100. **This project has not run these exact steps against real
hardware** — treat every command as "confirm against your release's
docs," not gospel. If your IPU ships a different bring-up flow (OEM
image, different IPDK version), this doc's mapping of *artifact →
config field* still applies; only the exact commands to produce the
artifact may differ.

**`ipdk.io` is dead — use these links instead.** As of this writing
`ipdk.io` returns HTTP 500 for every page. `ipdk-io/networking-recipe`
(the source code repo) was **archived by Intel on 2026-05-11** with a
top-of-README notice: *"Intel will not provide or guarantee development
of or support for this project... Patches to this project are no
longer accepted by Intel."* The documentation content itself did **not**
disappear — its README says it "moved from the `docs` directory to the
[`ipdk-io/p4cp-userguide`](https://github.com/ipdk-io/p4cp-userguide)
repository," and that repo's raw Markdown source is still live and
readable directly on GitHub even though the rendered `ipdk.io` website
that used to be built from it is not. Every link in this document has
been updated to point at that repo's raw source instead of `ipdk.io`.
Because the upstream project is now unmaintained, treat "confirm
against your release's docs" above even more literally than usual —
there will be no further updates from Intel if something has drifted.

## 0. Topology and what "the IPU" actually means here

Per [`intel-ipu-e2100.md`](../vendor-readiness/intel-ipu-e2100.md), an
IPU has (at least) three distinct planes — don't treat "I can log into
the IPU" as one thing:

| Plane | What it is | What this doc's config fields call it |
|---|---|---|
| Host x86 | The server whose lspci/VFs you saw in `first-run-intel-vm2vm.md` §1–2 | `host:` |
| IMC (management complex) | Firmware, Redfish, secure boot, P4 package flashing | `ipu.management_ip` / `ipu.redfish_endpoint` |
| ACC (Arm Compute Complex) | Runs `infrap4d`, optionally `ovs-vswitchd` — the thing you actually SSH into for everything below | `dpu.management_ip` / `dpu.ssh_user` / `dpu.ssh_key_path` (Intel's ACC reuses the same field shape as NVIDIA's BlueField Arm side) |

"I know the IP of the IPU, I can login to the IPU" — confirm *which*
plane that IP/login actually reaches before going further. Everything
in this doc runs **on the ACC**, over the SSH target you'll put in
`dpu:`. If your login only reaches the IMC, you need a separate hop to
the ACC first (IMC → ACC SSH, per IPDK's docs) before any of this
applies.

**A word on shared-lab inventory docs** (e.g.
[opiproject/lab's `ips.md`](https://github.com/opiproject/lab/blob/main/Documentation/ips.md),
which lists Intel IPU rows like `Intel <SKU> imc` on the same
`172.22.4.0/24` block it uses for every other vendor's **BMC**): the
label in a shared-lab spreadsheet is a *starting point*, not proof.
That subnet's own legend calls it "All BMC of the DPU" — for NVIDIA/
Marvell rows in the same document that address really is a BMC with a
Redfish web UI (hence the `https://<ip>/redfish/v1/` column those rows
have). The Intel row in that same table has **no such URL filled in**.
If you were given an IP labeled "imc" that you can SSH into but never
get a working browser/Redfish response from, don't assume the label is
simply wrong — an Intel E2100's real IMC often has no exposed Redfish
UI on the lab network at all, and separately, some lab wiring genuinely
does put the ACC's address in the slot a spreadsheet template calls
"imc" for every other vendor. Use the checks below instead of trusting
either the label or the absence of a browser response.

### 0.1 How do I know if I'm on the IMC or the ACC?

**Correction from an earlier version of this doc, confirmed against
real E2100 hardware:** "Rocky Linux" and "lots of interfaces" are
**not** reliable ACC signals — a real IMC on this hardware also runs
Rocky Linux 9.4 and also shows plenty of interfaces, because the IMC
is the complex that locally terminates *both* comm channels (to the
ACC and to the Host). Don't use OS distro or interface count to
decide. Use these instead, in order of reliability:

| Check | ACC (what you want for everything below) | IMC |
|---|---|---|
| `hostname` | Contains `acc` (e.g. `mev-acc-rl` in Intel's own doc examples) | Contains `imc` (e.g. `ipu-imc`, `mev-imc`) |
| `which cli_client` (or `ls /usr/bin/cli_client`) | Not present | **Present** — `cli_client` is the IMC-only node-policy/port tool ([`enabling-comm-channel.md`](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/guides/es2k/enabling-comm-channel.md)) |
| `ls /opt/p4/` | Usually exists — `p4sde/` and `p4-cp-nws/` (the pre-staged `infrap4d`/`p4rt-ctl` install, see §1) | Does not exist / `/opt` is empty |
| `ip addr show` — look for the comm-channel interface's **altname and address** | Altname **`commacc`** (or similar) at address **`192.168.0.2`** — this is the ACC's static side of the IMC↔ACC comm channel | Altname **`commacc`** at address **`192.168.0.1`** — the IMC's own side of that same link (and you may also see a second altname `commhost0`/similar for the separate IMC↔Host channel) |
| `cat /etc/os-release` | Not diagnostic on its own — both complexes can be Rocky Linux | Not diagnostic on its own |

The `commacc` altname + `.1` vs `.2` address is the single most
reliable signal once the comm channel is already configured (which it
usually is on a pre-provisioned/shared-lab box) — it comes directly
from Intel's own doc statement that "ACC comes up with a statically
assigned IP address `192.168.0.2`... You can access ACC from IMC over
an SSH session using this IP address," so `192.168.0.1` on that same
named link is definitionally the IMC's end.

**If you're on the IMC and need the ACC:** the comm channel is your
path over, no reboot/node-policy step needed if `commacc` is already
UP with an address (it usually is):

```bash
# from the IMC
ping -c2 192.168.0.2
ssh 192.168.0.2
```

If SSH prompts for credentials you don't have, try the same
credentials that got you into the IMC first; that's a separate
credentials question from the networking, which the successful `ping`
already confirms is fine.

**If `ping`/`arping` to `192.168.0.2` get zero response even though
`commacc` shows real L1 link** (`ethtool` `Link detected: yes`,
`dmesg` shows the `idpf` driver enumerating the interface cleanly, no
firewall in the way) — check whether the ACC's function is even
**instantiated** in the fabric, not just unreachable:

```bash
cli_client -q -c
```

Compare the `fn_id` groups against your own interfaces' MAC addresses
from `ip addr show` first — one `fn_id` group will be an exact MAC
match for your own local vports (that one is "this machine," not a
peer). In Intel's own doc examples the **Host** is `fn_id 0`/`host_id
0` and the **ACC** is `fn_id 4`/`host_id 4` — but confirm this by MAC
match on your unit rather than assuming the numbers, since the fabric
also assigns the IMC itself a function id (`fn_id 5` in one real
DH4-class unit observed) that isn't in Intel's doc example at all.

If the row for the ACC's function is **entirely absent** from the
table (not merely `is_enabled: no`), that's a materially different,
worse finding than a network-reachability problem: the ACC's function
isn't currently created by this node policy at all, so nothing will
ever answer on `192.168.0.2` no matter how long you wait or how many
addresses you try. Confirm with:

```bash
cli_client --query --config --pkg_action get_info
cat /etc/dpcp/cfg/cp_init.cfg | grep -i cpf_host
```

Per Intel's own docs, `cpf_host` in that config is what enables the
ACC function (documented default: `4`, meaning ACC). There are two
distinct cases depending on what you find:

- **`cpf_host` is *not* `4`** (e.g. it's `0`–`3`, meaning a Xeon/Host
  partition is the active CPF instead): this is a real config change,
  not just a re-init. On a shared lab host with no formal admin gate,
  don't edit this file and reboot unilaterally — confirm with whoever
  else might be using the box (mailing list / chat for that lab) that
  switching the active CPF to ACC won't break someone else's in-flight
  work, since only one CPF (Host or ACC) can be active/reserved at a
  time.

- **`cpf_host` is already `4`** but `cli_client -q -c` still shows the
  vport/VSI reservations sitting with `fn_id 0`/`host_id 0` (Host) and
  no `fn_id 4` (ACC) row at all: the config already asks for what you
  want, but the *running* fabric state doesn't reflect it yet. Per
  `cli_client --help`'s own language ("used to reset ... after IMC CP
  is initialized"), this reservation is assigned at CP-init
  (boot) time, not hot-reloaded — so this looks like a config that was
  changed after the control plane last initialized, with no reboot
  since. Rebooting the IMC (`reboot`) is the natural next step here,
  since it's not actually changing policy, just applying config that's
  already committed. On shared, admin-less community lab hardware:
  check `who`/`w`/`last -10` first for other active sessions, and
  confirm you have an out-of-band way back in (serial/KVM console,
  not just SSH) in case the reboot doesn't come back cleanly, before
  proceeding. After reboot, re-run `cli_client -q -c` and expect an
  `fn_id 4` group with `is_enabled: yes` if this was the fix.

## 1. Get the P4 artifacts onto the ACC

Per [Running Infrap4d on Intel IPU E2100](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/guides/es2k/running-infrap4d.md),
this assumes the P4 package is **already flashed onto the IMC** (a
separate, out-of-scope firmware-lifecycle step — see
[`intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md)'s
"What stays out of scope"). What you do on the ACC:

### 1.0 Check for a pre-staged tarball first — you may not need to build anything

Intel's own walkthrough for this exact step starts from a prompt like
`[root@mev-acc-rl opt]# cd /opt` and untars a **`p4.tar.gz` that is
already sitting there**:

```bash
cd /opt
ls -la                 # look for p4.tar.gz, or an already-extracted p4/ directory
tar -xf p4.tar.gz       # only if not already extracted
ls -lR /opt/p4/         # expect p4sde/ and p4-cp-nws/
ls /opt/p4/p4-cp-nws/bin/   # look for infrap4d, p4rt-ctl, tdi_pipeline_builder
ps -ef | grep infrap4d      # it may already be running as a boot service
```

If `infrap4d`/`p4rt-ctl`/`tdi_pipeline_builder` are already there under
`/opt/p4/p4-cp-nws/bin/`, **you do not need to build P4 Control Plane
from source at all** — that is the whole point of this pre-staged
tarball, and it's what most real E2100 ACC images (including
community/shared-lab ones) ship with. Only fall back to building it
yourself (a much heavier lift: cross-compiling `networking-recipe` for
aarch64 against an ACC SDK toolchain — see
[`building-acc-p4cp.md`](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/guides/es2k/building-acc-p4cp.md)/
[`installing-acc-sdk.md`](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/guides/es2k/installing-acc-sdk.md)/
[`defining-acc-environment.md`](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/guides/es2k/defining-acc-environment.md))
if `/opt/p4.tar.gz` genuinely isn't there — and given the upstream
project is now archived/unmaintained (see the callout above), treat
that as a last resort, and consider asking whoever provisioned the
hardware whether a pre-built image is available before going down that
path solo.

### 1.1 Get the P4 program artifacts

If §1.0 already found a working `infrap4d`/`p4rt-ctl` install, you
still need P4 program artifacts to actually load a pipeline (§2):

1. Get the artifacts for your P4 program. Two ways:
   - **Pre-built**: download the `hw-p4-programs` release TAR for your
     IPDK version and extract it. IPDK's own example uses
     `fxp-net_linux-networking` (the "Linux Networking" recipe — see
     §3 below, this is almost certainly what you want for plain
     VM-to-VM L2 bridging) or `l3-fwd_sem` as reference programs.
   - **Compiled yourself**: run the `p4c-pna-xxp` compiler against your
     `.p4` source (see
     [Compiling P4 programs](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/guides/es2k/compiling-p4-programs.md)).
2. Either way you end up with `tdi.json`, `context.json`, and a
   `p4info.txt` (sometimes `p4Info.txt` — case varies by release). Copy
   all three to the ACC.
3. On the ACC, run `tdi_pipeline_builder` (under
   `/opt/p4/p4-cp-nws/bin` in IPDK's default layout) against those
   three files to produce the compiled pipeline binary (`.pb.bin`).

**Map straight into `config/lab-intel-vm2vm.yaml`'s `p4.artifacts`
block** — this is exactly what those fields are for:

```yaml
p4:
  artifacts:
    p4info_path: /path/on/acc/p4info.txt
    pipeline_binary_path: /path/on/acc/fxp-net_linux-networking.pb.bin
    tdi_path: /path/on/acc/tdi.json
    context_path: /path/on/acc/context.json
```

Do not commit any of these files or their paths to git — they are
per-lab, per-SKU, and this repo's `.gitignore` already excludes your
real `lab-intel-vm2vm.yaml`.

## 2. Start `infrap4d` and load the pipeline

```bash
# on the ACC
infrap4d                    # Linux Networking (OVS-integrated) mode
# or: infrap4d -disable_krnlmon   # if you are NOT using the OVS control plane
```

Then load the pipeline you built in §1 — this is the one manual step
this project's tooling does **not** do for you yet
(`P4RuntimeClient`'s `set-pipe` subcommand is listed as "not wired yet"
in `plugins/intel_ipu/p4runtime.py`'s docstring):

```bash
p4rt-ctl set-pipe br0 fxp-net_linux-networking.pb.bin p4info.txt
```

`br0` here is `p4.device_id` in the config (defaults to `br0` if left
`null`).

**Validate this step worked** before going further:

```bash
p4rt-ctl get-pipe br0
```

Non-empty output = a pipeline is loaded. This is the *exact* command
`plugins/intel_ipu/p4runtime.py`'s `_cli_get_pipe()` runs for you once
`p4.mode: cli` — so once you set that in your real config, `labctl`'s
own capability check re-runs this same probe (see §5).

## 3. Finding the real table/action names for `p4.forwarding_entries`

This is the concrete answer to "how do I know all those": **read your
`p4info.txt`** — it is a plain protobuf-text file listing every table
and action in your loaded pipeline by name (`Table { preamble { name:
"..." } }`, `Action { preamble { name: "..." } }`). `grep` or `less`
it directly on the ACC; there is no other source of truth for a
pipeline-specific FLOW string, and this project deliberately never
guesses one (see `intel-ipu-p4-ipdk.md`).

**If you're using the default `fxp-net_linux-networking` recipe** (the
common case — you almost certainly are, unless you wrote a custom P4
program), the real table names are already publicly documented and
mostly **not** something you write by hand at all. Per
[Linux Networking for ES2K](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/apps/lnw/es2k/es2k-linux-networking.md):

> When the first packet (either Tx or Rx) reaches the IPU, since no
> rules are programmed, this packet will be sent to the respective
> port representors. From the representors OVS learns the MAC address
> and the control plane **dynamically programs**: `l2_fwd_smac_table`,
> `l2_fwd_tx_table`, `l2_fwd_rx_table`, `ipv4_table`/`ipv6_table`,
> `nexthop_table`, `neighbor_mod_table`, ...

That "dynamically programs" line is exactly why this project's `ovs`
control plane (`intel.control_plane: ovs`, see
[`intel-ipu-ovs-p4.md`](../vendor-readiness/intel-ipu-ovs-p4.md))
exists: for plain bidirectional VM-to-VM L2 bridging, **you don't need
`p4.forwarding_entries` at all** — attach each VF's ACC port
representor to an OVS bridge (§4 below) and let `ovs-vswitchd`'s own
MAC learning write these table entries for you via P4Runtime. Reach
for `p4.forwarding_entries` (raw `p4rt-ctl add-entry`, exact FLOW
strings from your `p4info.txt`) only if you need something OVS's
dynamic learning won't give you — a static/non-learned rule, a custom
P4 program, or you specifically want to bypass OVS.

## 4. (`ovs` control plane only) Start the OVS side and find representor names

Per [Linux Networking with Overlay VMs](https://raw.githubusercontent.com/ipdk-io/p4cp-userguide/main/docs/apps/lnw/es2k/es2k-lnw-overlay-vms.md),
the IPDK-patched, P4Runtime-integrated `ovs-vswitchd` build is a
**separate process** from `infrap4d` (started against its own
`ovsdb-server`, with `OVS_P4_OFFLOAD=false` set before the first
start so dynamic learning is allowed to program rules). Confirm it's
running:

```bash
ovs-vsctl show
```

If this fails or the binary isn't found, you have upstream/kernel OVS
without the P4Runtime integration — that will not program anything on
the IPU pipeline, and `intel.control_plane: ovs` should not be used
until the correct build is running.

Once it's up, list the ACC's port representor netdevs:

```bash
ip link show
# or, once a bridge exists:
ovs-vsctl show
```

Representor names on real E2100 hardware look like ordinary
PCI-function-derived netdev names (IPDK's own examples use e.g.
`enp0s1f0d1`, `enp0s1f0d9`) — not `pf0vf0`-style names like NVIDIA
BlueField. Match each one to a VF using the index-paired convention
from `ovs_offload_lnw.py`'s `config.yaml` (already in this repo's
config schema as `p4.vf_interfaces[i]` ↔ `p4.acc_pr_interfaces[i]`),
then put the matching representor into
**`forwarding.endpoints[].representor`** for that VM/VF — this is the
one thing `_submit_ovs_forwarding()` refuses to guess.

## 5. Readiness checklist (map every check to a config field/labctl stage)

Run these **before** trusting `labctl vm2vm configure-forwarding
--apply` to do anything real. Each row is a manual command you can run
right now over SSH, plus the equivalent thing this project checks for
you automatically once the config field is filled in.

| # | Check (run on ACC unless noted) | Confirms | Feeds config field | Automatic equivalent once configured |
|---|---|---|---|---|
| 1 | `ssh <dpu.ssh_user>@<dpu.management_ip> echo ok` (run from the host) | ACC is reachable at all | `dpu.management_ip`, `dpu.ssh_user`, `dpu.ssh_key_path` | Every `plugins.base.remote.ssh_params()`-based call below refuses honestly (`executed: false`) instead of hanging if this isn't reachable |
| 2 | `pgrep -a infrap4d` | `infrap4d` process is up | (none directly — prerequisite for #3) | — |
| 3 | `p4rt-ctl get-pipe br0` (or your `p4.device_id`) | A pipeline is loaded (§2) | `p4.device_id` | `check_vm_to_vm_forwarding_capability()`'s `_maybe_probe_p4_live()`, when `p4.mode: cli` **and** `remote_execution.dry_run_default: false` |
| 4 | `grep -c '^Table {' p4info.txt` (or just read it, §3) | Real table/action names exist for your pipeline | `p4.artifacts.p4info_path`, `p4.forwarding_entries[].table`/`.flow` | Nothing automatic — this project never invents a FLOW string |
| 5 | `ovs-vsctl show` (ovs control plane only) | Patched `ovs-vswitchd`+`ovsdb-server` are up and P4Runtime-integrated | `intel.control_plane: ovs` | `check_vm_to_vm_forwarding_capability()`'s `_maybe_probe_ovs_live()`, when `intel.control_plane: ovs` **and** `remote_execution.dry_run_default: false` |
| 6 | `ip link show` (ovs control plane only) | Representor netdev names for each VF | `forwarding.endpoints[].representor`, `p4.acc_pr_interfaces` | `discover-representors` stage (currently reports empty for Intel — see [`first-run-intel-vm2vm.md`](first-run-intel-vm2vm.md) §8, stage 6 — so today you fill `representor` by hand from this check) |

Once rows 1–3 (for `p4`) or 1, 2, 5–6 (for `ovs`) pass, set
`remote_execution.dry_run_default: false` in your real config and
re-run `labctl vm2vm configure-forwarding --dry-run --json` — you
should see `capability_proven: true` and `control_plane` set to the
one you configured, still without submitting anything (dry-run). Only
then move to `--apply`.

## 6. If a check fails

| Symptom | Likely cause |
|---|---|
| SSH to ACC hangs or refuses | Wrong IP/user for this plane (see §0 — you may have the IMC's address instead), or IMC→ACC hop not established |
| Logged in fine, but unsure if this is the IMC or the ACC | See §0.1's table — check `hostname`, `/etc/os-release`, `/opt/p4/`, `cli_client`, and `ip addr show` |
| On the IMC, `commacc` interface has real L1 link but `ping`/`arping` to the ACC's address get zero response, across the whole subnet | Don't assume "wrong address" — check whether the ACC's function even exists in `cli_client -q -c`'s output at all (not just `is_enabled: no`). See §0.1's last block |
| `cli_client -q -c` shows no ACC `fn_id` row, but `/etc/dpcp/cfg/cp_init.cfg`'s `cpf_host` is already `4` (ACC) | Config is already correct but hasn't been applied — this is very likely just a pending IMC reboot (CPF reservation is assigned at CP-init/boot time). Do the shared-host due-diligence checks in §0.1, then `reboot` the IMC and re-check |
| `cli_client -q -c` shows no ACC `fn_id` row, and `cpf_host` is *not* `4` | This is a real policy change, not just a stale re-init — coordinate with other users of the shared box before flipping the active CPF, since only one CPF can be reserved at a time |
| `infrap4d`/`p4rt-ctl` not found | Check `/opt/p4.tar.gz`/`/opt/p4/` first (§1.0) — it may just need extracting; only actually missing if that's not there either, which on the now-archived upstream project (see the callout near the top) means asking whoever provisioned the hardware for a pre-built image rather than building from source solo |
| `p4rt-ctl get-pipe` returns empty | Pipeline never loaded — repeat §2's `set-pipe` step |
| `ovs-vsctl show` fails or `ovs-vsctl: command not found` | Either the patched `ovs-vswitchd` isn't running, or this ACC only has plain upstream OVS (no P4Runtime integration) — do not use `intel.control_plane: ovs` until confirmed otherwise |
| Representor names don't look like any VF you created | `dpu.pf_name`/VF creation from `prepare-vfs` (host side) hasn't run yet, or representors only appear after `infrap4d`/OVS actually attach that VF — re-run this check after stage 4 (`prepare-vfs --apply`) |

## 6.1 Open issue: ACC never appears (`fn_id 4` never created)

Observed on a real E2100/MEV-TS community-lab unit (`opiproject/lab`
DH4). If you've confirmed `cpf_host` is `4` (ACC) in
`/etc/dpcp/cfg/cp_init.cfg` (§6's table above), rebooted, and `fn_id 4`
*still* never shows up in `cli_client -q -c` no matter how many times
you reboot, you are in this case. A plain reboot loop will not fix it.

This section records what we observed, what we think is happening, and
— importantly — what has *not* been tested yet. The cause is still
open. Read §6.1.2 before repeating any of §6.1.1.

**Two defects found and fixed on 16 Sept — neither brought the ACC up.**

1. The ACC's generated kernel command line contained `initrdmem=,`,
   because the box's custom `/work/cfg/memory/reserved-memory.json`
   was invalidating the `ARM_MEMORY_IMAGE_ID_INITRD_IMG` BMD node.
   `clear_bmd_config` plus removing that file restored the node, and
   the command line now reads
   `initrdmem=0x440c000000,0x8000000`. Worth reporting upstream on its
   own merits.
2. `acc-uefi-boot-config.json` held `"BootType": "Other"`, which is not
   among the values `set_acc_uefi_boot_config` accepts. Reset to
   `LocalIscsiBoot` / `ContinuousOverride`.

**As of 17 Sept the ACC Linux kernel is running.** UEFI still iSCSI
netboots Fedora 43 ostree (`7.0.4-100.fc43.aarch64`). With
`console=` / `earlycon=` on the GRUB BLS line the kernel is visible
on `ttyFAM0`. It first panicked in `efi_create_mapping` (broken EFI
runtime map at `0x4400700000`); `efi=noruntime` skips that. Dracut
then starts and never mounts `/dev/mapper/rhel-root`. Session 26B
put `idpf` and a stub `/lib/net-lib.sh` into the LUN initramfs and
`rd.driver.pre=idpf` on the BLS line; after reboot the failure is
unchanged (UEFI initiator only, no ping, no `fn_id 4`). The stub
also dropped the `iscsi_started.sh` timeout hook. Stop: this ostree
is not Intel's ACC OS. Live log:
[`intel-acc-dh4-triage-session.md`](intel-acc-dh4-triage-session.md)
sessions 0–26B.

The `LanCP ... reset state machine returned unsupported event:
type=3, opcode=0x1` warning is still logged, but it is no longer
blocking — the ACC clears that handshake and proceeds.

**The failure is now inside the ACC's own Linux boot,** after UEFI
hands off and before the ACC-side IDPF driver registers. That the ACC
does not reset despite `acc_watchdog_timer: 60` suggests a kernel that
is running and waiting rather than one that has died.

**The clock hypothesis in §6.1.1 is falsified.** A boot whose netboot
window ran at raw epoch — all four `tar: ... in the future` warnings
present — produced an iSCSI login anyway. The RTC-less clock does not
prevent ACC boot, and obtaining newer firmware is not a prerequisite
for progress.

The most promising open lead is that
`set_acc_kernel_cmdline.sh` gives the ACC a console on its ramdisk
boot path (`console=ttyS0,115200
earlycon=uart8250,mmio32,0x2058400000`) and no console at all on the
iSCSI path that DH4 uses, which is why a kernel hanging in early
userspace does so invisibly.

Current work, findings and next steps live in
[`intel-acc-dh4-triage-session.md`](intel-acc-dh4-triage-session.md) —
see §E for the current state and sessions 10-12 for what to run next.

### 6.1.1 What was observed — clock hypothesis, FALSIFIED 16 Sept

> **This theory is dead.** A boot that extracted the ACC's ATF images
> at raw epoch, with all four `tar: ... in the future` warnings in the
> journal, produced a successful ACC iSCSI login. The section is kept
> because it shaped weeks of investigation and because the boot-sequence
> description in it is still accurate.

**Leading hypothesis, not yet confirmed:** this class of IMC has no
battery-backed RTC (`timedatectl status` shows `RTC time: n/a`) and
boots with the system
clock at raw Unix epoch every time. `ipu_daemon` (spawned outside
systemd — `systemctl cat ipu_daemon.service` returns nothing) runs the
ACC netboot sequence roughly 60–80 seconds into boot: it iSCSI-boots
ACC's OS image from the IMC itself (`iqn.e2000:acc`) and `tar`-extracts
`acc-os-bl31.bin`/`bl32.bin`/`bl33.bin` (ARM Trusted Firmware-A boot
stages). **This extraction happens while the clock is still at
epoch** — `journalctl -b -o short-iso` will show `tar: ...: time stamp
... is ... s in the future` for all three files, timestamped
`1970-01-01`, every single boot. Immediately after, `LanCP` logs three
`reset state machine returned unsupported event` warnings and ACC
never appears again for the rest of that boot. NTP (`chronyd`) is
**not broken** — `chronyc sources -v`/`chronyc tracking` show it fully
synced with sub-millisecond accuracy once it converges — it just
converges several minutes *after* the ACC-netboot window, and nothing
in the boot sequence gates ACC netboot on time-sync completing first.

**What now supports it.** The 16 Sept force-start, with the clock
already synced, is the only netboot attempt that ever produced an
iSCSI login from the ACC. The same board with the same config file had
produced nothing on a cold boot hours earlier. That is a real signal,
not log adjacency — but it is still one run, and the control
(identical sequence with the clock forced back to epoch) has not been
done.

**Why it is still a hypothesis and not a root cause.** The original
clock story was built on log adjacency — the `tar` warnings and the
`LanCP` warnings land one second apart — and three things argue
against the mechanism as originally stated:

- Those `tar` lines are *warnings*. GNU tar prints them and extracts
  the file anyway. No extraction error, signature rejection or boot
  abort was ever captured.
- Standard ARM TF-A trusted board boot deliberately skips X.509
  validity-date checking, precisely because there is no trusted time
  source that early in boot. Intel's auth path could be custom, but
  the mechanism we assumed is the one TF-A normally tolerates.
- Every IMC of this class is RTC-less and boots at epoch. If that
  alone broke ACC netboot, no E2100 on this image would ever bring up
  an ACC.

No vendor release note, erratum or support ticket has been found that
ties this symptom to a firmware defect. Treat "a newer firmware will
fix it" as untested, and do not block on obtaining one.

**Competing explanation worth ruling out first:** on DH4,
`/mnt/imc/acc_variable/acc-uefi-boot-config.json` sits at `"State":
"OneTimeOverrideUpdated"` with `"BootType": "Other"` and `"VPortId":
0`, while `ipu_daemon` polls for `fn_id: 0x4 ... vport_id 0x1` and
logs `Primary not found. ACC may have gone down, retry.` A stale
one-time UEFI override or a boot type that isn't iSCSI would each
produce exactly the observed silence.

(The `VPortId` difference is *not* a candidate: Intel's reference
procedure uses `set_acc_uefi_boot_config --reset`, which leaves
`VPortId` at its default of `0`, so the two numbers are separate
namespaces. Do not change it speculatively.)

**What does *not* fix it** (confirmed on real hardware, so don't
re-spend time on these):
- Rebooting alone — applies pending `cpf_host` changes but leaves the
  outcome unchanged.
- `/usr/bin/imc-scripts/enable_acc_boot` and `clear_acc_boot_errors` —
  run fine, no effect; `acc-uefi-boot-config.json`'s `Error` field
  stays `"None"` even immediately after a confirmed failure, so ACC's
  UEFI isn't surfacing a failure reason through that mechanism here.
- `touch /usr/lib/clock-epoch` (systemd ≥247's documented RTC-less
  clock-floor mechanism — this IMC runs systemd 252, which supports
  it) — does not persist. The whole rootfs is a RAM disk
  (`root=/dev/ram0` in the kernel command line), rebuilt from a fixed
  baked image every boot; nothing written to `/usr/lib` survives a
  reboot.

**Also confirmed not to help:** editing `/etc/chrony.conf` (e.g. to
point at a faster, locally-reachable NTP source instead of an internet
pool). Tested by appending a marker line and rebooting — it did not
persist. `/etc` is on the same ephemeral RAM disk as `/usr/lib`, not a
persistent partition.

**Correction — `/work` *is* persistent.** An earlier version of this
section generalised the ephemeral-rootfs finding into "no config
surface survives a reboot." That is wrong, and the evidence disproves
it: `enable_acc_boot` writes `/work/cfg/config.json`, and on a later
boot `ipu_daemon` reads those overrides back —

```
WARNING: ACC_NETBOOT_STAGE=0 due to 'init_app_acc_nboot_stage' override in /work/cfg/config.json
WARNING: ACC_NETBOOT_INTERFACE=enp0s1f0 due to 'init_app_acc_nboot_net_name' override in /work/cfg/config.json
```

`/work` also holds `sme/versions/1.5.0/`. Persistent state that *is*
reachable from a running shell includes `/work/cfg/config.json`,
`/mnt/imc/acc_variable/*.json`, the `acc-rl` NVMe partition, the
`acc_config` labelled volume, `/mnt/imc/acc-os.iso` and
`/mnt/imc/acc/ramdisk/acc-os-image.bin`. The ephemeral finding holds
for `/etc` and `/usr/lib` only.

**Additional observation:** network/DNS reachability right after boot
may itself be inconsistent — `2.rocky.pool.ntp.org` (ping) and
`clock.corp.redhat.com` (DNS resolution) both failed during the
investigation, then later succeeded from the same shell with no
config change in between. This suggests part of the delay before
`chronyd` converges may be DNS/network-path flakiness while the
interface/DHCP lease is still settling immediately after boot — worth
mentioning as a secondary factor if you do escalate.

### 6.1.2 Not yet tried — do these before escalating

Ordered by what each one tells you per unit of risk.

This list predates the 16 Sept iSCSI login. Items 1, 3 and 5 are done.
Fedora BLS/initramfs work through session 26B is also done and did not
produce `fn_id 4`. Do not add more LUN surgery; remaining items below
are leftover isolation ideas, not a live plan.

1. **Re-trigger ACC netboot with the clock already correct.** *Done, 16
   Sept.* Requires `systemctl stop ipu.service` first, otherwise the
   running `ipumgmtd` holds the device and the force-start exits 255.
   With the clock synced, the ACC logged in to the iSCSI target for the
   first time — but never registered `fn_id 4`. The isolating control
   (same sequence, clock forced to epoch) is still outstanding.
2. **Clear the latched UEFI state.** Only `clear_acc_boot_errors` has
   been run, and it blanks the `Error` field only. These exist and
   have never been run: `clear_acc_uefi_boot_config`,
   `clear_acc_uefi_nv_vars`, `clear_acc_boot_options`. Aimed straight
   at the `OneTimeOverrideUpdated` / `BootType: Other` state.
3. **Set the boot config to the reference value.** *Done, 16 Sept.*
   `set_acc_uefi_boot_config --reset --BootType "LocalIscsiBoot"`, as
   Intel's own provisioning script does, replacing the out-of-range
   `BootType: Other`. Config is now correct; on its own it did not
   bring the ACC up.
4. **Fall back to RAM/ramdisk boot** with
   `set_acc_kernel_cmdline.sh -b ramdisk -a -f` plus
   `--BootType "DramBoot"`. This isolates whether the fault is the
   boot *source* (iSCSI target, `acc-rl` partition) or the ACC
   bring-up *mechanism*. Currently blocked: removing
   `reserved-memory.json` to fix item 1's BMD node also zeroed the
   RAM-disk region, so a corrected file has to be restored first.
5. **Verify the boot source exists at all.** *Done, 16 Sept.* `tgtd`
   is running and serving `iqn.e2000:acc`, and `/dev/nvme0n1p4` holds
   a real image. The target was never the problem.
6. **Read `/usr/bin/scripts/start_imc_idpf.sh` line 77.** It logs
   `[: 0x00000000: integer expression expected` one second after
   `Primary not found`. A zero VSI value suggests VSI-group setup the
   ACC depends on may be silently skipped.
7. **Check ACC secure boot state** with `cfg_acc_secure_boot`. This is
   the one configuration in which certificate dates could plausibly
   matter, so establish it before blaming the clock.

**Cheapest check of all:** ask the lab maintainer whether DH4's ACC
has *ever* come up, and whether sibling E2100 units in the same lab
boot their ACCs on the same image. A working sibling on identical
firmware exonerates firmware and makes this unit-specific.

When you do escalate, attach `journalctl -b -o short-iso | grep -iE
'acc|idpf|iscsi|primary|reset'`, the three
`/mnt/imc/acc_variable/*.json` files, and the results of items 1–3
above, so the report describes a tested failure rather than an
inferred one.

## See also

- [`intel-ipu-p4-ipdk.md`](../vendor-readiness/intel-ipu-p4-ipdk.md) —
  what the `p4` control plane's code does and does not do
- [`intel-ipu-ovs-p4.md`](../vendor-readiness/intel-ipu-ovs-p4.md) —
  what the `ovs` control plane's code does and does not do, and why
  it's not a fourth independent hardware mechanism
- [`intel-vm-to-vm-capability-detection.md`](intel-vm-to-vm-capability-detection.md)
  — the three alternate proofs this feeds
- [`first-run-intel-vm2vm.md`](first-run-intel-vm2vm.md) — the
  host-side `labctl vm2vm` runbook this doc is a prerequisite for
