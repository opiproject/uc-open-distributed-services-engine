# Vendor implementation roadmap

How to move `plugins/` **skeletons** to **working** vendor backends
without copying NVIDIA eswitch/OVS-TC onto Intel, Marvell, or AMD, and
without treating Kind or fixture tests as hardware certification.

Companion:

- First 12 steps (repo-wide): [`first-implementation-plan.md`](first-implementation-plan.md)
- Per-vendor flows: [`../vendor-readiness/`](../vendor-readiness/)
- Cert levels: [`../test-plan/vendor-certification.md`](../test-plan/vendor-certification.md)
- Lab template: [`../../config/README.md`](../../config/README.md)
- Fixture tests: [`../../tests/vendor/README.md`](../../tests/vendor/README.md)

A plugin is **working** when discovery is honest on a real SKU, configure
paths stay dry-run unless `--apply` is explicit, and every unproven
capability stays `null`. Files existing in git is not “done.”

---

## Shared phase model

Use the same seven phases for every vendor. **Do not skip** a phase by
inventing devices, representors, or `offloaded: yes`.

| Phase | Goal | Typical commands | Hardware? |
|---|---|---|---|
| 1 Discovery | Parse inventory; no mutations | `vendorctl discover`, `capability_report.py` | Optional (fixtures first) |
| 2 Read-only validation | Tools and identity present | `vendorctl validate` | Optional |
| 3 Controlled configuration | SR-IOV / eswitch / OVS / vDPA only if proven | `vendorctl configure-* --dry-run` then `--apply` | Required for apply |
| 4 Traffic validation | Packets between endpoints | FN connectivity cases | Required |
| 5 Offload validation | Hardware vs software path | `tools/offload-checker`; unknown is valid | Required for L2 |
| 6 Telemetry | Real samples or omit | `vendorctl telemetry` | Optional |
| 7 Certification | Pinned SKU + evidence tree | `tests/vendor-certification/` | Required for level ≥ 1 |

Kind / netdevsim may help **phase 1–2 harnesses** and **cert Level 0**
only. They cannot award Level ≥ 1 or flip the capability matrix to
`supported`.

---

## NVIDIA BlueField

Code: `plugins/nvidia_bluefield/`. Flow:
[`../vendor-readiness/nvidia-bluefield-implementation.md`](../vendor-readiness/nvidia-bluefield-implementation.md).

### 1. Discovery phase

Collect `lspci -nn`, `devlink dev/port show`, `ip -d link`, `ethtool -i`
on mlx5 interfaces, optional `ovs-vsctl show`. Hints: Mellanox, NVIDIA,
mlx5, BlueField, ConnectX. Representors only from `flavour pcivf/pcisf`
or `portname` / `phys_port_name` like `pf0vf0` / `pf0sf0` — **not**
`eth0v1`. `eswitch_mode` stays `null` until `devlink` mode is read.
Fixtures: `tests/fixtures/nvidia_bluefield/`.

### 2. Safe read-only validation phase

`vendorctl validate --vendor nvidia-bluefield`. Confirm `lspci`/`ip` (and
optional `devlink`, `ethtool`, `ovs-vsctl`). Do not set eswitch. Do not
write `sriov_numvfs`. Record firmware from ethtool; do not invent serials.

### 3. Controlled configuration phase

Dry-run `configure-switchdev` / `configure-sriov` / `configure-ovs` /
`configure-vdpa`. Refuse eswitch change while VFs are bound or bind state
is unknown unless `force` is explicit. OVS-DOCA only if `doca` appears in
`datapath_types`. Representor names are never guessed. `--apply` plus
`config/lab.yaml` with `dry_run: false` is required for mutation; plugins
may still stub execution until the lab CLI is documented.

### 4. Traffic validation phase

FN cases: VF (or representor) connectivity through the intended bridge.
Fail closed if the PF/VF/representor set from discovery does not match
the lab file. Software OVS forwarding is **not** hardware offload.

### 5. Offload validation phase

