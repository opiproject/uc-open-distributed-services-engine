# hardware-regression

Hardware-in-the-loop regression CI pipeline definitions, run against real
vendor DPU/IPU hardware.

Expected contents:

- Pipeline definitions for lab hardware runners (per vendor track)
- Scheduling/gating rules for merges that require hardware validation
- Result publishing into `docs/vendor-readiness/` and
  `tests/vendor-certification/`
