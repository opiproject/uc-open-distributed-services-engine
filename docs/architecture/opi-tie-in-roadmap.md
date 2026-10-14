# OPI tie-in for the VM-to-VM lab

Status: Draft, 30 Sept 2026  
Audience: contributors  
Scope: the single-host VM-to-VM use case (`labctl vm2vm`), every vendor.  
Not in scope: storage offload, IPsec, EVPN fabrics, AI/ML.

This page answers four questions against **published**
[opiproject/opi-api](https://github.com/opiproject/opi-api) (`main` as read
on 30 Sept 2026), not against this repo’s private
`apiVersion: odse.opi/v1alpha1` objects. Those private objects are an
envelope. They are not OPI.

Operator summary of what the lab uses *today*:
[`../operations/user-doc-flow.md`](../operations/user-doc-flow.md).
Design background: [`opi-alignment.md`](opi-alignment.md).

---

## 1. What from OPI we can bring in

OPI’s own rule (its README) is: do not reinvent OVSDB, OpenConfig, or
VPP. Bring an OPI API only when it is the northbound for a step this
lab already does in vendor CLIs.

| Published OPI API | What it actually is | VM-to-VM step it can own | Bring in? |
|---|---|---|---|
| `opi_api.lifecycle.v1.DeviceService.GetDevices` | Device id, health, node. Extracted from the OpenShift DPU Operator (`lifecycle/lifecycle.proto`, 2025). | `discover` — list the PF the lab will use | **Yes. First.** |
| `DeviceService.SetNumVfs` | `SetNumVfsRequest.vf_cnt` → `VfCount` | `prepare-vfs` — same sysfs write `plugins/base/sriov.py` already does | **Yes. First.** |
| `HeartbeatService.Ping` | Timestamp + healthy bool | Readiness of the xPU management channel | **Yes.** `healthy` is SSH to `dpu.management_ip`. |
| `LifeCycleService.Init` | `dpu_mode`, `dpu_identifier` → IP and port | Provision the card | **No.** Gap. The request has no image and no procedure. See section 2. |
| `opi_api.inventory.v1.InventoryService.GetInventory` | SMBIOS plus `PCIeDeviceInfo` (BDF, vendor, product). Reference bridge: `opi-smbios-bridge`. | Cross-check `lspci` BDFs from discovery | **Yes, PCI slice only.** BIOS/chassis/CPU are not this lab. |
| `opi_api.network.cloud.v1alpha1.CloudInfraService` `CreateVnic` / `GetVnic` | Pensando/AMD VPC object. A VNIC is a tenant workload NIC (VM, container, or bare metal) with `host_if_name_ref` when it is bound to a PF/VF. | Later: the *name* of “VM endpoint attached to this VF” | **Not yet.** It requires subnet, encap, and security-policy refs this single-host L2 lab does not have. |
| `CloudInfraService.GetDeviceCapabilities` | Empty request; capabilities live in the Pensando device model | Not a drop-in for `switchdevSupported` | **No**, until we read that message against a real DSC and it matches E2100/BlueField facts. |
| `network/evpn-gw` `BridgePortService` | EVPN gateway port on a logical bridge (FRR reference: `opi-evpn-bridge`) | Not `ovs-vsctl add-port br-int pf0vf0` | **No** for this use case. |
| `security` IPsec (`opi-strongswan-bridge`) | IKE/ESP | Not VM-to-VM | **No.** |
| `storage/*` | NVMe, virtio-blk/scsi/fs, iSCSI backends | Not VM-to-VM | **No.** |
| `aiml/` | Separate taxonomy | Not VM-to-VM | **No.** |

What we should **stop growing** for this use case: new fields on
`odse.opi/v1alpha1` `NetworkAttachment` / `DPUDevice` that duplicate
`GetDevices`, `SetNumVfs`, or `PCIeDeviceInfo`. Map those three to the
published protos. Keep the private envelope only where OPI has no
message yet (representor, offload evidence).

Host VM lifecycle stays libvirt. OPI’s consumption interface is the VF
the guest sees. OPI does not replace `virsh`, and this roadmap does
not ask it to.

---

## 2. What OPI does not do, and this use case needs

Community-facing writeup of these gaps, with a multi-vendor way to
close each one:
[`../presentations/opi-technical-meeting-api-gaps.md`](../presentations/opi-technical-meeting-api-gaps.md).

These are gaps **in OPI** (or in its choice to leave the job to another
standard). They are not missing Python in this repo.

