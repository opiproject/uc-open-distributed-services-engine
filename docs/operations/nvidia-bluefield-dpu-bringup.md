# NVIDIA BlueField DPU bring-up: preparing and validating OVS-Kernel + OVS-DOCA

The NVIDIA-side equivalent of
[`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md): the missing
piece between "I can SSH to the BlueField Arm OS" and "I know my
`ovs.datapath_type`/`nvidia.datapath` choice will actually work" —
plus a direct answer to **"how do I confirm both datapath types
(kernel OVS-TC and OVS-DOCA) are available on this card"**.

This is a bring-up runbook, not a code-scope description — for what
this repo's code actually checks/does, see
[`nvidia-dpu-side-ovs-validation.md`](nvidia-dpu-side-ovs-validation.md)
and [`../vendor-readiness/nvidia-bluefield.md`](../vendor-readiness/nvidia-bluefield.md).
Same caveat as everywhere else in this repo: DOCA/BFB flags and package
names drift by train — the commands below are cited from NVIDIA's own
current docs (linked inline) as of when this was written; confirm
against `<DOCA_VERSION>` for your card, don't copy blind.

## 0. The one fact that resolves "how do I get both datapath types"

Unlike a scenario where you'd have to choose one flavor at image-flash
time, **a standard DOCA/BFB image registers all three OVS datapath
implementations at once** — this is not an either/or install choice.
Per NVIDIA's own docs
([Installation Guide for Linux](https://docs.nvidia.com/doca/sdk/nvidia-doca-installation-guide-for-linux.pdf),
[OpenvSwitch Acceleration – OVS in DOCA](https://networking-docs.nvidia.com/doca/sdk/openvswitch-acceleration-ovs-in-doca)):

> NVIDIA OVS installation contains all three OVS flavors [OVS-Kernel,
> OVS-DPDK, OVS-DOCA]... default is OVS-Kernel.

So "ensuring you have both" is a **verification + activation**
question, not a reinstall-and-choose question. There is one command
that answers it directly, on the DPU/Arm side:

```bash
ovs-vsctl list Open_vSwitch
# or just the one field:
ovs-vsctl get Open_vSwitch . datapath_types
```

Expected on a card that truly has both available:

```text
datapath_types      : [doca, netdev, system]
doca_initialized    : false
doca_version        : 3.1.0105
```

- **`system`** = kernel OVS-TC (this project's baseline/default
  datapath — see `nvidia-bluefield.md` §6). Always present if OVS is
  installed at all; no extra activation step.
- **`netdev`** = the OVS-DPDK datapath (§7 in `nvidia-bluefield.md`,
  optional in this project).
- **`doca`** appearing in this list means the **package** supports
  OVS-DOCA. It does **not** mean OVS-DOCA is initialized/usable yet —
  that's the separate `doca_initialized` field, which starts `false`
  even on a card that lists `doca` here (confirmed by NVIDIA's own
  [OVS-DOCA Hardware Acceleration](https://networking-docs.nvidia.com/doca/sdk/ovs-doca-hardware-acceleration)
  page and a real user hitting exactly this in the
  [BlueField DOCA support forum thread](https://forums.developer.nvidia.com/t/enabling-doca-support-in-ovs-on-bluefield-dpu/348227)).

**This "listed vs. initialized" distinction is a real gap in what this
repo's code checks today** — see §4 below.

## 1. Topology (same three-tier shape as Intel, different names)

| Plane | What it is | Config field |
|---|---|---|
| Host x86 | KVM/QEMU VMs, VF passthrough | `host:` |
| BMC / rshim | Out-of-band, BFB flashing | `<RSHIM_DEV>` (see `nvidia-bluefield.md` §1) |
| Arm (DPU OS) | Runs `ovs-vswitchd`, owns representors — what you actually SSH into for everything below | `dpu.management_ip` / `dpu.ssh_user` / `dpu.ssh_key_path` |

Confirm which plane your login reaches, same caveat as the Intel doc:
"I can SSH to the BlueField" is ambiguous until you know if that's the
Arm OS or a BMC shell.

## 2. Preparing the card: BFB/DOCA image

Per the [DOCA Installation Guide](https://docs.nvidia.com/doca/sdk/nvidia-doca-installation-guide-for-linux.pdf):

1. Update DOCA on the **host** side first, before touching the
   BlueField.
2. Flash the full DOCA image (recommended path — overwrites the boot
   partition, updates BlueField + NIC firmware):
   ```bash
   sudo bfb-install --rshim <rshim_dev> --bfb <image_path>.bfb
   ```
   This is the same "vendor firmware bundle" concern
   `nvidia-bluefield.md` §1–2 already flags as `<BFB_IMAGE>`/
   `<DOCA_VERSION>` — do not skip recording which train you flashed.
3. Once booted, SSH to the Arm OS (`dpu.management_ip`) and confirm
   the OVS package really is the DOCA-enabled build (§0's
   `datapath_types` command) — a plain distro OVS package would only
   ever show `[system]`.

This project does not automate BFB flashing (same "opt-in firmware
lifecycle, no blob in git" rule as Intel's P4-package-flashing) — it's
a one-time per-card step, out of scope for a per-forwarding-operation
backend.

## 3. Activating each datapath

**`system` (kernel OVS-TC) — the baseline path, usually already active:**

```bash
ovs-vsctl set Open_vSwitch . other_config:hw-offload=true
```

This is exactly what `plugins/nvidia_bluefield/ovs.py`'s
`enable_ovs_hw_offload()` runs for you under `configure-forwarding
--apply` when `dpu.enable_ovs_hw_offload: true` (the example config's
default). No `doca-init` needed for this path.

**`doca` (OVS-DOCA) — needs explicit activation even once `datapath_types`
lists it,** per NVIDIA's own
[OVS-DOCA Hardware Acceleration](https://networking-docs.nvidia.com/doca/sdk/ovs-doca-hardware-acceleration)
page:

```bash
ovs-vsctl --no-wait set Open_vSwitch . other_config:doca-init=true
ovs-vsctl set Open_vSwitch . other_config:hw-offload=true
# then, per bridge:
ovs-vsctl add-br br0-ovs -- set bridge br0-ovs datapath_type=doca
```

Confirm it actually took (not just requested) by re-running §0's
`ovs-vsctl list Open_vSwitch` and checking `doca_initialized: true`
this time (may require restarting `openvswitch-switch`/`ovs-vswitchd`,
per the same NVIDIA doc). If `doca_initialized` stays `false`, the real
[forum thread](https://forums.developer.nvidia.com/t/enabling-doca-support-in-ovs-on-bluefield-dpu/348227)
above traces this to the DOCA kernel modules (`mlx5_doca.ko`,
`doca_drv.ko`) not being present under `/lib/modules/<kernel>/updates/dkms/`
— check that before assuming it's an OVS config problem.

`other_config:doca-init=true` is exactly the placeholder command
`plugins/nvidia_bluefield/ovs.py`'s `enable_doca_init()` already sends
(see its docstring: "confirm against `<DOCA_VERSION>` docs" — this
now is that confirmation) — but that function only checks whether
`doca` is **listed** in `datapath_types`, matching NVIDIA's docs'
"requested" step; neither it nor
`plugins/nvidia_bluefield/dpu_ovs.py`'s `validate_dpu_ovs_environment()`
currently reads back `doca_initialized`. Treat a passing
`OVS_DOCA_AVAILABLE` result from this repo today as "the package
supports DOCA," not "DOCA is confirmed active" — re-run the raw
`ovs-vsctl list Open_vSwitch` command yourself for the stronger claim
until that field is wired in.

Per NVIDIA's docs, kernel OVS-TC and OVS-DOCA are chosen **per bridge**
(`datapath_type=system` vs `datapath_type=doca`), not per-card — you
can have both kinds of bridge on the same BlueField at once. This
project's `ovs.datapath_type` / `nvidia.datapath` config fields select
which one `configure-forwarding` builds for *this lab's* bridge, not a
card-wide exclusive mode.

## 4. Map to config fields

```yaml
nvidia:
  datapath: ovs-doca            # or "system" for kernel OVS-TC only

dpu:
  enable_ovs_hw_offload: true   # runs other_config:hw-offload=true either way
  preferred_datapath: doca      # informational; drives dpu-side prepare_switchdev hints

ovs:
  bridge: br-int
  datapath_type: doca           # or "system" — this is the actual per-bridge value from §3
```

## 5. Readiness checklist (map every check to a config field / labctl stage)

| # | Check (run on Arm/DPU) | Confirms | Feeds config field | Automatic equivalent once configured |
|---|---|---|---|---|
| 1 | `ssh <dpu.ssh_user>@<dpu.management_ip> echo ok` (from host) | Arm OS reachable | `dpu.management_ip`, `dpu.ssh_user`, `dpu.ssh_key_path` | Every `RemoteCommandRunner` call below refuses honestly instead of hanging |
| 2 | `command -v ovs-vsctl` | OVS installed at all | — | `validate_dpu_ovs_environment()` step 1 |
| 3 | `ovs-vsctl list Open_vSwitch` → `datapath_types` | Which flavors are **compiled in** (`system` always; `doca`/`netdev` if a DOCA-enabled build) | `nvidia.datapath`, `ovs.datapath_type` | `validate_dpu_ovs_environment()`'s `datapath_types`/`doca` fields (see §0 — checks "listed", not "initialized") |
| 4 | `ovs-vsctl list Open_vSwitch` → `doca_initialized` | Whether OVS-DOCA is **actually active**, not just listed | (none yet — see §3's gap note) | **Not checked by this repo today** — read it yourself |
| 5 | `ovs-vsctl show` | OVS database reachable, bridges/ports visible | `ovs.bridge` | `validate_dpu_ovs_environment()` step 5 |
| 6 | `ovs-vsctl get Open_vSwitch . other_config` | `hw-offload`/`doca-init` actually set | `dpu.enable_ovs_hw_offload` | `validate_dpu_ovs_environment()` step 6 |
| 7 | `devlink port show` + `ip -d link show` | Representor names (`pf0vf0` style) | `representor_mapping.*`, feeds `forwarding.endpoints[].representor` | `discover-representors` stage — see [`dpu-side-vf-to-representor-mapping.md`](dpu-side-vf-to-representor-mapping.md) (already thorough; not duplicated here) |

Steps 1–3 and 5–6 are exactly what
[`nvidia-dpu-side-ovs-validation.md`](nvidia-dpu-side-ovs-validation.md)'s
`validate_dpu_ovs_environment()` already automates via
`labctl vm2vm prepare-ovs --dry-run --json`, once
`remote_execution.dry_run_default: false` is set (same dual-gate
convention as Intel's live probes). Step 4 is a manual check you
should still do by hand before trusting `nvidia.datapath: ovs-doca` to
actually accelerate anything, since this repo's status codes
(`OVS_DOCA_AVAILABLE`) currently mean "listed," not "initialized" (see
§3).

## 6. If a check fails

| Symptom | Likely cause |
|---|---|
| SSH to Arm hangs/refuses | Wrong IP for this plane (BMC/rshim vs Arm OS), or BFB never actually booted the Arm OS |
| `datapath_types` shows only `[system]` | Plain distro OVS, not a DOCA-enabled build — reflash with a DOCA/BFB image that includes `doca-openvswitch` (§2), don't try to "enable" DOCA on top of a non-DOCA package |
| `datapath_types` lists `doca` but `doca_initialized` stays `false` after `other_config:doca-init=true` + restart | DOCA kernel modules (`mlx5_doca.ko`, `doca_drv.ko`) missing from `/lib/modules/<kernel>/updates/dkms/` — a DOCA-Host/kernel-support package step was skipped; this is the exact real-world case in [the NVIDIA forum thread](https://forums.developer.nvidia.com/t/enabling-doca-support-in-ovs-on-bluefield-dpu/348227) |
| `ovs-vsctl add-br ... datapath_type=doca` fails / falls back silently | Per NVIDIA's docs, an unsupported `datapath_type` silently falls back to `system` — always re-check `ovs-vsctl get bridge <br> datapath_type` after creating it, don't assume the value you requested stuck |
| `OVS_DOCA_MISSING` from `validate_dpu_ovs_environment()` | `nvidia.datapath: ovs-doca` set but `doca` not in `datapath_types` — confirm §2/§0 first; set `nvidia.allow_host_ovs_for_forwarding`/fallback only if you deliberately want to degrade to `system` |

## See also

- [`intel-ipu-acc-bringup.md`](intel-ipu-acc-bringup.md) — the Intel
  IPU equivalent of this doc
- [`nvidia-dpu-side-ovs-validation.md`](nvidia-dpu-side-ovs-validation.md)
  — what `validate_dpu_ovs_environment()`'s code actually checks
- [`dpu-remote-execution.md`](dpu-remote-execution.md) — SSH mechanics
  and timeout troubleshooting for all of the above
- [`dpu-side-vf-to-representor-mapping.md`](dpu-side-vf-to-representor-mapping.md)
  — representor discovery (step 7 above), already thorough
- [`../vendor-readiness/nvidia-bluefield.md`](../vendor-readiness/nvidia-bluefield.md)
  §6–8 — OVS-Kernel / OVS-DPDK / OVS-DOCA path descriptions this doc
  makes concrete
- [`first-run-nvidia-bluefield-dpu-mode.md`](first-run-nvidia-bluefield-dpu-mode.md)
  — the host-side `labctl vm2vm` runbook this doc is a prerequisite for
