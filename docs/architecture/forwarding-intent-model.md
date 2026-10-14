# Forwarding intent model (ODSE)

Status: Draft  
Scope: The single user-facing forwarding control point for the VM-to-VM
single-DPU lab use case, and how it is expected to extend to more than
two endpoints.  
Audience: Contributors, lab operators  
Companions: [`api-model.md`](api-model.md),
[`../operations/labctl-usage.md`](../operations/labctl-usage.md),
[`../operations/configure-forwarding.md`](../operations/configure-forwarding.md),
[`../../config/README-lab-vm2vm.md`](../../config/README-lab-vm2vm.md)

---

## 1. Why `labctl.py` is the single user-facing forwarding control point

Connecting two VMs through a DPU/IPU touches a different tool per
vendor: OVS (kernel or DOCA) on NVIDIA, DAO/DPDK representors on
Marvell, an OPI/DPU-Operator style API on Intel, and (today) an
undiscovered control path on AMD Pensando. If users are taught to run
`ovs-vsctl`, `ovs-ofctl`, a DAO CLI, or an OPI client directly, every
lab becomes vendor-specific knowledge that does not transfer, and every
"how do I connect VM1 to VM2" question has four different answers.

ODSE instead exposes exactly one command for this use case:

```text
python tools/labctl.py vm2vm configure-forwarding --config <config-file> --apply
```

Everything vendor-specific happens *behind* that command, inside a
single `VendorBackend.configure_forwarding()` call per vendor.

## 2. User-facing control vs. backend implementation

| Layer | Owns | Example |
|---|---|---|
| User | Describes *what* should be connected (`forwarding:` block in a lab config) | "VM1 and VM2, bidirectional, offload requested" |
| `tools/labctl.py` | Builds a `ForwardingIntent`, resolves VF→representor mapping, selects the vendor backend, records evidence | `plugins.base.vm2vm_config.build_forwarding_intent()` |
| `VendorBackend.configure_forwarding()` | Decides *how* — or whether it can yet — realize that intent on real (or not-yet-discovered) hardware | NVIDIA: dry-run OVS bridge/port plan. Marvell: DAO placeholders or `DAO_NOT_AVAILABLE`. Intel: OPI placeholder or `OPI_ENDPOINT_REQUIRED`. AMD: `REPRESENTOR_OR_CONTROL_PATH_REQUIRED` today. |
| Vendor CLI/API (OVS, DAO, OPI, DOCA, devlink, tc) | The actual mechanism | Never called directly by a user in this workflow |

