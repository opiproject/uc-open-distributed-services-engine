# OPI-style adapter (ODSE)

Vendor-neutral **Open Programmable Infrastructure (OPI)-aligned** API
surface for DPU/IPU inventory, network attach, offload policy, telemetry,
and lifecycle. This is **OPI-aligned**, not a claim of OPI Forum or NSX
API compatibility. Canonical field semantics live in
[`docs/architecture/api-model.md`](../../docs/architecture/api-model.md)
(`apiVersion: odse.opi/v1alpha1`).

```text
 Control plane (Ansible / dpu-operator / OVN-K8s)
                    |
                    v
        kubernetes/opi  (this tree: CRs / adapter process)
                    |
                    v
           VendorBackend (ABC)
          /    |      |      \
      AMD   NVIDIA  Intel  Marvell
                    |
                    v
         vendor CLI / SDK / Redfish / kernel
```

| Piece | Role |
|---|---|
| [`adapter/`](adapter/) | Python skeleton: private `odse.opi/v1alpha1` models. Not the published OPI protos. |
| [`pinned/`](pinned/README.md) | Verbatim `opi-api` lifecycle and inventory protos |
| [`../../tools/opi_server.py`](../../tools/opi_server.py) | HTTP gateway for those protos' `google.api.http` routes |
| [`../../plugins/base/opi_lifecycle.py`](../../plugins/base/opi_lifecycle.py) | Shared implementation used by the gateway and `labctl` |
| [`ansible/roles/opi_services`](../../ansible/roles/opi_services/README.md) | Install/run the adapter (or gRPC server later) on host or DPU OS |
| Vendor Ansible roles | Native enablement; the adapter **wraps** them, it does not replace them |

Unknown capabilities must be `false` / `null`, never guessed `true`.

---

## 1. DPUDevice

Inventory object: one physical DPU/IPU (or simulated stand-in).

| Field | Required | Notes |
|---|---|---|
| `metadata.id` | yes | Opaque, stable |
| `metadata.vendor` | yes | `amd` \| `nvidia` \| `intel` \| `marvell` \| `simulated` |
| `model` | yes | Vendor string; do not normalize |
| `serialNumber` | yes | |
| `firmwareVersion` | yes | |
| `pciAddress` | yes | Host-visible PF if known; `null` only if undiscoverable |
| `capabilities.sriovMaxVFs` | yes | Integer; `0` if unknown/unsupported |
| `capabilities.switchdevSupported` | yes | `false` unless proven |
| `capabilities.vdpaSupported` | yes | `false` unless proven |

Optional: `managementAddress`, `numaNode`, `driverVersion`,
`capabilities.opiNative`, `status.health`.

Python: `adapter.models.DPUDevice`. Backend:
`VendorBackend.list_devices()` / `get_device(id)`.

---

## 2. NetworkFunction

A **DPU-resident** service (ACL proxy, NAT, telemetry exporter) outside
the tenant pod/VM. OVN remains source of truth for tenant ACLs; this
object is for functions the DPU operator places on Arm/IPU cores.

| Field | Required |
|---|---|
| `metadata.id` | yes |
| `dpuDeviceId` | yes |
| `functionType` | yes | e.g. `acl-proxy`, `nat`, `telemetry` |
| `image` | yes | OCI or vendor package id |

Optional: `attachPoint` (representor + direction), `resources`,
`status.state` (`pending` \| `running` \| `failed` \| `unknown`).

Backend: `apply_network_function` / `delete_network_function`. Default
implementation: **not implemented** (TODO per vendor).

---

## 3. NetworkAttachment

Desired bind of a workload-facing port (VF / vDPA / representor) to a
bridge or OVN logical port. Closest api-model objects:
`RepresentorPort` (network-facing update) + `VirtualFunction.boundTo`.

| Field | Required |
|---|---|
| `metadata.id` | yes |
| `dpuDeviceId` | yes |
| `virtualFunctionId` or `representorPortId` | one of |
| `bridgeName` | yes | e.g. `br-int` |
| `spec.adminState` | yes | `up` \| `down` |

Optional: `mtuBytes`, `vlanId`, `ovnLogicalPort`, `status.operState`,
`status.attachedToBridge`.

Backend: `apply_attachment` / `delete_attachment`. Must not invent a
representor name; use
`tools/topology-discovery/vf_to_representor_map.py` or return error.

---

## 4. OffloadPolicy

