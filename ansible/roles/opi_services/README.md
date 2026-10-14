# opi_services

Skeleton Ansible role for the **OPI-aligned** adapter defined in
[`kubernetes/opi/README.md`](../../../kubernetes/opi/README.md) and
[`docs/architecture/api-model.md`](../../../docs/architecture/api-model.md).

It does **not** yet install a gRPC server or vendor SDK. Current tasks
validate `opi_services_backend` and refuse a non-loopback listen
address without TLS (tenant VF networks must not reach this API).

## Intended responsibilities (TODO)

- Copy `kubernetes/opi/adapter/` to the target (`opi_services_install_adapter`)
- Run it as `odse-opi-adapter` (`opi_services_manage_service`)
- Select `VendorBackend` via `opi_services_backend`: `amd` | `nvidia` |
  `intel` | `marvell` | `simulated`
- TLS for any non-localhost bind
- Health/readiness for `kubernetes/opi` Deployments later

Vendor enablement stays in `vendor_*` roles. This role only **hosts**
the adapter process that calls those capabilities through `VendorBackend`.

## Inputs

See `defaults/main.yml`. Leave `opi_services_backend` empty until a
track implements `list_devices()`.

## Safety

`opi_services_tls_enabled` defaults to `false` and listen defaults to
`127.0.0.1`. The role fails if listen is not loopback and TLS is off.
