# dpu_base

Base provisioning role for the DPU/IPU-resident operating system, common
across vendors before vendor-specific roles are applied.

Expected responsibilities:

- DPU OS bootstrap and connectivity validation (management network, SSH)
- Baseline packages and users on the DPU-side OS
- Common health checks and inventory facts collection
- Handoff points for `vendor_*` roles to layer vendor-specific setup