`tools/offload-checker` / `vendorctl validate-offload`. `in_hw` / vendor
hits only when the checker sees them. Missing markers → `unknown`, not
`offloaded: yes`. OVS `other_config:hw-offload=true` is configuration,
not proof.

### 6. Telemetry phase

ethtool / OVS counters when present. Empty list if none. No synthetic
zeros. DOCA extras stay in the NVIDIA plugin only.

### 7. Certification phase

Level 0 from inventory on a pinned BlueField SKU. Levels 1–2 need VF +
honest OVS-TC (or documented DOCA) evidence. Level 3 needs VFIO and/or
vDPA if that path exists. Kind does not raise this track.

---

## Intel IPU

Code: `plugins/intel_ipu/`. Flow:
[`../vendor-readiness/intel-ipu-implementation.md`](../vendor-readiness/intel-ipu-implementation.md).

### 1. Discovery phase

`lspci -nn` must match IPU / E2100 / Infrastructure Processing Unit.
**ice / E810 is not an IPU.** Optional Redfish / OPI / IMC from config
(`ODSE_INTEL_IPU_CONFIG` or lab YAML). Management CLI in config is **not
executed**. Representors stay empty until datapath discovery proves them.

### 2. Safe read-only validation phase

`vendorctl validate --vendor intel-ipu --config config/lab.yaml`. Confirm
config endpoints without HTTP if the client is still a placeholder.
Redfish “configured” ≠ Redfish “probed.”

### 3. Controlled configuration phase

Do **not** copy NVIDIA `devlink eswitch` or OVS-DOCA. Prefer OPI / DPU
Operator / Redfish once those CLIs are documented per SKU. SR-IOV,
switchdev, OVS, and vDPA remain `skipped` until identity and a named
procedure exist. `--apply` with invented IMC commands is an error, not a
best-effort shell string.

### 4. Traffic validation phase

Traffic through the IPU datapath the lab actually uses (often host VF vs
DPU-side port). Do not require `br-int` + representors unless discovery
showed them.

### 5. Offload validation phase

Default `discovery-required`. Do not treat OPI deploy success as
`in_hw`. Skip with evidence if the SKU has no OVS-TC path.

### 6. Telemetry phase

OPI `get_telemetry` may return an empty list. Mock mode is not a vendor
daemon. HTTP mode must not send requests until the client is implemented.

### 7. Certification phase

Often OPI-heavy. Level ≥ 1 still needs real IPU silicon. Config-only
Redfish in CI is Level 0 at most.

---

## Marvell OCTEON

Code: `plugins/marvell_octeon/`. Flow:
[`../vendor-readiness/marvell-octeon-implementation.md`](../vendor-readiness/marvell-octeon-implementation.md).

### 1. Discovery phase

`lspci` hints: Marvell, OCTEON, cn10k, cnxk. Optional `dpdk-devbind.py -s`,
`ovs-vsctl show`, DAO env (`DAO_*`, SDK paths) and binary **existence**.
Kernel representors are not inferred from DPDK port lists.

### 2. Safe read-only validation phase

`vendorctl validate --vendor marvell-octeon`. DAO “available” from env or
binary presence is not a running datapath. Generic OVS ≠ DAO offload.

### 3. Controlled configuration phase

DPDK representors / DAO OVS first; kernel switchdev is **not assumed**.
Placeholders must not execute invented CLI. `apply=True` without a
frozen argv → `error`. `configure-ovs` stays skipped until DAO commands
are documented for the lab train.

### 4. Traffic validation phase

First-packet vs post-offload: software path before rte_flow/DAO hits is
expected. Document which ports are kernel vs vfio-pci.

### 5. Offload validation phase

Need DAO / rte_flow evidence. Kernel `in_hw` is not the default proof.
`ovs_present: true` does not set `ovs_hw_offload_available`.

### 6. Telemetry phase

DAO telemetry placeholder returns no fake counters. Omit missing metrics.

### 7. Certification phase

