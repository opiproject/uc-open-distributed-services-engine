# tests/results/ — VM-to-VM single-DPU lab evidence

This directory holds **real lab run evidence** written by
`tools/labctl.py`, `tools/lab_cleanup.py`, and `tools/report_generator.py`.
Nothing under here is a fixture used by `pytest`; fixtures for unit
tests live under `tests/fixtures/`. This directory is gitignored except
for its `README.md` and the one example report kept as reference — do
not commit real hardware evidence here without checking for secrets
(management IPs are fine if they use the documentation ranges from
`config/README-lab-vm2vm.md`; SSH keys, passwords, and real customer
hostnames are not).

## Layout

```
tests/results/
  <vendor-type>/                      # e.g. nvidia-bluefield, marvell-octeon,
                                       #      intel-ipu, amd-pensando
    .latest                           # run id labctl.py reuses when --run-id is omitted
    <run-id>/                         # e.g. 20260115T090000Z, or an explicit --run-id
      discover.json
      validate_config.json
      prepare_host.json
      prepare_vfs.json
      prepare_switchdev.json
      discover_representors.json
      prepare_ovs.json
      create_vms.json
      attach_vfs.json
      configure_forwarding.json
      configure_vm_ips.json
      run_traffic.json
      validate_offload.json
      collect_evidence.json
      cleanup.json                    # labctl.py's thin cleanup delegate record
      cleanup/
        cleanup-summary.json          # tools/lab_cleanup.py's own, fuller evidence
      result-summary.md               # written by tools/report_generator.py
      result-summary.json
  example-vm2vm-single-dpu-result.md  # fake placeholder data; kept in git as a reference
```

`<vendor-type>` matches `vendor.type` from the lab config
(`config/lab-<vendor>-vm2vm.yaml`), normalized by
`plugins.base.lab_config.normalize_vendor`. `<run-id>` defaults to a
UTC timestamp (`resolve_run_id()` in `tools/labctl.py`) unless
`--run-id` is passed explicitly; every stage run without `--run-id`
reuses the vendor's most recent run via the `.latest` marker file, so a
full pipeline naturally lands in one run directory.

Every JSON file's field is either the exact value a real tool observed
or `null` / an explicit error message — never a fabricated default.
See `docs/operations/labctl-usage.md` for what each stage writes and
`docs/operations/cleanup-and-rollback.md` for the `cleanup/` subfolder.

## Generating a result report

Once a run directory has some or all of the 15 stage files above:

```bash
python3 tools/report_generator.py \
    --input-dir tests/results/nvidia-bluefield/20260115T090000Z/ \
    --output result-summary.md
```

This writes `result-summary.md` and `result-summary.json` into the run
directory (or wherever `--output` points, with the JSON twin next to
it). Missing stage files degrade individual report fields to "not
available" rather than failing the whole report — a report can and
should be generated even from a partially-completed or failed run, so
the failure is visible instead of silent.

See [`docs/test-plan/result-report-template.md`](../../docs/test-plan/result-report-template.md)
for what every section means and where its data comes from, and
[`example-vm2vm-single-dpu-result.md`](example-vm2vm-single-dpu-result.md)
for a fully worked example with fake data.

## What NOT to do with this directory

- Do not hand-edit a `result-summary.md`/`.json` to change a verdict.
  If the verdict looks wrong, the underlying stage JSON or
  `report_generator.py`'s logic is wrong — fix that instead.
- Do not copy a passing run's JSON into a new run-id to "seed" a
  result. Every run-id must be the output of stages actually executed
  for that run.
- Do not commit real lab evidence to a shared/public repo without
  reviewing it for management IPs, hostnames, and SSH details that are
  not already redacted per `config/README-lab-vm2vm.md`.
