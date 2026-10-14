# Vendor-Neutral API Model

Status: Draft
Scope: Common, vendor-neutral API contract used by the control plane,
`ansible/roles/opi_services`, and `kubernetes/opi` to interact with any
DPU/IPU backend
Audience: Contributors, vendor integration engineers, control-plane
developers
Companion documents: [`overview.md`](overview.md) §9,
[`roles-and-trust-boundary.md`](roles-and-trust-boundary.md)

> **Framing note.** This model is **OPI-aligned**, not a restatement of
> any single vendor's SDK, and not a claim of full OPI (Open Programmable
> Infrastructure) or NSX API compatibility. Per the project's design
> principles (`overview.md` §13), OPI alignment is the long-term target,
> not a hard prerequisite — a vendor backend may implement this contract
> by wrapping native vendor tooling before it can speak an OPI-native API.
> The schema below is deliberately minimal and implementation-friendly: it
> is meant to be easy to serialize as plain JSON, map onto a gRPC/protobuf
> service, or represent as a Kubernetes CRD, not a complete formal
> specification.

---

## Table of contents

1. [Design conventions](#1-design-conventions)
2. [Inventory API](#2-inventory-api)
3. [Provisioning API](#3-provisioning-api)
4. [Network API](#4-network-api)
5. [Telemetry API](#5-telemetry-api)
6. [Lifecycle API](#6-lifecycle-api)
7. [Security Policy API](#7-security-policy-api)
8. [Vendor Extension API](#8-vendor-extension-api)
9. [JSON object reference](#9-json-object-reference)
10. [Validation and conformance summary](#10-validation-and-conformance-summary)

---

## 1. Design conventions

Every object in this model shares a common envelope, so tooling (schema
validators, test harnesses, CLI pretty-printers) can be written once and
reused across all seven API categories:

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "<ObjectKind>",
  "metadata": {
    "id": "<stable, globally unique identifier>",
    "name": "<human-readable name>",
    "vendor": "<amd | nvidia | intel | marvell | simulated>",
    "createdAt": "<RFC 3339 timestamp>",
    "updatedAt": "<RFC 3339 timestamp, optional>",
    "labels": { "<key>": "<value>" }
  },
  "...": "object-specific fields",
  "status": { "...": "observed state, where applicable" },
  "vendorExtensions": { "...": "optional, see Section 8" }
}
```

Conventions used throughout this document:

- **IDs are opaque strings**, stable for the lifetime of the object
  (UUID, or a vendor-stable identifier such as a serial number-derived
  string). Callers must not parse structure out of an ID.
- **Timestamps are RFC 3339 / ISO 8601** (`"2026-08-14T06:00:00Z"`).
- **Enums are lowercase, hyphen-free strings** (e.g. `"switchdev"`,
  `"hardware"`, `"firmwareUpgrade"`). Unknown enum values encountered by a
  client must be treated as unsupported/unknown, not as an error, to allow
  forward-compatible additions.
- **`vendorExtensions` is reserved on every object.** It is always
  optional, always additive, and must never be required for baseline
  interpretation of an object — see [Section 8](#8-vendor-extension-api).
- **`spec` vs. `status` is used only where desired-state/observed-state
  divergence is meaningful** (e.g. `LifecycleOperation`, `VirtualFunction`
  binding state). Purely descriptive/inventory objects (e.g.
  `DPUDevice`) keep observed fields at the top level for simplicity rather
  than forcing an artificial `spec`/`status` split.
- **This is API-shape guidance, not a wire protocol mandate.** A given
  implementation may expose this shape over REST+JSON, gRPC+protobuf, or
  as Kubernetes CRDs (`kubernetes/opi`); the field names and semantics
  should stay consistent across all three.

---

## 2. Inventory API

### Purpose

Discover what DPU/IPU hardware exists, what it is capable of, and how it
maps onto host-visible PCI functions — the prerequisite for every other
API category. Per the project's design principles, this API is
deliberately built to **report actual capability, never assume it**,
since switchdev/SR-IOV/vDPA support is not uniform across the four vendor
tracks.

### Example objects

`DPUDevice`, `HostPF`, `VirtualFunction`, `RepresentorPort`

### Required fields

| Object | Required fields |
|---|---|
| `DPUDevice` | `metadata.id`, `metadata.vendor`, `model`, `serialNumber`, `firmwareVersion`, `pciAddress`, `capabilities.sriovMaxVFs`, `capabilities.switchdevSupported`, `capabilities.vdpaSupported` |
| `HostPF` | `metadata.id`, `dpuDeviceId`, `hostId`, `pciAddress`, `maxVFs`, `eswitchMode` |
| `VirtualFunction` | `metadata.id`, `hostPfId`, `vfIndex`, `pciAddress` |
| `RepresentorPort` | `metadata.id`, `dpuDeviceId`, `virtualFunctionId`, `netdevName` |

### Optional fields

`managementAddress`, `numaNode`, `driverVersion`, `capabilities.opiNative`,
`macAddress`, `trustMode`, `bridgeName`, `mtuBytes`, and any field under
`status` (health, link state, current VF count) — inventory clients should
treat these as best-effort and degrade gracefully if a vendor backend
cannot populate them yet.

### Vendor-specific considerations

- Capability discovery mechanism differs by vendor: kernel-visible facts
  (`devlink`, `ethtool`) are common to all Linux-based DPU OSes, but
  firmware/model/serial-number discovery typically requires a
  vendor SDK, Redfish call, or vendor CLI wrapped by the backend.
- A vendor backend that cannot yet determine a capability must report the
  corresponding field as `false`/absent with a documented reason in
  `docs/vendor-readiness/`, never guess or default to `true`.
- `HostPF.eswitchMode` values are not guaranteed uniform in supported set
  across vendors (e.g. `legacy`, `switchdev`, and any vendor-specific
  intermediate mode) — clients should treat unrecognized values as
  unsupported rather than failing hard.

### Validation tests

- Inventory discovery test: a `DPUDevice` object is populated within a
  bounded time window after DPU boot (`tests/functional`).
- Schema validation test: required fields present and correctly typed for
  every object returned (`ci/lint`, `ci/molecule`).
- Cross-check test: capability flags reported by the API match ground
  truth from `tools/inventory` and raw `ethtool`/`devlink` output.
- Host PF/VF discovery test and representor discovery test (per the
  project's Definition of Done, `open-source-vdse-equivalent-plan.md`
  §10).

---

## 3. Provisioning API

### Purpose

Perform the state-changing actions that turn raw hardware capability into
usable resources: creating VFs, switching eswitch mode, binding drivers,
and establishing DPU management-network reachability. This is the
"day-1" counterpart to the Inventory API's "day-0" discovery.

### Example objects

`ProvisioningRequest` (action envelope), producing/updating `HostPF` and
`VirtualFunction` objects as its result.

### Required fields

`ProvisioningRequest` requires: `targetDeviceId`, `action` (enum:
`createVFs`, `setEswitchMode`, `bindDriver`, `setManagementNetwork`),
`parameters` (an action-specific object, e.g. `{"vfCount": 8}` for
`createVFs`).

### Optional fields

`idempotencyKey` (strongly recommended — see vendor considerations below),
`requestedBy`, `timeoutSeconds`, `dryRun`.

### Vendor-specific considerations

- The number of VFs supported per PF, and whether an eswitch-mode change
  requires a reboot or can be applied live, both vary by vendor and
  firmware revision — the Provisioning API surfaces the *request*
  uniformly, but completion semantics (synchronous vs. requiring a
  follow-up reboot/`LifecycleOperation`) are vendor-specific and must be
  documented per vendor.
- Idempotency matters because provisioning actions are frequently retried
  by automation (Ansible, the DPU operator); a backend must treat a
  repeated request with the same `idempotencyKey` as a no-op if already
  applied, not as an error or a duplicate side effect.

### Validation tests

- VF creation test and representor discovery test (Definition of Done,
  baseline tier).
- Idempotency test: re-submitting an identical `ProvisioningRequest` does
  not create duplicate VFs or change state.
- Rollback test: a failed or partially applied provisioning action leaves
  the device in a documented, recoverable state rather than an undefined
  one.

---

## 4. Network API

### Purpose

Bind logical network state (from OVN, via Neutron or OVN-Kubernetes) to
the physical/virtual topology discovered and provisioned above: attaching
representor ports to a bridge, and — where visible to the API — exposing
the resulting offloaded flow state for validation.

### Example objects

`RepresentorPort` (attachment state), `OffloadFlow`

### Required fields

| Object | Required fields |
|---|---|
| `RepresentorPort` (network-facing update) | `metadata.id`, `bridgeName`, desired `status.adminState` |
| `OffloadFlow` | `metadata.id`, `representorPortId`, `matchFields`, `actions` |

### Optional fields

`RepresentorPort.mtuBytes`, `RepresentorPort.vlanId`;
`OffloadFlow.status.offloadState`, `OffloadFlow.status.packetCount`,
`OffloadFlow.status.byteCount` (all status fields are inherently
best-effort/observational, not settable by a client).

### Vendor-specific considerations

- Not every vendor backend can report **per-flow** hardware-offload state
  at the same granularity — some only expose an aggregate offload hit
  ratio rather than a per-`OffloadFlow` `offloadState`. Backends that
  cannot populate per-flow state must say so explicitly rather than
  fabricating a value.
- Flow identifiers used internally by a vendor's accelerated datapath
  (e.g. a DOCA Flow handle, a DAO flow-table index) are not required to
  match the kernel `tc flower` flow ID; `OffloadFlow.metadata.id` is an
  API-level identifier the backend is responsible for mapping to whatever
  internal identifier its datapath uses.

### Validation tests

- OVS bridge attach test (Definition of Done, baseline tier).
- VM-to-VM and VM-to-wire traffic tests (`tests/functional`).
- Offload detection test: confirms a flow transitions from a software
  path to a reported hardware `offloadState` under load
  (`tests/offload-validation`).

---

## 5. Telemetry API

### Purpose

Surface counters, health signals, and offload effectiveness metrics from
any object in the model (device, PF, VF, representor, flow, or network
function) in a single, vendor-neutral sample format, for consumption by
`tools/telemetry-collector` and `docs/operations/` monitoring.

### Example objects

`TelemetrySample`

### Required fields

`sourceObjectId`, `sourceKind`, `metricName`, `value`, `unit`, `timestamp`.

### Optional fields

`labels` (arbitrary additional dimensions, e.g. `{"direction": "rx"}`),
`sampleIntervalSeconds` (present on periodic/streamed samples).

### Vendor-specific considerations

- Metric namespaces and units differ substantially by vendor
  (`ethtool -S` counter names vs. DOCA telemetry metrics vs. DAO/DPDK
  `rte_flow` statistics). This API does **not** attempt to force a single
  canonical metric-name taxonomy in its first version — instead, each
  vendor backend documents its `metricName` values in
  `docs/vendor-readiness/`, and any cross-vendor normalization (e.g. a
  common `offload_hit_ratio` metric computed differently per vendor) is
  called out explicitly as derived, not raw.
- Backends must not silently drop or synthesize samples to mask a missing
  vendor telemetry source; an absent metric should simply not be emitted,
  rather than emitted as a fabricated zero or default value.

### Validation tests

- Telemetry collection test: samples for a known metric arrive within an
  expected interval (`tests/performance`).
- Cross-validation test: `TelemetrySample` values match raw counters
  queried directly via `tools/offload-checker`.
- Telemetry collection test from the Definition of Done (baseline tier).

---

## 6. Lifecycle API

### Purpose

Manage the operational lifecycle of a DPU/IPU: firmware upgrades, service
restarts, reboots, and drain/undrain operations used before maintenance or
in response to a health signal.

### Example objects

`LifecycleOperation`

### Required fields

`targetDeviceId`, `operationType` (enum: `firmwareUpgrade`,
`serviceRestart`, `reboot`, `drain`, `undrain`), `status.state` (enum:
`pending`, `inProgress`, `succeeded`, `failed`, `rolledBack`),
`status.requestedAt`.

### Optional fields

`parameters.firmwareVersionTarget`, `parameters.dryRun`,
`parameters.rollbackOnFailure`, `status.startedAt`, `status.completedAt`,
`status.errorMessage`.

### Vendor-specific considerations

- Firmware upgrade artifact format and upgrade process are inherently
  vendor-specific; the Lifecycle API normalizes the *verbs and observable
  state machine* (`pending → inProgress → succeeded|failed|rolledBack`),
  not the underlying firmware image or transport.
- Not every vendor backend supports a true `dryRun` or automatic
  `rollbackOnFailure`; a backend that cannot honor either flag must reject
  the request explicitly rather than silently ignoring the flag.
- **Drain semantics are not currently uniform.** Whether `drain` degrades
  gracefully (redirecting flows to a software path) or interrupts traffic
  outright is vendor- and configuration-dependent — this directly relates
  to the fail-open/fail-closed ambiguity flagged in
  [`roles-and-trust-boundary.md` §2](roles-and-trust-boundary.md#2-dpuipu-node)
  and must not be assumed safe without vendor-specific verification.

### Validation tests

- Firmware/lifecycle update dry-run test (Definition of Done, advanced
  tier).
- Reboot recovery test: device returns to `healthy` inventory status
  within an expected window after `reboot`.
- Drain/undrain connectivity test: observes actual traffic behavior during
  a drain operation and records whether it was fail-open or fail-closed
  for that vendor/configuration (feeds `docs/vendor-readiness/`).

---

## 7. Security Policy API

### Purpose

This category deliberately does **not** duplicate OVN's role as the
source of truth for tenant network policy (see
[`roles-and-trust-boundary.md` §4](roles-and-trust-boundary.md#4-threat-boundary)
— policy authorship stays in the OVN control path). Instead, the Security
Policy API covers what OVN does not:

- **Audit/observability** of policy as actually enforced on the DPU
  (read-oriented, for verifying OVN-intended state matches DPU-applied
  state).
- **Policy for DPU-resident network functions** that sit outside OVN's
  logical network model (e.g. a standalone ACL/NAT `NetworkFunction`, see
  [Section 9](#9-json-object-reference)).
- **Access control for the OPI API surface itself** (which control-plane
  callers may invoke which operations against which DPU).

### Example objects

`SecurityPolicyRule` (illustrative — used to configure policy for a
`NetworkFunction`, not for OVN-managed ports), `APICredential`
(illustrative — client identity/role binding for the API itself).

### Required fields

`SecurityPolicyRule`: `direction` (enum: `ingress`, `egress`), `action`
(enum: `allow`, `deny`, `rateLimit`), `matchFields`, `priority`.
`APICredential`: `subject` (the calling identity), `role` (enum:
`readOnly`, `operator`, `admin`), `expiresAt`.

### Optional fields

`SecurityPolicyRule.appliesTo` (list of `representorPortId` or
`networkFunctionId` values it targets), `SecurityPolicyRule.ttlSeconds`,
`SecurityPolicyRule.rateLimitBps`.

### Vendor-specific considerations

- Rule evaluation order/semantics (first-match vs. highest-priority-match)
  are not guaranteed identical across vendor-accelerated policy engines;
  this must be documented per vendor rather than assumed.
- Per-rule hit counters (useful for audit) are not universally exposed;
  where absent, aggregate `TelemetrySample` data should be used instead
  and the gap documented.

### Validation tests

- Security boundary test: confirms a host workload cannot reach this API
  directly, only the control plane can (`tests/security`, directly
  validating the boundary described in
  [`roles-and-trust-boundary.md` §4](roles-and-trust-boundary.md#4-threat-boundary)).
- Policy audit consistency test: OVN-intended ACL state matches the
  DPU-applied state reported through this API's audit view.
- Unauthorized access rejection test: a call using an expired or
  insufficiently privileged `APICredential` is rejected, not silently
  downgraded.

---

## 8. Vendor Extension API

### Purpose

Provide a bounded, clearly namespaced escape hatch for vendor-specific
capabilities that do not yet fit the common schema — consistent with the
design principle that vendor logic stays behind a plugin boundary
(`overview.md` §13) rather than forking the common object model per
vendor.

### Example objects

`VendorExtension` (a generic envelope; in practice this is expressed as
the `vendorExtensions` field present on every object, see
[Section 1](#1-design-conventions)), e.g. a hypothetical
`nvidia.doca.flowAgingPolicy` or `marvell.dao.flowTableStats` block.

### Required fields

`vendor`, `extensionKind`, `payload` (an opaque, vendor-defined object).

### Optional fields

`schemaVersion`, `deprecated` (boolean, set once a field has been
promoted into the common schema), `migratesTo` (a pointer to the common
field name a deprecated extension has been superseded by).

### Vendor-specific considerations

- Extension payloads are intentionally opaque to generic tooling — a
  conformance-testing client must be able to ignore `vendorExtensions`
  entirely and still correctly interpret an object.
- Extensions are expected to be periodically reviewed for promotion into
  the common schema as capability converges across vendors (e.g. if three
  of four vendors converge on an equivalent concept, it becomes a
  candidate for a new common field rather than three parallel
  extensions).
- Extensions must be **additive only** — a vendor backend must never rely
  on a `vendorExtensions` field to override or contradict a common-schema
  field's documented meaning.

### Validation tests

- Additivity test: every core object still validates against the common
  schema with `vendorExtensions` stripped entirely.
- Namespace-collision test: extension field names/keys do not collide
  with reserved common-schema field names.
- Conformance test: a generic (vendor-unaware) client can process an
  object's common fields correctly regardless of which vendor populated
  its `vendorExtensions` block.

---

## 9. JSON object reference

The following are complete, minimal example instances of the eight core
objects referenced throughout this document. Field sets here match the
Required/Optional fields defined per API category above; treat these as
illustrative examples, not an exhaustive formal schema.

### DPUDevice

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "DPUDevice",
  "metadata": {
    "id": "dpu-7f3a1c",
    "name": "dpu-node-03",
    "vendor": "nvidia",
    "createdAt": "2026-08-01T10:00:00Z",
    "labels": { "rack": "r12", "role": "dpu" }
  },
  "model": "BlueField-3",
  "serialNumber": "MT2125X00001",
  "firmwareVersion": "32.41.1000",
  "driverVersion": "24.10-1.1.4.0",
  "pciAddress": "0000:03:00.0",
  "managementAddress": "10.10.10.15",
  "numaNode": 0,
  "capabilities": {
    "sriovMaxVFs": 32,
    "switchdevSupported": true,
    "vdpaSupported": true,
    "opiNative": false
  },
  "status": {
    "health": "healthy",
    "lastInventoryAt": "2026-08-14T05:00:00Z"
  }
}
```

### HostPF

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "HostPF",
  "metadata": {
    "id": "pf-eth0-host12",
    "name": "eth0",
    "vendor": "nvidia",
    "createdAt": "2026-08-01T10:05:00Z"
  },
  "dpuDeviceId": "dpu-7f3a1c",
  "hostId": "host-12",
  "pciAddress": "0000:03:00.0",
  "macAddress": "0c:42:a1:11:22:33",
  "maxVFs": 32,
  "eswitchMode": "switchdev",
  "status": {
    "linkState": "up",
    "currentVFCount": 4
  }
}
```

### VirtualFunction

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "VirtualFunction",
  "metadata": {
    "id": "vf-host12-pf0-3",
    "name": "eth0v3",
    "createdAt": "2026-08-10T09:00:00Z"
  },
  "hostPfId": "pf-eth0-host12",
  "vfIndex": 3,
  "pciAddress": "0000:03:00.3",
  "macAddress": "0c:42:a1:11:22:99",
  "trustMode": false,
  "boundTo": {
    "driver": "vfio-pci",
    "workloadId": "vm-tenant-a-17"
  },
  "status": {
    "state": "bound",
    "representorPortId": "rep-host12-pf0-3"
  }
}
```

### RepresentorPort

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "RepresentorPort",
  "metadata": {
    "id": "rep-host12-pf0-3",
    "name": "pf0vf3",
    "createdAt": "2026-08-10T09:00:05Z"
  },
  "dpuDeviceId": "dpu-7f3a1c",
  "virtualFunctionId": "vf-host12-pf0-3",
  "netdevName": "pf0vf3",
  "bridgeName": "br-int",
  "mtuBytes": 1500,
  "status": {
    "adminState": "up",
    "operState": "up",
    "attachedToBridge": true
  }
}
```

### OffloadFlow

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "OffloadFlow",
  "metadata": {
    "id": "flow-9c21",
    "createdAt": "2026-08-14T06:00:00Z"
  },
  "representorPortId": "rep-host12-pf0-3",
  "matchFields": {
    "ethType": "0x0800",
    "ipProto": "tcp",
    "srcIp": "10.0.1.5",
    "dstIp": "10.0.1.9",
    "dstPort": 443
  },
  "actions": ["output:uplink0"],
  "status": {
    "offloadState": "hardware",
    "packetCount": 184213,
    "byteCount": 27912340,
    "lastUpdatedAt": "2026-08-14T06:05:00Z"
  }
}
```

### NetworkFunction

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "NetworkFunction",
  "metadata": {
    "id": "nf-acl-edge-01",
    "name": "edge-acl-proxy",
    "createdAt": "2026-08-05T12:00:00Z"
  },
  "dpuDeviceId": "dpu-7f3a1c",
  "functionType": "acl-proxy",
  "image": "odse/nf-acl-proxy:0.3.0",
  "attachPoint": {
    "representorPortId": "rep-host12-pf0-3",
    "direction": "ingress"
  },
  "resources": {
    "cpuCores": 1,
    "memoryMb": 256
  },
  "status": {
    "state": "running",
    "startedAt": "2026-08-05T12:01:10Z"
  }
}
```

### TelemetrySample

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "TelemetrySample",
  "metadata": {
    "id": "sample-556a",
    "createdAt": "2026-08-14T06:10:00Z"
  },
  "sourceObjectId": "rep-host12-pf0-3",
  "sourceKind": "RepresentorPort",
  "metricName": "rx_bytes",
  "value": 4021983123,
  "unit": "bytes",
  "timestamp": "2026-08-14T06:10:00Z",
  "labels": {
    "direction": "rx"
  }
}
```

### LifecycleOperation

```json
{
  "apiVersion": "odse.opi/v1alpha1",
  "kind": "LifecycleOperation",
  "metadata": {
    "id": "op-fwupg-0012",
    "createdAt": "2026-08-14T02:00:00Z"
  },
  "targetDeviceId": "dpu-7f3a1c",
  "operationType": "firmwareUpgrade",
  "parameters": {
    "firmwareVersionTarget": "32.42.0512",
    "dryRun": false,
    "rollbackOnFailure": true
  },
  "status": {
    "state": "inProgress",
    "requestedAt": "2026-08-14T02:00:00Z",
    "startedAt": "2026-08-14T02:00:05Z"
  }
}
```

---

## 10. Validation and conformance summary

| API category | Primary test location |
|---|---|
| Inventory | `tests/functional`, `tools/inventory` |
| Provisioning | `tests/functional`, `ci/molecule` |
| Network | `tests/functional`, `tests/offload-validation` |
| Telemetry | `tests/performance`, `tools/telemetry-collector` |
| Lifecycle | `tests/vendor-certification` |
| Security Policy | `tests/security` |
| Vendor Extension | `ci/lint` (schema/additivity checks), `tests/vendor-certification/opi-conformance/` (planned) |

Cross-vendor conformance is defined operationally, not aspirationally: a
vendor backend is considered to correctly implement a given API category
only when it passes the corresponding tests above against real (or, for
CI, simulated) hardware — consistent with the project-wide rule that
**tests are the contract across vendors**
(`overview.md` §13, `open-source-vdse-equivalent-plan.md` §11).

---

## References

- [`overview.md`](overview.md) §9 (Vendor backend layer) — architectural
  context for why this model exists and how vendor backends implement it.
- [`roles-and-trust-boundary.md`](roles-and-trust-boundary.md) — the trust
  boundary this API model must respect, especially for the Security
  Policy and Lifecycle categories.
- `docs/vendor-readiness/` — per-vendor documentation of which parts of
  this model are implemented, partially implemented, or not yet
  supported.
- `ansible/roles/opi_services/`, `kubernetes/opi/` — where this API model
  is deployed and exposed in practice.
- [`open-source-vdse-equivalent-plan.md`](../../open-source-vdse-equivalent-plan.md)
  §10 (Definition of Done) — the operational test list this document's
  validation sections are drawn from.
