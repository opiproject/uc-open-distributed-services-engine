# Result report template — VM-to-VM single-DPU lab

Section-by-section specification for the report `tools/report_generator.py`
produces (`result-summary.md` / `result-summary.json`), and for anyone
filling one in **by hand** from a run that predates the tool, or from a
run where some stages failed to write JSON.

Pairs with:

- [`forwarding-validation.md`](forwarding-validation.md) — the five
  claims (forwarding configured / traffic works / offload requested /
  offload probably active / offload confirmed) sections 10 and 16
  below must keep separate.
- [`offload-validation.md`](offload-validation.md) — how to read the
  raw evidence quoted in sections 12–16.
- [`../operations/labctl-usage.md`](../operations/labctl-usage.md) —
  where each stage's JSON file (read by `report_generator.py`) comes
  from.
- [`../../tests/results/README.md`](../../tests/results/README.md) —
  the on-disk run directory layout `--input-dir` points at.
- [`../../tests/results/example-vm2vm-single-dpu-result.md`](../../tests/results/example-vm2vm-single-dpu-result.md) —
  a fully worked example with fake placeholder data.

**Every section must state what it does not know, not omit it.** A
field with no evidence renders as "not available" — never a blank, a
zero, or a guessed value. This mirrors the "never synthesize" rule used
throughout the DPU tooling (`dpu_inventory.py`,
`ovs_offload_checker.py`, vendor plugins).

---

## Section list

| # | Title | Primary source(s) |
|---|---|---|
| 1 | Test metadata | run directory path, `discover.json` timestamp, which stage files exist |
| 2 | Server details | `validate_config.json` (`config.host_name`, `config.management_interface`), `discover.json` (`dpu_inventory.host`) |
| 3 | Vendor and DPU/IPU details | `validate_config.json` (`config.vendor_type`, `pf_name`, `pf_pci`, `num_vfs`, `preferred_datapath`), `discover.json` (`dpu_inventory.vendor_hints`) |
| 4 | Firmware and driver details | `discover.json` (`dpu_inventory.netdevs.interfaces` entry matching `pf_name`: driver, firmware_version, bus_info) |
| 5 | Kernel and OS details | `discover.json` (`dpu_inventory.host`: system, release, machine, platform) |
| 6 | PF/VF/representor mapping | `discover_representors.json` (`vf_to_representor_map.mappings`: vf_index, vf_pci, representor_netdev, mapping_method, no_representor_reason) |
| 7 | OVS bridge configuration | `validate_offload.json` (`forwarding.bridge`, `forwarding.configured_ports`) or `configure_forwarding.json` (`intent.bridge`); `prepare_ovs.json` (`backend_result`) |
| 8 | VM definitions | `create_vms.json` (`vms`: name, vf_index, created, error) joined with `configure_forwarding.json` (`intent.endpoints`: vf_pci, representor, mac) |
| 9 | VM interface configuration | `attach_vfs.json` (`vms`: vf_pci, skipped/message), `configure_vm_ips.json` (`vms`: ip_cidr, management_host, error) |
| 10 | **Forwarding Configuration** (Prompt J item 4) | `configure_forwarding.json` (`intent`, `result`), `validate_offload.json` (`offload_checker_report.assessment`) |
| 11 | Traffic test results | `run_traffic.json` (`commands`: kind, executed, returncode, dry_run, stdout/stderr tail) |
| 12 | OVS flow evidence | `validate_offload.json` (`offload_checker_report.ovs_vsctl_show`, `.dpctl_show`, `.dpctl_dump_flows`, `.ofctl_dump_flows`) |
| 13 | TC filter evidence | `validate_offload.json` (`offload_checker_report.tc_filters`) |
| 14 | ethtool counter evidence | `validate_offload.json` (`offload_checker_report.ethtool_stats`, `.ip_link_stats`) |
| 15 | CPU usage comparison | `validate_offload.json` (`offload_checker_report.cpu_snapshot`, `.duration`), `collect_evidence.json` (`evidence.cpu_stats`) |
| 16 | Offload confidence assessment | `validate_offload.json` (`offload_checker_report.assessment`: status, confidence, evidence, warnings, limitations) |
| 17 | Failures and anomalies | scan of every stage file above for explicit `error`/`errors`/nonzero-`returncode` markers only — never inferred from missing "success" text |
| 18 | Cleanup result | `cleanup/cleanup-summary.json` (preferred) or `cleanup.json` (`lab_cleanup_report`): stages run, rollback_risks |
| 19 | Final verdict | computed by `report_generator.py` (see below) |
| 20 | Next actions | computed suggestions tied to the verdict and section 17 |

