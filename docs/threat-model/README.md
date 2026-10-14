# Threat Model

**Audience: contributors / security reviewers.** See
[`../README.md`](../README.md) for the full documentation map.

Security threat modeling for the ODSE stack, covering trust boundaries
between host, DPU/IPU, hypervisor, and control planes.

- [`host-dpu-threat-model.md`](host-dpu-threat-model.md) — host vs DPU/IPU
  separation (design intent; not a certification)
- Role overview:
  [`../architecture/roles-and-trust-boundary.md`](../architecture/roles-and-trust-boundary.md)

Intended validation: `tests/security/` (not complete). Isolation is
**discovery-required** for all four vendor tracks in
[`../vendor-readiness/vendor-capability-matrix.md`](../vendor-readiness/vendor-capability-matrix.md).