Users should not call OVS, DAO, DPU Operator, OPI, DOCA, `tc`,
`devlink`, or vendor SDKs directly for this use case — see
[Single Forwarding Control Point](../../README.md#single-forwarding-control-point)
in the top-level README.

## 3. Forwarding entity vs. control entity

Every `ForwardingResult` names two things, because "what actually
forwards the packet" and "what you talk to in order to configure it"
are frequently different pieces of hardware/software:

- **`forwarding_entity`** — the thing that will actually move packets
  once configured (e.g. "NVIDIA BlueField/ConnectX E-Switch", "Marvell
  OCTEON hardware path", "Intel IPU packet pipeline ... (discovery
  required)", "AMD Pensando DSC datapath (if supported)").
- **`control_entity`** — the chain you go through to configure it (e.g.
  `labctl -> nvidia backend -> OVS/OVS-DOCA -> E-Switch`).

This separation matters most for Intel and AMD today: the
*control_entity* (OPI endpoint, or "discovered Pensando control path")
may exist even when the *forwarding_entity*'s exact behavior has not
been confirmed, which is why those backends return `discovery-required`
warnings/errors instead of a confident success.

## 4. How VM1-to-VM2 forwarding is represented

A `ForwardingIntent` (`plugins/base/models.py`) is built from the
`forwarding:` block of a VM2VM lab config
(`config/lab-vm2vm-single-dpu.schema.yaml`):

```text
ForwardingIntent
  name, type=l2-vm-to-vm, mode=bridge
  endpoints: [ForwardingEndpoint(vm1, vf_index=0, representor=...),
              ForwardingEndpoint(vm2, vf_index=1, representor=...)]
  policy: ForwardingPolicy(action=allow, bidirectional=true)
  offload: ForwardingOffloadRequest(requested, fallback_to_software, require_confirmed_offload)
  explicit_flows: {enabled: bool}
  bridge: "br-int"          # from ovs.bridge, not part of the user-authored forwarding: block
  details: {datapath_type: "system"}   # from dpu.preferred_datapath, free-form passthrough
```

`representor`/`vf_pci` on each endpoint start `null` in the config and
are filled in by `labctl vm2vm configure-forwarding` from a prior
`discover-representors` result
(`tools/topology-discovery/vf_to_representor_map.py`) before the intent
is handed to the backend — they are never hand-guessed.

## 5. How VM1-to-VM2-to-VM3 is represented later

`ForwardingIntent.type` already distinguishes two shapes:

- `l2-vm-to-vm` — exactly 2 endpoints, the only shape validated and
  exercised by this lab use case today.
- `l2-segment` — 3+ endpoints on one shared L2 domain (VM1, VM2, VM3,
  ...). The dataclass shape (a list of `ForwardingEndpoint`, one shared
  `policy`, one shared `offload` request) already supports this without
  a schema change; what remains for a future prompt is: (a) validation
  that requires >=3 endpoints for `l2-segment`, (b) each backend's
  `configure_forwarding()` looping over all endpoints instead of
  assuming exactly 2 (NVIDIA's implementation already loops, so it is
  the closest to ready), and (c) an explicit-flows strategy for N>2
  (full mesh vs. hub-and-spoke), which is out of scope until a 3-VM use
  case is added.

## 6. How vendor backends translate the same intent differently

| Vendor | `configure_forwarding()` behavior today |
|---|---|
| NVIDIA | Real (dry-run) plan: create/verify OVS bridge, attach representor ports, optionally add explicit OpenFlow rules, optionally enable hw-offload. Most complete of the four. |
| Marvell | Checks DAO availability and DPDK representor mapping first; returns `error=DAO_NOT_AVAILABLE` or `error=REPRESENTOR_MAPPING_REQUIRED` when either is missing, otherwise stages DAO OVS-offload as placeholders. |
| Intel | Requires `opi_endpoint`; without it returns `error=OPI_ENDPOINT_REQUIRED`. With it, builds an OPI/DPU-Operator style placeholder object but still reports `discovery_required=True` because the exact VF-switching mechanism is unconfirmed. |
| AMD | Discovery-first: with no discovered representors (the current state), returns `error=REPRESENTOR_OR_CONTROL_PATH_REQUIRED` plus the specific capability gaps recorded in `details`. |

## Conceptual diagram

```text
User
  -> labctl.py vm2vm configure-forwarding --config <file> --apply
  -> ForwardingIntent            (plugins.base.vm2vm_config.build_forwarding_intent)
  -> VendorBackend.configure_forwarding(intent)
  -> vendor-specific implementation   (OVS/OVS-DOCA | DAO/DPDK | OPI/DPU-Operator | discovery)
  -> DPU/IPU hardware forwarding entity
```

## Related error codes

Defined once in `plugins/base/models.py` so `labctl`, `report_generator.py`,
and tests can branch on them without string matching:

| Constant | Meaning |
|---|---|
| `FORWARDING_ERROR_NOT_IMPLEMENTED` | Base `VendorBackend.configure_forwarding()` stub (should not appear for the four bundled backends) |
| `FORWARDING_ERROR_REPRESENTOR_REQUIRED` | Reserved for backends that need a representor and have none |
| `FORWARDING_ERROR_DAO_NOT_AVAILABLE` | Marvell: DAO environment/binary not discovered |
| `FORWARDING_ERROR_REPRESENTOR_MAPPING_REQUIRED` | Marvell: DPDK representor mapping missing |
| `FORWARDING_ERROR_OPI_ENDPOINT_REQUIRED` | Intel: no `opi_endpoint` configured |
| `FORWARDING_ERROR_REPRESENTOR_OR_CONTROL_PATH_REQUIRED` | AMD: no discovered representor or control path |

## Known limitations

- `explicit_flows` on the NVIDIA backend plans symbolic
  `in_port=<repr>,actions=output:<repr>` flow strings; the exact
  `ovs-ofctl`/OpenFlow syntax needed for symbolic port names has not
  been confirmed against a specific OVS build. Treat it as a starting
  point, not a validated command.
- Every backend's `configure_forwarding()` is hard-coded to plan (never
  execute) OVS/DAO commands today, the same way `configure_switchdev`
  and `configure_ovs_bridge` are — `--apply` at the `labctl`/`vendorctl`
  layer does not yet flip these to real execution.
- `l2-segment` (3+ endpoints) is modeled but not validated or tested end
  to end; see §5.