| Need in VM-to-VM | What OPI has | Gap |
|---|---|---|
| Initialize the xPU and learn where it listens | `InitRequest` is `dpu_mode` plus `dpu_identifier`. The response is an IP and port. | No firmware image, no credentials, and no procedure. `dpu_mode` is not switchdev and is not a mode change. The gateway returns HTTP 501. Upstream would have to say what Init does for a BlueField and for an IPU. |
| Create N VFs on **one named PF** | `SetNumVfsRequest` has only `vf_cnt`. No device id, no PCI address. `GetDevices` returns ids separately. | A host with two PFs cannot say which one. Upstream fix: add a device name to `SetNumVfsRequest` and return the VF BDFs. |
| Map VF index → DPU representor (`pf0vf0`, `enp0s1f0d1`) | No representor field on `Device`. Inventory PCI entries are host BDFs, not representor netdevs. | This is the step that makes `configure-forwarding` vendor-neutral. OPI should return it from VF creation or from `GetDevices`. Until then every vendor plugin invents the mapping. |
| Put that representor on a local L2 bridge | EVPN `BridgePort` is a fabric gateway port. Cloud `Vnic` is a VPC endpoint, not `add-port`. OPI explicitly defers switching to OVSDB. | Either document “VM-to-VM bridge attach is OVSDB, OPI only allocates the VF”, **or** add a small host-bridge attachment (VF id, representor, bridge name) that is not an EVPN port and not a VPC. This lab needs that decision in writing. A silent private `NetworkAttachment` does not make the decision. |
| Eswitch / switchdev vs legacy | Not in `lifecycle.proto`. | NVIDIA `prepare-switchdev` has no OPI verb. Do not overload `Init.dpu_mode` to mean switchdev. |
| Offload evidence (`in_hw`, `dp:tc`) | README taxonomy lists Telemetry. There is no `telemetry/` proto in the tree read for this note. USECASES mentions an OTEL collector, not a flow API. | Keep `tools/offload-checker`. Do not invent `offloaded:yes` behind an OPI name. Propose a telemetry proto only after the bridge-attach decision above. |
| Datapath choice (OVS-TC, OVS-DOCA, P4Runtime, IDPF) | Not a common enum. Cloud device capabilities are a different model. | Stays vendor-specific, behind the VF and representor objects. Advertising it as a boolean `opiNative` without a live server is the bug we already have on Intel. |
| Program a P4 table or DOCA flow | Out of scope by OPI’s “do not reinvent existing APIs” rule. P4Runtime and OVSDB already exist. | OPI should name the *intent* (two VNICs, one bridge). The shim speaks `p4rt-ctl` or `ovs-vsctl`. This repo already has those shims. They are not the missing OPI API. |
| Guest SSH, ping, iperf | Consumption interface, not a control API. | Stays `labctl` VM stages. |
| DPU-resident network function (`labctl intel-nf`) | CAPABILITIES.md asks for a runtime API for apps on the xPU. No NF proto. | Do not call the Intel mock deploy an OPI tie-in. |

The single sentence for the OPI project: **lifecycle can create VFs;
nothing in the published API tells you the representor or attaches it
to the bridge this lab uses.**

---

## 3. Low-hanging fruit (add in this repo without an upstream API change)

**Landed 30 Sept 2026.** Pinned protos:
[`../../kubernetes/opi/pinned/`](../../kubernetes/opi/pinned/README.md)
at opi-api `0b58e48b3c91095621ffb1124b89094c6eac3ec7`. Implementation:
`plugins/base/opi_lifecycle.py`, HTTP gateway `tools/opi_server.py`
(the `google.api.http` routes; no `grpcio` / `grpcurl` in this tree).
`labctl vm2vm discover` and `prepare-vfs` record the same proto JSON.
`SetNumVfs` still has no device id; the limitation is an HTTP header
and a field beside the proto body, not a new proto field. Representor
and offload stay labeled `not_opi`.

All of these wrap code that already runs. None of them require a new
P4/OVS behavior.

1. **Pin `opi-api` `lifecycle/lifecycle.proto` and `inventory/inventory.proto`.**
   Record the git revision in this doc when the pin lands. Generate or
   vendor the Python (or gRPC-gateway) stubs. Do not fork the messages.

2. **One server, four RPCs, dry-run by default:**
   - `GetDevices` ← the vendor plugin's `discover()` physical functions.
     The HTTP route does not add `dpu.pf_name` unless discovery found it.
   - `SetNumVfs` ← `plugins/base/sriov.py` (same path NVIDIA and Intel
     `prepare-vfs` already call). Until upstream adds a device id, the
     server takes the PF from lab config (`dpu.pf_name`) and documents
     that limitation on every response.
   - `Ping` ← SSH to `dpu.management_ip`. `healthy: false` when that
     address is unset or the command fails.
   - `GetInventory` ← `PCIeDeviceInfo` from `lspci -nn` only
   - `Init` is not in this list. It is the gap in section 2.

3. **Call that server from `labctl`, or call the same Python functions
   the server calls.** Two entry points, one implementation. A passing
   `grpcurl` of `GetDevices` on a laptop (dry-run, fixture `lspci`) is
   the acceptance test.

4. **Mapping table only where OPI has no message:** representor name
   and offload verdict stay in stage JSON. Label them
   `not an OPI resource`.

