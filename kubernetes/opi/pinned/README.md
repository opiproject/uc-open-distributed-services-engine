# Pinned OPI protos

Copied verbatim from [opiproject/opi-api](https://github.com/opiproject/opi-api)
commit `0b58e48b3c91095621ffb1124b89094c6eac3ec7` (2026-01-26).

| File | Upstream path |
|---|---|
| `lifecycle.proto` | `lifecycle/lifecycle.proto` |
| `inventory.proto` | `inventory/inventory.proto` |

Do not edit these files. A newer pin is a new commit plus a fresh copy.

ODSE implements the `google.api.http` routes in those files with
`plugins/base/opi_lifecycle.py` and `tools/opi_server.py`. There is no
generated `*_pb2.py` and no `grpcio` dependency. `curl` against the
annotated paths is the check. `grpcurl` needs a generated gRPC server
this tree does not ship.

`SetNumVfsRequest` still has no device id. The server uses
`dpu.pf_name` from the lab config and reports that beside the proto
body.
