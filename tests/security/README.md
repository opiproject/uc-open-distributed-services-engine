# security

Security-focused test suite validating isolation and threat-model
assumptions documented in `docs/threat-model/`.

Expected contents:

- Tenant/VF isolation tests (VLAN/VNI leakage, spoofing prevention)
- DPU management-plane access control tests
- Negative tests for privilege escalation across host/DPU trust boundary