5. **File the two upstream gaps** (device id on `SetNumVfs`, representor
   on `Device` or on the `SetNumVfs` response) against `opiproject/opi-api`.
   Do not add those fields to a private proto and call it OPI.

Explicitly not low-hanging: `CreateVnic`, EVPN `CreateBridgePort`,
IPsec, storage, a real Intel OPI HTTP client, OVS-DOCA.

---

## 4. Roadmap

Each phase is done only when its exit check is true. Later phases do
not start by renaming vendor CLIs to OPI.

### Phase 0 — Stop pretending the private envelope is OPI

- This document is the map.
- `user-doc-flow.md` points here.
- New inventory/VF code targets `opi_api.lifecycle.v1` and
  `opi_api.inventory.v1`, not a new `odse.opi` kind.

**Exit:** a reader can point at a published RPC for “list devices” and
“set VF count”, and at a written gap for “representor”.

### Phase 1 — VF lifecycle on the real protos

**HTTP gateway is in tree** (`tools/opi_server.py`). Generated gRPC
stubs are not: this repo does not depend on `grpcio`. `curl` against
the annotated paths is the check.

- Stub server from section 3.
- `prepare-vfs` and `discover` share the implementation.
- Works for NVIDIA and Intel because both already use `sriov.py`.
- AMD/Marvell: `GetDevices` may return the discovered device;
  `SetNumVfs` returns unimplemented until that plugin’s SR-IOV path
  is real. Unimplemented is success for this phase.

**Exit:** dry-run `GetDevices` + `SetNumVfs` against the NVIDIA example
config and the Intel example config, with tests. No hardware required.
`grpcurl` remains future work if a generated server is added.

### Phase 2 — Representor is a returned fact, not a guess

- Upstream proposal: `SetNumVfs` names the device and returns VF BDF +
  representor netdev when the backend knows it.
- Until that merges, NVIDIA fills representor from the existing
  `vf_to_representor_map.py` high-confidence path and returns it in a
  clearly named **extension** (not a forked `Device` message).
- Intel keeps representor unset unless the operator set
  `forwarding.endpoints[].representor`. Do not invent `pf0vfN`.

**Exit:** NVIDIA dry-run (and, on hardware, `--apply`) shows the same
representor string via the OPI-facing call and via
`discover-representors`. Intel still returns empty rather than a
fabricated name.

### Phase 3 — Bridge attach has one owner

Decide and document one of these, then implement only that one:

- **A.** OPI allocates the VF and representor (phases 1–2). Bridge
  attach stays OVSDB (`ovs-vsctl`), which is what OPI’s README already
  prefers. `configure-forwarding` stays the shim. The OPI tie-in is
  complete at “here is the port”; it does not program the bridge.
- **B.** Add a small OPI message, proposed upstream, for host L2 attach:
  VF id, representor, bridge name. EVPN `BridgePort` and cloud `Vnic`
  are the wrong messages. NVIDIA implements B with `ovs-vsctl`. Intel
  implements B with ACC `ovs-vsctl` or refuses until representors exist.
  P4 table strings stay a vendor extension on B, not a second API.

**Recommendation: A first, B only if two vendors need the same attach
RPC.** A is enough to say the lab’s *provisioning* is OPI. B is what
makes *forwarding* OPI. Do not start B by wrapping `CreateVnic`.

**Exit (A):** `configure-forwarding` reads representors produced in
phase 2 and the runbook says OVSDB is intentional.  
**Exit (B):** an upstream proposal exists, and one vendor’s
`--apply` goes through that RPC with dry-run tested for the other.

### Phase 4 — Evidence stays honest

- No OPI telemetry proto: offload checking stays
  `tools/offload-checker`.
- If OPI later adds telemetry or standardizes on OTEL for this, map
  `in_hw` / `dp:tc` there. Until then the metric name in stage JSON is
  not an OPI metric.

**Exit:** no code path reports `offloaded:yes` because an OPI RPC
existed.

### Phase 5 — Vendor shims behind the chosen attach

Only after phase 3’s decision:

- NVIDIA: OVS-TC now; OVS-DOCA only when `datapath_types` contains
  `doca` (already the rule in `plugins/nvidia_bluefield/ovs.py`).
- Intel: ACC OVS or `p4rt-ctl` behind the same VF/representor objects.
  `intel.control_plane: opi` either calls the phase 3 API or is removed
  as a mode. A placeholder `NetworkAttachment` is not a third datapath.
- `labctl intel-nf` stays out of this roadmap until OPI has an NF/runtime
  API.

**Exit:** one lab YAML field selects the vendor shim. The OPI RPCs from
phases 1–3 do not grow a `if nvidia` branch.

---

## What this does not change

- Guest images, IOMMU, VFIO, ping/iperf.
- DH4 ACC firmware triage.
- Flashing an ACC image or a BFB. `LifeCycleService.Init` is not that.
- Claiming OPI Forum conformance. Certification stays Level 5 / M6 in
  [`opi-alignment.md`](opi-alignment.md), and it still requires the
  hardware checks listed there.
