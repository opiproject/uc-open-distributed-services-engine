# Lab configuration

`lab.example.yaml` is a **template**, not an inventory of a real rack.
Copy it, fill values from discovery, and keep secrets out of git.

## Create a real lab file

1. Run read-only discovery first (`vendorctl discover`, `capability_report.py`).
   Do not copy PF names, PCI addresses, VF counts, or representor names from
   the example file. Those strings are placeholders.
2. Copy the template:

   ```text
   cp config/lab.example.yaml config/lab.yaml
   ```

3. Edit **only** `config/lab.yaml` (gitignored). Set:
   - `hosts[].hostname` to the real compute/DPU-host name
   - `dpu_devices[].vendor` to one of `nvidia-bluefield`, `intel-ipu`,
     `marvell-octeon`, `amd-pensando`
   - `management_ip` from the management plane you actually use
   - `pfs[].name` / `pfs[].pci` from `lspci` / `ip link` / `ethtool -i`
   - `expected_vfs` from `sriov_totalvfs` (or leave `null` if unknown)
   - `bridges[].name` only if that bridge already exists or you intend to
     create it later under `--apply`
   - `bridges[].datapath_type` only if you observed it (`system` is the
     safe default; do **not** set `doca` unless `ovs-vsctl` lists it)
   - `vdpa.enabled` only after `vdpa mgmtdev` (or equivalent) proves a device
   - `opi_endpoint` / `redfish_endpoint` only if those services exist
4. Leave `dry_run: true` until you have a written change window.
5. Never put passwords, tokens, or Redfish credentials in this YAML.
   Point Intel plugins at a separate, mode-`0600` file if needed
   (`ODSE_INTEL_IPU_CONFIG`), and keep that file untracked.

## Safety rules

- **Default is dry-run.** `lab.example.yaml` sets `dry_run: true`.
  `vendorctl` will not apply destructive configure commands while that
  flag is true, even if you pass `--apply`.
- To allow apply: copy to `lab.yaml`, set `dry_run: false`, **and** pass
  `--apply` (and do not pass `--dry-run`). CLI `--dry-run` always wins.
- Config does **not** invent representors. `configure-ovs --ports` must
  list names you observed; do not paste PF names as representors.
- Do not copy NVIDIA switchdev/OVS-DOCA settings onto Intel, Marvell, or
  AMD devices in the same file. Each `dpu_devices` entry is vendor-specific.
- Kind or a VM without the named PCI device is not hardware certification.

## Use with vendorctl

```text
python3 tools/vendorctl.py discover --vendor nvidia-bluefield \
  --config config/lab.example.yaml

python3 tools/vendorctl.py discover --vendor intel-ipu \
  --config config/lab.yaml --host compute-01
```

`--host` selects `hosts[].id`. If omitted, the first host that has a
device matching `--vendor` is used.

## What not to commit

| File | Commit? |
|---|---|
| `config/lab.example.yaml` | Yes (placeholders only) |
| `config/lab.yaml` | No |
| `config/lab.local.yaml` | No |
| Any file with passwords/tokens | No |
