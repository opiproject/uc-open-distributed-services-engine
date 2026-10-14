# Intel IPU implementation flow (ODSE)

How Intel **E2100-class IPU** enablement should start in this repo.
This is **not** the NVIDIA BlueField OVS-TC / OVS-DOCA sequence.

Intel is **OPI / OpenShift DPU Operator first**. Kernel `switchdev`,
representors, and OVS hardware offload are **discovery-required**. Do
not copy `plugins/nvidia_bluefield/ovs.py` or assume `datapath_type=doca`.

Companion: [`intel-ipu-e2100.md`](intel-ipu-e2100.md),
`plugins/intel_ipu/`, `docs/architecture/opi-alignment.md`.

---

## Order of work

### 1. Discovery (PCI / netdev / devlink)

`plugins.intel_ipu.discovery.discover()` runs read-only:

- `lspci -nn`
- `ip -d link show`
- `devlink dev show`

A device is treated as an IPU only if the text contains **IPU**,
**E2100**, or **Infrastructure Processing Unit**. An Intel `ice` /
E810 Ethernet controller **does not** count.

Empty device lists on a laptop are valid.

### 2. Redfish / IMC reachability

Management is **not** a frozen CLI (`ipmitool`, vendor binaries). Put
endpoints in a JSON config (`ODSE_INTEL_IPU_CONFIG` or `load_config(path)`):

```json
{
  "redfish_base": "https://<IMC_ADDRESS>/redfish/v1",
  "imc_address": "<IMC_ADDRESS>",
  "opi_endpoint": "https://<OPI_DAEMON>",
  "opi_mode": "mock",
  "management_cli": null
}
```

If `management_cli` is set, discovery **records** it and **does not
execute** it. Redfish HTTP probe is not enabled in the skeleton
(`redfish_probed: false`). Fill `<IPU_REDFISH_DOC_URL>` per SKU; do not
commit payloads.

`IntelIpuBackend.validate_prerequisites()` reports whether Redfish/OPI
URLs are **configured**, not whether they answered.

### 3. OPI-style daemon

`plugins.intel_ipu.opi.OpiClient`:

| Method | Mock | HTTP |
|---|---|---|
| `list_devices()` | Optional `mock_devices` from config | Placeholder, **no request** |
| `get_device(id)` | Lookup in mock list | Placeholder |
| `deploy_network_function(spec)` | In-memory only | Placeholder |
| `delete_network_function(name)` | In-memory only | Placeholder |
| `get_telemetry(device_id)` | Empty samples (no fake zeros) | Placeholder |

Vendor protobuf ≠ `odse.opi/v1alpha1` until a **mapping table** is
filled (`mapping_table` in config). See OPI-1…10.

### 4. DPU Operator / OpenShift alignment

`plugins/intel_ipu/dpu_operator.py` remains a no-mutate stub. Pin
`<DPU_OPERATOR_VERSION>` from Intel/Red Hat docs. Helm in this repo
stays comment-only until that pin. Host cluster vs IPU OS cluster
follows `kubernetes/ovn-kubernetes/` **only** if the operator documents
the same split — do not assume NVIDIA DPU-mode labels.

### 5. Datapath (later, not first)

After inventory + Redfish + OPI/operator:

- SR-IOV / representors / OVS: **only** if discovery proves them.
- `configure_switchdev` / `configure_ovs_bridge` on this backend
  **skip** by design.

---

## What “done” means for this track (near-term)

- lspci parser distinguishes E2100-class hints from ice NICs.
- Config can describe Redfish and OPI without running a vendor tool.
- OPI client mock works in unit tests; HTTP does not phone home.
- No claim of OVS-DOCA, switchdev, or cert Level 2 from Kind.

Hardware labs still use FN-01 and later FN-02 (Redfish) when an IMC
exists. Kind cannot award Intel datapath levels.