Desired **offload behaviour** for a device or attachment. Not a flow
table. Maps to `ovs_setup` (`hw-offload`, `datapath_type`) and checker
expectations.

| Field | Required |
|---|---|
| `metadata.id` | yes |
| `targetId` | yes | `DPUDevice` or `NetworkAttachment` id |
| `spec.hwOffload` | yes | boolean |
| `spec.datapathType` | no | `system` \| `netdev` \| vendor string; omit = leave default |

Optional: `spec.requireInHwMarkers` (CI/hardware only; **false** in
Kind simulation), `status.observedOffloadState`.

Backends that cannot honor `hwOffload: true` must **reject** the
object, not silently run software-only.

---

## 5. TelemetrySample

One metric point. Required: `sourceObjectId`, `sourceKind`,
`metricName`, `value`, `unit`, `timestamp` (RFC 3339).

Do not emit fabricated zeros. Unknown metrics are omitted.

Backend: `collect_telemetry(source)`. Normalization of vendor counter
names is documented in `docs/vendor-readiness/`, not in this adapter.

---

## 6. LifecycleOperation

Async verb against a device. Required: `targetDeviceId`,
`operationType` (`firmwareUpgrade` \| `serviceRestart` \| `reboot` \|
`drain` \| `undrain`), `status.state`, `status.requestedAt`.

State machine: `pending` → `inProgress` → `succeeded` \| `failed` \|
`rolledBack`.

If `parameters.dryRun` or `rollbackOnFailure` cannot be honored, the
backend **must fail the request**, not ignore the flag.

Drain fail-open vs fail-closed is **vendor-specific** — see
`docs/architecture/roles-and-trust-boundary.md`.

---

## 7. VendorBackend interface

Python ABC: [`adapter/base.py`](adapter/base.py) class `VendorBackend`.

| Method | Returns |
|---|---|
| `backend_id` | `amd` / `nvidia` / `intel` / `marvell` / `simulated` |
| `list_devices()` | `list[DPUDevice]` |
| `get_device(id)` | `DPUDevice` or raises `NotFound` |
| `apply_network_function` / `delete_network_function` | `NetworkFunction` |
| `apply_attachment` / `delete_attachment` | `NetworkAttachment` |
| `apply_offload_policy` | `OffloadPolicy` |
| `collect_telemetry` | `list[TelemetrySample]` |
| `submit_lifecycle` / `get_lifecycle` | `LifecycleOperation` |

Rules:

- Never raise past the adapter process for “vendor tool missing”: map to
  `BackendError` with `retryable` / `unsupported`.
- `vendorExtensions` is additive only; common fields keep their meaning.
- Stubs in `vendor_*.py` raise `UnsupportedOperation` until a track
  implements them.

Ansible selects the class via `opi_services_backend`
(`amd` \| `nvidia` \| `intel` \| `marvell` \| `simulated`).

---

## 8. Conformance test plan

Home (planned): `tests/vendor-certification/opi-conformance/`. Until that
directory has cases, treat this table as the contract.

| ID | Check | Pass if |
|---|---|---|
| OPI-1 | Envelope | `apiVersion`, `kind`, `metadata.id` on every object |
| OPI-2 | Inventory honesty | `switchdevSupported`/`vdpaSupported` not `true` without evidence |
| OPI-3 | Strip extensions | Object still valid with `vendorExtensions` removed |
| OPI-4 | Attachment | `apply_attachment` either binds a **real** representor or errors |
| OPI-5 | Offload reject | `hwOffload: true` on incapable backend → error, not silent SW |
| OPI-6 | Telemetry | No synthetic zero for missing counters |
| OPI-7 | Lifecycle flags | Unsupported `dryRun` → explicit error |
| OPI-8 | Idempotency | Repeat apply with same id/key is no-op when already applied |
| OPI-9 | Simulated backend | Kind CI uses `simulated`; never talks to vendor SDK |
| OPI-10 | Security | Adapter not reachable from tenant VF/pod network (`tests/security`) |

Generic client (no vendor branch) must round-trip common fields for all
four stubs. Vendor tracks add extra cases in `docs/vendor-readiness/`.

---

## Kubernetes notes

CRDs are **not** checked in yet. When added, names should match `kind`
above under group `odse.opi.example.com` (placeholder). The adapter can
run as a Deployment next to `opi_services`, watching CRs and calling
`VendorBackend`.
