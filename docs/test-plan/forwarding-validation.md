# Forwarding validation methodology

Status: Draft — how to read the **chain of evidence** from a
forwarding intent through to an offload verdict, not a results
database. Pairs with
[`offload-validation.md`](offload-validation.md) (how to read a single
`ovs_offload_checker.py` report) and the
[forwarding intent model](../architecture/forwarding-intent-model.md)
(the `ForwardingIntent`/`ForwardingResult` data model referenced
below).

---

## 1. Five distinct claims, easily conflated

A single VM-to-VM lab run produces up to five different, **increasingly
strong**, claims. Treat each as a separate fact with its own evidence
source; never let a later stage's success be read backwards as proof
of an earlier stage's strongest claim.

| # | Claim | Evidence source | What it actually proves |
|---|---|---|---|
| 1 | **Forwarding configured** | `configure-forwarding.json` (`result.success`, `result.configured_ports`, `result.configured_flows`) | The backend *planned* (and, under `--apply`, executed) OVS/DAO/OPI/Pensando commands to wire the endpoints together. In dry-run (the default), this is a plan, not a live path. |
| 2 | **Traffic works** | `run-traffic` stage (ping/iperf3 results) | Packets cross from one VM to the other *somehow*. Says nothing about which datapath moved them — kernel OVS, TC software, hardware offload, or even a path that bypasses the configured forwarding entirely (e.g. a stale route). |
| 3 | **Offload requested** | `configure-forwarding.json` (`intent.offload.requested`, `result.offload_requested`) | The **user's config** asked for hardware offload (`forwarding.offload.requested: true`). This is intent, captured before any command ran. |
| 4 | **Offload probably active** | `validate-offload` stage → `offload_checker_report.assessment` (`status: probable_offloaded`, some `confidence`) | `ovs_offload_checker.py`'s heuristic combined dpctl/tc/ethtool evidence and leans offloaded. This is inference from markers that upstream OVS/TC choose to expose — never a hardware-verified fact. |
| 5 | **Offload confirmed** | Vendor-specific counter/telemetry cross-check (e.g. ASIC flow-table dump, vendor debug counters showing zero CPU touches) that the *specific* flow under test was hardware-switched | The only claim that should be reported as "offload happened," and only when a vendor plugin or manual vendor tooling corroborates it. `assessment.status` never reaches this level on its own — see `assessment.limitations` in every `ovs_offload_checker.py` report. |

`ForwardingResult.offload_configured` (claim 3.5, effectively) sits
between 3 and 4: it is the **backend's own belief** that it issued the
commands to enable offload (e.g. `ovs-vsctl set Open_vSwitch .
other_config:hw-offload=true` succeeded), still not evidence that any
flow actually offloaded. `ForwardingResult.offload_confirmed` is
reserved for a future vendor plugin that performs claim 5's check
inline; today every backend returns `offload_confirmed: false` or
`null` because none does that check yet — treat any backend value here
as unproven if the plugin is a documented placeholder (see
`docs/vendor-readiness/*.md`).

**The most common reporting mistake in this lab is claim inflation:**
reading "traffic works" (claim 2) plus "offload was requested" (claim
3) as if they add up to "offload confirmed" (claim 5). They do not —
claim 2 passes identically whether or not offload happened at all (see
`offload-validation.md` §1), and claim 3 is user intent recorded
*before* anything ran.

---

## 2. How the claims are linked in tooling (intent-to-evidence chain)

Prompt J wires these claims together mechanically so a report never
has to re-derive them by hand:

1. `labctl.py vm2vm configure-forwarding` writes
   `configure-forwarding.json` with `intent` (claims 1 & 3, as
   requested) and `result` (claim 1, as planned/executed, plus
   `offload_configured` — the backend's belief about claim 3.5).
2. `labctl.py vm2vm validate-offload`:
   - Reads `configure-forwarding.json` from the same run directory
     (`ctx.load_prior("configure-forwarding")`).
   - Surfaces the forwarding intent's `name`, `endpoints`, VF PCI
     addresses, representors, bridge, `configured_flows`,
     `offload_requested`, and `offload_configured` in its own
     `forwarding` output section, next to (not merged into) the
     checker's independent observation.
   - Invokes `ovs_offload_checker.py --forwarding-result
     <path-to-configure-forwarding.json>` so the checker collects
     evidence for the *same* bridge/interfaces the forwarding stage
     actually configured (claim 1), rather than a bridge/interface the
     operator has to remember to pass again by hand.
3. `ovs_offload_checker.py` records the forwarding-result fields it
   was given under `report['forwarding_result']` (so a report can be
   read standalone, without needing the original
   `configure-forwarding.json` open next to it) and computes
   `report['assessment']` (claim 4) independently — the assessment
   never trusts `offload_requested`/`offload_configured` as an input;
   it only trusts what dpctl/tc/ethtool actually show at collection
   time. A forwarding-result saying `offload_configured: true` next to
   an assessment saying `status: software_only` is not a
   contradiction to "fix" — it is exactly the kind of gap this
   linkage exists to catch (see §3 example 2 below).
4. `tools/report_generator.py`'s **Forwarding Configuration** section
   (added alongside the rest of the result-summary report, see
   [`result-report-template.md`](result-report-template.md)) renders
   all five claims side by side: user intent, backend selected,
   control entity, forwarding entity, configured ports/flows, offload
   requested, offload configured, and offload confirmed-or-probable
   (taken from `assessment.status`/`confidence`, explicitly labeled
   "probable" unless a vendor plugin supplies claim-5 evidence).

## 3. Worked examples

**Example 1 — everything lines up (expected good case).**
`intent.offload.requested: true` → `result.offload_configured: true`
→ traffic passes → `assessment.status: probable_offloaded`,
confidence `0.8+`. Report this as "offload requested, configured, and
probably active" — still not "confirmed" unless a vendor plugin says
so.

**Example 2 — configured but not active (the case this linkage is
for).** `intent.offload.requested: true`, `result.offload_configured:
true` (backend issued the hw-offload command successfully), traffic
passes, but `assessment.status: software_only`. This is a **real,
reportable gap** — offload was requested and the backend believed it
configured it, but the checker's independent evidence says the flow is
still software-forwarded (common causes: flow not warmed up yet,
firmware/driver doesn't support this flow's match fields in hardware,
eswitch mode misconfigured). Do not paper over this by reporting
"offload requested" and stopping there — report claim 3.5 and claim 4
as the two independent facts they are, exactly as
`report_generator.py`'s Forwarding Configuration section does.

**Example 3 — VF passthrough, no OVS in the loop.**
`forwarding.mode` uses direct VF passthrough (no bridge/representor
wiring); `configure-forwarding.json` may show no `configured_ports`
entries because OVS was never in the path. Traffic passes via the
SR-IOV switch directly. `ovs_offload_checker.py` correctly reports
`assessment.status: unknown` (no OVS/TC data to inspect) — this is
**not a failure of claims 1 or 2**, it is a topology where claim 4
simply does not apply the way it does for `ovs_hw_offload_kernel`
scenarios (see `performance-methodology.md` §3–4).

## 4. What to write in a result report

For every run, the result report (`result-summary.md`, see
`docs/test-plan/result-report-template.md`) must state all five claims
explicitly, even when some are `null`/`not applicable`:

- Forwarding configured: yes/no, from `configure-forwarding.json`
  `result.success`.
- Traffic works: yes/no, from the `run-traffic` stage.
- Offload requested: yes/no, from `intent.offload.requested`.
- Offload probably active: `assessment.status` + `assessment.
  confidence`, explicitly labeled *probable* (never *confirmed*
  in this row).
- Offload confirmed: only "yes" if a vendor plugin or manual
  vendor-tool cross-check exists in `evidence_files`; otherwise
  "not verified" — never inferred from the other four.

Never collapse these into a single boolean "offload: yes/no" summary
line. The whole point of the intent-to-evidence chain in §2 is that
each claim can independently be true or false, and a report that
merges them hides exactly the gap (Example 2 above) that operators
most need to see.