---

## 1. Test metadata

- Run id, vendor track, input directory.
- Which of the 15 `labctl` stage files were found vs. missing — a
  report with 6 of 15 stages present is not a completed run; say so
  plainly rather than only reporting on the stages that exist.

## 2. Server details

- Host name and management interface **as declared in the lab config**
  (`config/lab-<vendor>-vm2vm.yaml`), plus the hostname/platform the
  `discover` stage actually observed. A mismatch between the two is
  worth flagging, not silently preferring one.

## 3. Vendor and DPU/IPU details

- Vendor track (`nvidia-bluefield` / `marvell-octeon` / `intel-ipu` /
  `amd-pensando`), PF name/PCI address, configured VF count, preferred
  datapath, and the raw `vendor_hints` list `dpu_inventory.py` produced
  (each hint has its own confidence — do not collapse to one "detected
  vendor" line).

## 4. Firmware and driver details

- Driver and firmware version for the PF named in config, read only
  from the matching `netdevs.interfaces` entry. If no entry matches
  (PF not visible, wrong name, or `discover` never ran), say so instead
  of leaving the field blank.

## 5. Kernel and OS details

- `system`/`release`(kernel)/`machine`/`platform` from
  `dpu_inventory.py`'s host block. This is the **host** the lab tooling
  ran on; a BlueField/IPU Arm-side OS is a separate fact this report
  does not currently collect (see `docs/vendor-readiness/*.md` for that
  detail).

## 6. PF/VF/representor mapping

- One row per VF: index, PCI address, representor netdev (or `null`),
  `mapping_method` (`devlink_pcivf`, `phys_port_name`, or `null`), and
  `no_representor_reason` when a representor could not be resolved.
  `mapping_method: null` for every VF is a signal the mapping never
  really succeeded, even if the stage exited 0.

## 7. OVS bridge configuration

- Bridge name and the ports actually configured onto it, sourced from
  the *forwarding* stage's own record of what it did (not re-derived
  from raw `ovs-vsctl show`, which lives in section 12 instead).

## 8. VM definitions

- Both VMs: name, vf_index, whether `virsh define` executed, and any
  `create-vms` error, cross-referenced with the VF PCI/representor/MAC
  the forwarding intent recorded for that VM.

## 9. VM interface configuration

- Whether VFIO attach was skipped (and why), and whether the guest IP
  was actually configured over SSH (`configure-vm-ips`), including the
  management host used to reach each guest.

## 10. Forwarding Configuration

Added per Prompt J item 4 — this is the section that ties everything
else together and must show, side by side (never merged into one
boolean):

- **User intent** — `configure_forwarding.json`'s `intent.name` /
  `intent.type` / `intent.mode` (what the operator asked for).
- **Backend selected** — `result.vendor` (which `VendorBackend`
  actually handled the request).
- **Control entity** — `result.control_entity` (what issued the
  commands — e.g. `ovs-vsctl`, a DAO CLI, an OPI service).
- **Forwarding entity** — `result.forwarding_entity` (what actually
  forwards traffic — e.g. a bridge name, a DAO pipeline id).
- **Configured ports** / **Configured flows** — `result.configured_ports`
  / `result.configured_flows`.
- **Offload requested** — `result.offload_requested` (falls back to
  `intent.offload.requested` if the backend did not echo it).
- **Offload configured** — `result.offload_configured` (the backend's
  own belief it issued the enabling commands — not evidence).
- **Offload confirmed or probable** — taken from
  `validate_offload.json`'s `offload_checker_report.assessment.status`
  / `.confidence`, always labeled *probable* unless a vendor plugin
  supplies independent confirmation (`result.offload_confirmed is
  True` alongside `status: probable_offloaded`). See
  `forwarding-validation.md` §1, claim 5 — no backend sets this today,
  so "confirmed" should be rare and always explained when it appears.

## 11. Traffic test results

