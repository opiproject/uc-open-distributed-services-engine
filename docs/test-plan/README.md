# Test Plan

**Audience: contributors.** Test strategy, not a runbook. See
[`../README.md`](../README.md) for the full documentation map.

Overall test strategy for ODSE, linking requirements and architecture
decisions to the concrete test suites under `tests/`.

Expected contents:

- Test strategy and coverage goals per milestone
- Mapping of functional, performance, offload-validation, security, and
  vendor-certification suites to architecture components
- Environment/topology requirements for each test tier (simulated vs.
  hardware-in-the-loop)
- Entry/exit criteria for vendor certification

Documents:

- [`performance-methodology.md`](performance-methodology.md) — datapath
  scenarios, metrics, repeatability, `odse-perf-result/v1alpha1` JSON schema
- [`functional-test-plan.md`](functional-test-plan.md) — FN-01…FN-18
  (inventory through failure recovery); skip vs fail; vendor gates
- [`vendor-certification.md`](vendor-certification.md) — Levels 0–5,
  evidence, pass/fail, report template
- [`offload-validation.md`](offload-validation.md) — interpreting
  `ovs_offload_checker.py` output; why connectivity != offload proof;
  before/after comparison procedure
- [`marvell-dao-offload-validation.md`](marvell-dao-offload-validation.md) —
  `marvell_dao_evidence`; host ovs-vsctl is not DAO offload proof
- [`forwarding-validation.md`](forwarding-validation.md) — the five
  distinct claims (configured / traffic works / requested / probable /
  confirmed) and how `configure-forwarding.json` links to
  `validate-offload` evidence
- [`result-report-template.md`](result-report-template.md) — the
  20-section VM-to-VM single-DPU result report `tools/report_generator.py`
  produces, and where each section's data comes from

