# OPI adapter (Python skeleton)

Non-functional **VendorBackend** plus dataclasses for ODSE OPI-aligned
objects. Wire protocol (gRPC vs watching CRDs) is not chosen yet.

```text
kubernetes/opi/adapter/
  models.py           DPUDevice, NetworkFunction, NetworkAttachment,
                      OffloadPolicy, TelemetrySample, LifecycleOperation
  base.py             VendorBackend, BackendError, load_backend()
  vendor_amd.py       AmdPensandoBackend
  vendor_nvidia.py    NvidiaBlueFieldBackend
  vendor_intel.py     IntelIpuBackend
  vendor_marvell.py   MarvellOcteonBackend
```

Run from this directory (stdlib only):

```bash
cd kubernetes/opi/adapter
python3 -c "from base import load_backend; load_backend('nvidia').list_devices()"
# raises UnsupportedOperation until a vendor track implements it
```

TODOs (all backends):

- Inventory from `tools/inventory/dpu_inventory.py` + vendor CLI
- Attach via representor map tool; never guess netdev names
- Reject `OffloadPolicy.hw_offload=true` when capability is unknown
- Telemetry: omit missing metrics
- Lifecycle: fail closed on unsupported `dryRun` / `rollbackOnFailure`

Do not add vendor SDK pip dependencies here until a track pins versions.
