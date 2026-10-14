# AMD SR-IOV / representor / switchdev discovery

```text
python tools/labctl.py amd-discovery <stage> --config config/lab-amd-vm2vm.example.yaml
```

Stages: `validate-config`, `discover-device`, `discover-ionic`,
`discover-rdma`, `discover-sriov`, `dry-run-create-vfs`,
`discover-representors`, `discover-switchdev`, `discover-ovs-feasibility`,
`collect-evidence`, `generate-gap-report`.

Rules:

- Do not create VFs unless `--apply` is passed (and PF identity is proven;
  the skeleton still refuses apply without that proof).
- Do not assume representors, switchdev, or OVS forwarding.
- `configure-forwarding` stays blocked until the gap report is
  `AMD_READY_FOR_VM_TO_VM_DRY_RUN`.

Outputs under `tests/results/amd-pensando/<run-id>/`:

- `amd-device-discovery.json`
- `amd-ionic-discovery.json`
- `amd-rdma-discovery.json`
- `amd-sriov-discovery.json`
- `amd-representor-discovery.json`
- `amd-switchdev-discovery.json`
- `amd-ovs-feasibility.json`
- `amd-gap-report.md`