- Every `run-traffic` command (ping, iperf3 server/client): argv,
  whether it actually executed (vs. dry-run), return code, and a short
  tail of stdout/stderr. A `dry_run: true` command here means **no
  traffic was sent** — do not read it as a pass.

## 12. OVS flow evidence

- Raw-ish `ovs-vsctl show`, `dpctl/show`, `dpctl/dump-flows`, and
  `ofctl dump-flows` output the checker collected, focused on the
  bridge/ports section 7 named (via `--forwarding-result` linkage).

## 13. TC filter evidence

- `tc filter show` per interface, including whether entries show
  `in_hw`.

## 14. ethtool counter evidence

- `ethtool -S` counters and `ip -s link` stats per interface. Useful
  for before/after comparison, not standalone proof.

## 15. CPU usage comparison

- `/proc/stat`-derived CPU snapshot(s), and the before/after delta
  section produced when `validate-offload` (or `ovs_offload_checker.py`
  directly) was run with `--duration <seconds>`. Lower host CPU growth
  under load is *supporting*, not conclusive, offload evidence — see
  `offload-validation.md` §3.

## 16. Offload confidence assessment

- The single `assessment` object: `status`
  (`probable_offloaded` / `probable_partial` / `software_only` /
  `unknown`), `confidence` (0.0–1.0), the `evidence` list (one line per
  contributing signal), `warnings`, and the standing `limitations`
  list every report carries (this is a heuristic, never hardware
  proof).

## 17. Failures and anomalies

- Every explicit error surfaced by any stage: `validate-config` issues,
  `backend_result.status == "error"`, per-VM `create-vms`/`attach-vfs`/
  `configure-vm-ips` errors, nonzero traffic command return codes,
  a missing/failed `ovs_offload_checker.py` invocation, a missing
  VF-to-representor mapping, and any `cleanup` stage error. This
  section can be non-empty even when the final verdict is a PASS —
  report both, do not let one hide the other.

## 18. Cleanup result

- Whether `lab_cleanup.py` ran for this run id, in dry-run or apply
  mode, which of its ten stages executed, and the `rollback_risks` it
  recorded before any `--apply`. "Cleanup did not run" is itself a
  fact worth stating, not an empty section.

## 19. Final verdict

One of:

| Verdict | Meaning |
|---|---|
| `PASS_FUNCTIONAL_ONLY` | Traffic between the VMs worked; no hardware offload evidence exists (requested-but-not-observed, or offload never requested). |
| `PASS_OFFLOAD_PROBABLE` | Traffic worked and `assessment.status` is `probable_offloaded` or `probable_partial`. |
| `PASS_OFFLOAD_CONFIRMED` | Traffic worked, `assessment.status` is `probable_offloaded`, **and** a vendor plugin independently corroborated it (`result.offload_confirmed: true`). Expect this to be rare until vendor plugins implement claim-5 checks. |
| `FAIL_CONNECTIVITY` | A traffic command actually ran and failed (nonzero exit). |
| `FAIL_SETUP` | A setup stage (`validate-config`, `prepare-vfs`, `prepare-switchdev`, `prepare-ovs`, `create-vms`, `attach-vfs`) reported an error before traffic was attempted. |
| `INCONCLUSIVE` | `run-traffic` never ran, or only ran in dry-run mode — there is no real evidence either way. |

`report_generator.py`'s verdict logic is deliberately conservative: it
never upgrades a verdict based on absence of evidence, and it never
infers `PASS_OFFLOAD_CONFIRMED` from `PASS_OFFLOAD_PROBABLE` plus wishful
reading.

## 20. Next actions

Short, verdict-specific suggestions (e.g. "re-run `validate-offload`",
"run cleanup before reusing this hardware") plus a nudge to review
section 17 even on a PASS run, since a report can legitimately be a
PASS with unrelated warnings.

---

## How this template is produced automatically

`python3 tools/report_generator.py --input-dir tests/results/<vendor>/<run-id>/`
reads the 15 `labctl.py` stage JSON files (plus
`cleanup/cleanup-summary.json` if `tools/lab_cleanup.py` ran directly)
from the given run directory and writes `result-summary.md` /
`result-summary.json` following exactly the section numbering above.
Missing stage files degrade individual fields to "not available"; they
do not stop the report from being generated for the stages that do
exist.