Pin OCTEON SKU + DAO/DPDK versions. Level 2 requires the documented
offload path, not “OVS is installed.”

---

## AMD Pensando

Code: `plugins/amd_pensando/`. Flow:
[`../vendor-readiness/amd-pensando-implementation.md`](../vendor-readiness/amd-pensando-implementation.md)
(includes gap template G-01…G-12).

### 1. Discovery phase

`lspci -nn`, `ip -d link`, `ethtool -i`/`-S` on **ionic** interfaces,
optional `rdma link show`, `devlink dev show`, modules `ionic` /
`ionic_rdma`. Hints: Pensando, AMD Pensando, ionic, DSC. An AMD **host
bridge is not a DSC**. Missing RDMA is not an Ethernet failure.

### 2. Safe read-only validation phase

`vendorctl validate --vendor amd-pensando`. ionic optional. Do not set
eswitch. Do not assume OVS hardware offload (`try_ovs_offload` stays
false until proven).

### 3. Controlled configuration phase

SR-IOV, switchdev, representors, OVS, and vDPA are **discovery-required**.
Skip configure until G-07…G-10 are filled from the lab. If no
representors, AMD-7 OPI Network is a contingency — do not fake `br-int`
ports.

### 4. Traffic validation phase

ionic netdev / VF traffic only after those objects exist. ionic stats ≠
guest connectivity.

### 5. Offload validation phase

FN-05/17 (or equivalent) before any offload claim. `ethtool -S` is
telemetry, not `in_hw`. Capability report keeps `ovs_hw_offload` **null**
until proven (plugin `False` means “not assumed,” not lab proof of
absence).

### 6. Telemetry phase

`get_ionic_stats` / RDMA facts. Empty if no ionic. No zeros for missing
counters.

### 7. Certification phase

Discovery-first tracks often stop at Level 0–1. Do not copy NVIDIA L2
evidence onto a DSC that never showed representors.

---

## Implementation Rules

These apply to every vendor backend, `vendorctl`, Ansible roles, and
docs.

1. **No destructive actions by default.** Discover and validate are
   read-only. Configure subcommands do not mutate unless `--apply` is
   set, lab `dry_run` is `false`, and CLI `--dry-run` is not set.
   `config/lab.example.yaml` keeps `dry_run: true`.

2. **Every vendor command must support dry-run.** `plugins.base.commands.run`
   defaults to `dry_run=True`. Planned argv is recorded; `executed` stays
   false. Sysfs writes and `ovs-vsctl` mutations follow the same rule.

3. **Every action must return structured results.** Use `OperationResult`
   (status `ok` / `skipped` / `error` / `not_implemented`, devices,
   capabilities, commands, details). Unknown facts are `null`, not `0`
   or `offloaded: yes`.

4. **Every vendor assumption must be documented.** If a path copies
   NVIDIA eswitch, OVS-DOCA, DAO CLI, Redfish, or ionic SR-IOV, say so in
   `docs/vendor-readiness/` and in `VendorCapability.notes`. Undocumented
   assumptions are bugs.

5. **Tests must use fixtures first.** Parsers and capability mapping live
   under `tests/unit/` and `tests/vendor/` with
   `tests/fixtures/<vendor>/`. No live `devlink set` on a developer
   laptop.

6. **Real hardware tests must be separate from unit tests.** Hardware
   belongs in `tests/functional/`, `tests/offload-validation/`, and
   `tests/vendor-certification/reports/`. Fixture success is not Level ≥ 1.

---

## Suggested order of work

1. Keep fixture tests green while filling parsers.
2. Run `capability_report.py` on the lab host; file gaps instead of
   forcing `true`.
3. Copy `config/lab.example.yaml` → `config/lab.yaml` from discovery
   (see [`../../config/README.md`](../../config/README.md)).
4. Dry-run configure; then a change-window `--apply`.
5. Traffic → offload checker → telemetry → cert report for that SKU
   only.

Do not start phase 3 for Intel/Marvell/AMD by cloning
`plugins/nvidia_bluefield/switchdev.py`.
