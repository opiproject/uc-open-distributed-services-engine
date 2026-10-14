#!/usr/bin/env python3
"""HTTP gateway for the pinned OPI lifecycle and inventory protos.

Serves the google.api.http routes in kubernetes/opi/pinned/. The JSON
body is the proto message. Limitations that are not proto fields are
HTTP headers, not extra JSON keys.

    python tools/opi_server.py --config config/lab-nvidia-vm2vm.example.yaml

Default is dry-run and 127.0.0.1:8080. --apply writes sriov_numvfs only
when the lab config also has safety.dry_run_default: false.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.commands import command_exists, run  # noqa: E402
from plugins.base.models import STATUS_OK  # noqa: E402
from plugins.base.opi_lifecycle import (  # noqa: E402
    OpiCall,
    get_devices,
    get_inventory,
    init_xpu,
    ping,
    set_num_vfs,
)
from plugins.base.vm2vm_config import config_dry_run, load_vm2vm_config  # noqa: E402


def _json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    loaded = json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("JSON object required")
    return loaded


def _vf_cnt(payload: Dict[str, Any]) -> Any:
    if "vfCnt" in payload:
        return payload["vfCnt"]
    return payload.get("vf_cnt")


def discover_physical_functions(config: Optional[dict]):
    """Vendor discovery for GetDevices. Does not echo dpu.pf_name by itself."""
    vendor = ""
    block = (config or {}).get("vendor")
    if isinstance(block, dict):
        vendor = str(block.get("type") or "")
    if not vendor or (vendor.startswith("<") and vendor.endswith(">")):
        return [], "vendor.type is unset; GetDevices does not echo the lab YAML"
    try:
        from plugins import load_backend
        from plugins.base.errors import UnknownBackend
    except ImportError as exc:
        return [], "vendor plugin import failed: %s" % exc
    kwargs = {"lab_config": config, "config": config}
    dpu = (config or {}).get("dpu") if isinstance((config or {}).get("dpu"), dict) else {}
    if dpu.get("dao_app"):
        kwargs["configured_dao_app"] = dpu.get("dao_app")
    try:
        backend = load_backend(vendor, **kwargs)
    except UnknownBackend as exc:
        return [], str(exc)
    try:
        result = backend.discover()
    except Exception as exc:
        return [], "discovery failed: %s" % exc
    functions = list(getattr(result, "physical_functions", None) or [])
    message = getattr(result, "message", None) or ""
    if not functions:
        return [], message or "discovery returned no physical functions"
    if getattr(result, "status", None) != STATUS_OK and message:
        return functions, message
    return functions, None


def _lspci_nn() -> Optional[str]:
    if not command_exists("lspci"):
        return None
    result = run(["lspci", "-nn"], dry_run=False, reason="opi GetInventory")
    if result.executed and result.returncode == 0:
        return result.stdout
    return None


def _send(handler: BaseHTTPRequestHandler, call: OpiCall) -> None:
    encoded = json.dumps(call.body).encode("utf-8")
    handler.send_response(call.http_status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("X-Odse-Opi-Service", call.service)
    if call.limitation:
        handler.send_header("X-Odse-Opi-Limitation", call.limitation)
    if call.dry_run:
        handler.send_header("X-Odse-Dry-Run", "true")
    handler.end_headers()
    handler.wfile.write(encoded)


class OpiGateway:
    def __init__(self, config: Optional[dict], dry_run: bool = True, discover_fn=None, ping_probe=None) -> None:
        self.config = config or {}
        self.dry_run = dry_run
        self.discover_fn = discover_fn or discover_physical_functions
        self.ping_probe = ping_probe

    def handle(self, method: str, path: str, payload: Optional[dict]) -> OpiCall:
        payload = payload or {}
        if method == "GET" and path == "/v1/lifecycle/devices":
            functions, note = self.discover_fn(self.config)
            call = get_devices(
                self.config,
                physical_functions=functions or [],
                include_configured=False,
            )
            if note:
                call.limitation = note
            return call
        if method == "POST" and path == "/v1/lifecycle/devices/vfs":
            return set_num_vfs(self.config, _vf_cnt(payload), dry_run=self.dry_run)
        if method == "GET" and path == "/v1/lifecycle/heartbeat/ping":
            return ping(payload, self.config, probe=self.ping_probe)
        if method == "POST" and path == "/v1/lifecycle/init":
            return init_xpu(self.config, payload)
        parts = [part for part in path.split("/") if part]
        if (
            method == "GET"
            and len(parts) == 5
            and parts[0] == "v1"
            and parts[1] == "inventory"
            and parts[3] == "inventory"
        ):
            return get_inventory(lspci_text=_lspci_nn(), name="/".join(parts[1:]))
        return OpiCall(
            service="opi",
            http_status=404,
            body={"code": "NOT_FOUND", "message": "%s %s" % (method, path)},
            executed=False,
            dry_run=self.dry_run,
        )


def make_handler(gateway: OpiGateway):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _dispatch(self) -> None:
            parsed = urlparse(self.path)
            try:
                payload = _json_body(self) if self.command == "POST" else {}
            except (ValueError, json.JSONDecodeError) as exc:
                _send(
                    self,
                    OpiCall(
                        service="opi",
                        http_status=400,
                        body={"code": "INVALID_ARGUMENT", "message": str(exc)},
                        executed=False,
                        dry_run=gateway.dry_run,
                    ),
                )
                return
            _send(self, gateway.handle(self.command, parsed.path, payload))

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

    return Handler


def serve(config: Optional[dict], host: str, port: int, dry_run: bool) -> ThreadingHTTPServer:
    gateway = OpiGateway(config, dry_run=dry_run)
    server = ThreadingHTTPServer((host, port), make_handler(gateway))
    return server


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="ODSE OPI HTTP gateway")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    config = load_vm2vm_config(args.config) if args.config else {}
    dry_run = True
    if args.apply and not args.dry_run and not config_dry_run(config):
        dry_run = False
    server = serve(config, args.host, args.port, dry_run)
    print(
        "opi gateway http://%s:%s dry_run=%s" % (args.host, server.server_address[1], dry_run),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
