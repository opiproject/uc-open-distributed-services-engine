# First-run: Intel network-function offload

Status: Draft  
Scope: Intel IPU **network-function-offload**, not VM-to-VM OVS.  
Config: `config/lab-intel-network-function-offload.example.yaml`

This is the Intel first-run path. It does not copy NVIDIA representors or
host OVS. Missing OPI or DPU Operator prerequisites return
`OPI_ENDPOINT_REQUIRED` / `DPU_OPERATOR_REQUIRED`.

```text
python tools/labctl.py intel-nf validate-config --config config/lab-intel-network-function-offload.example.yaml
python tools/labctl.py intel-nf discover --config <config-file>
python tools/labctl.py intel-nf validate-opi --config <config-file>
python tools/labctl.py intel-nf validate-dpu-operator --config <config-file>
python tools/labctl.py intel-nf deploy-network-function-dry-run --config <config-file>
```

Stages: `validate-config`, `discover`, `validate-opi`,
`validate-dpu-operator`, `deploy-network-function-dry-run`,
`deploy-network-function` (still dry-run unless `--apply` and
`safety.dry_run_default: false`), `run-traffic` (placeholder),
`collect-evidence`, `cleanup`.

`network_function.type` is `proxy | firewall | packet-filter | custom`.
`placement` must be `ipu`. Default remains dry-run.
