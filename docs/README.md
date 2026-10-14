# ODSE documentation map

This repository has two audiences. Read the row that matches what you are
trying to do, then stop — you do not need the rest of `docs/` to get
started.

| You are... | Start at | What you get |
|---|---|---|
| An **operator/user** who wants to run something (dry-run or on real hardware) | [`getting-started.md`](getting-started.md), then the flowchart in [`operations/user-doc-flow.md`](operations/user-doc-flow.md) | A use-case-first, vendor-second flow with conditions (DPU/ACC SSH, Intel control plane). That page also answers what OPI this lab actually uses. |
| A **contributor/developer** who wants to understand design, roadmap, or gap-tracking | [Contributor docs](#contributor--developer-docs) below | Architecture decisions, per-vendor maturity roadmaps, test strategy, threat model |

If you only read one link from this page, make it
[`getting-started.md`](getting-started.md).

## Why this page exists

Before this page existed, `docs/` had ~115 files with no index, and
`getting-started.md` effectively only had a working flow for NVIDIA
BlueField — Intel, AMD, and Marvell users hit a dead end after the first
table. This page and the rewritten `getting-started.md` fix that: every
supported (use case, vendor) pair now has exactly one canonical "start
here" document, and every other document says who it is for.

## User docs: pick a use case, then a vendor

The full, linear version of this table lives in
[`getting-started.md`](getting-started.md#pick-a-use-case-then-a-vendor).
Short version:

| Use case | NVIDIA BlueField | Intel IPU | AMD Pensando | Marvell OCTEON |
|---|---|---|---|---|
| VM-to-VM lab (`labctl vm2vm`) | [Mature, dry-run GO](operations/first-run-nvidia-bluefield-dpu-mode.md) | [Discovery-first / mock-first](operations/first-run-intel-vm2vm.md) | Discovery-only ([readiness doc](operations/amd-pensando-readiness.md)) | Gated on DAO/DPDK ([readiness doc](operations/marvell-dao-readiness.md)) |
| Network-function offload (`labctl intel-nf`) | n/a | [First-class Intel path](operations/first-run-intel-network-function-offload.md) | n/a | n/a |
| Discovery / capability report only | `vendorctl.py discover --vendor nvidia-bluefield` | `vendorctl.py discover --vendor intel-ipu` | `vendorctl.py discover --vendor amd-pensando` | `vendorctl.py discover --vendor marvell-octeon` |

Cross-vendor go/no-go before any `--apply`:
[`operations/cross-vendor-readiness-matrix.md`](operations/cross-vendor-readiness-matrix.md).

Other user-facing references you will link to from a runbook, not read
top-to-bottom on their own:

- [`operations/labctl-usage.md`](operations/labctl-usage.md) — stages, dry-run/apply gating, run directories
- [`operations/preflight-readiness.md`](operations/preflight-readiness.md) — read-only readiness audit
- [`operations/configure-forwarding.md`](operations/configure-forwarding.md) — the single forwarding control point
- [`operations/cleanup-and-rollback.md`](operations/cleanup-and-rollback.md) — tearing a run down safely
- [`../config/README-lab-vm2vm.md`](../config/README-lab-vm2vm.md) — lab YAML field reference
- [`usecases/usecases-to-try-as-of-15-august-2026.md`](usecases/usecases-to-try-as-of-15-august-2026.md) — catalog of tryable use cases (not the onboarding path itself)
- [`test-plan/result-report-template.md`](test-plan/result-report-template.md) — reading a generated result report
- [`presentations/opi-summit-2026-10-15.html`](presentations/opi-summit-2026-10-15.html) — OPI Summit 15 Oct 2026 talk deck (speaker notes beside it)

## Contributor / developer docs

These explain *why* the system is designed this way, track *how ready*
each vendor track is, or define *how it is tested/secured*. They are not
step-by-step runbooks — if you are trying to run a lab, go back to
[`getting-started.md`](getting-started.md).

| Directory | Audience | Purpose |
|---|---|---|
| [`architecture/`](architecture/README.md) | Contributors | System design: control/data plane separation, per-vendor control placement, forwarding intent model, OPI alignment |
| [`vendor-readiness/`](vendor-readiness/README.md) | Contributors / maintainers | Per-vendor capability matrix, gap analysis, "what's discovery-required vs proven" — roadmap, not a how-to |
| [`test-plan/`](test-plan/README.md) | Contributors | Test strategy, suite-to-architecture mapping, certification levels |
| [`threat-model/`](threat-model/README.md) | Contributors / security reviewers | Trust boundaries between host, DPU/IPU, and control planes |
| [`operations/milestone-backlog.md`](operations/milestone-backlog.md), [`operations/vendor-implementation-roadmap.md`](operations/vendor-implementation-roadmap.md), [`operations/vendor-maturity-execution-plan.md`](operations/vendor-maturity-execution-plan.md), [`operations/first-implementation-plan.md`](operations/first-implementation-plan.md) | Contributors | Planning/roadmap documents that happen to live under `operations/` for historical reasons — these are backlog, not runbooks |

## Directory guide

| Directory | What's in it | Audience |
|---|---|---|
| `getting-started.md`, `user-guide.html` | Onboarding flow | Users |
| `operations/first-run-*.md`, `operations/*-readiness.md`, `operations/labctl-usage.md`, `operations/*mapping*.md`, `operations/configure-forwarding.md`, `operations/cleanup-and-rollback.md`, `operations/*vm*.md`, `operations/*-remote-execution.md` | Per-vendor and per-stage runbooks | Users |
| `operations/*backlog*.md`, `operations/*roadmap*.md`, `operations/*execution-plan*.md` | Planning documents | Contributors |
| `architecture/` | Design docs | Contributors |
| `vendor-readiness/` | Roadmap + gap-tracking per vendor | Contributors |
| `test-plan/` | Test strategy | Contributors |
| `threat-model/` | Security trust boundaries | Contributors / security reviewers |
| `usecases/` | Catalog of things you can try, given the current repo state | Users, once past `getting-started.md` |

## Reporting a documentation or onboarding gap

If you followed `getting-started.md` and still hit a dead end (as opposed
to an expected `discovery-required` result), that is a documentation bug.
The concrete symptom to report is: which use case, which vendor, which
command, and the exact output — the same way you would report a code bug.
`example-output/<host>/discover.txt` is a good artifact to attach.
