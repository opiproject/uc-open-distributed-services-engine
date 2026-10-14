# vendor_intel_ipu

Vendor-specific Ansible role for **Intel IPU E2100-class** hardware.
Companion:

[`docs/vendor-readiness/intel-ipu-e2100.md`](../../../docs/vendor-readiness/intel-ipu-e2100.md)

Intel is ODSE’s **OPI / OpenShift-first** track. Kernel `switchdev` /
OVS-TC / VFIO are **discovery-required** — this role must **not**
assume the NVIDIA BlueField playbook order works.

The role is **documentation and variable contract first**. No firmware
or Redfish writes by default.

## Intended responsibilities (TODO)

1. Discover/assert `<IPU_SKU>` via Redfish/`lspci`; fail if the device
   is not E2100-class (do not treat `ice` alone as proof).
2. Record IMC/`<REDFISH_BASE>` reachability (read-only first).
3. Optionally apply firmware only when `intel_ipu_apply_firmware` is
   true **and** `<INTEL_FW_UPDATE_CMD>` is supplied from vendor docs.
4. Choose DPU OS model: MicroShift vs RHEL kubelet vs appliance
   (`intel_ipu_dpu_os_model`) — do not guess.
5. Wire `opi_services_backend: intel` after
   `IntelIpuBackend.list_devices()` exists.

Generic roles (`sriov_setup`, `switchdev_setup`, `ovs_setup`) are
**opt-in after discovery**, not dependencies.

## Safety

- Default discover/check, never IMC firmware flash.
- Management API/Redfish on OOB only; fail if listen/target would be a
  tenant VF network.
- Do not apply `vendor_nvidia_bluefield` hooks to Intel devices.

## Alignment

| ODSE piece | Intel published analog (likely, unvalidated) |
|---|---|
| `kubernetes/dpu-operator` | OpenShift DPU Operator (`<DPU_OPERATOR_VERSION>`) |
| `kubernetes/opi/adapter` | IPU OPI-style daemon |
| `kubernetes/ovn-kubernetes` | ovn-kube DPU mode on IPU |

OpenStack off-path is **future** for this vendor — out of role scope.

## OPI

`kubernetes/opi/adapter/vendor_intel.py` still raises
`UnsupportedOperation`. Keep `opi_services_manage_service: false`.
`OffloadPolicy.hw_offload: true` must be rejected until offload is
proven (doc §9, OPI-5).
