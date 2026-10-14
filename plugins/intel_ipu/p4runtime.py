"""Intel IPU P4Runtime / IPDK client skeleton.

This is a **separate, independent** control path from opi.py and
dpu_operator.py -- P4Runtime (via IPDK's `infrap4d` daemon) is the
lower-level, vendor-native way to program an Intel IPU/E2100-class
pipeline and predates the OpenShift DPU Operator. Do not treat this as
a fallback of OPI/DPU Operator, and do not treat OPI/DPU Operator as a
prerequisite for it -- they are alternate control planes over the same
hardware.

Three modes:
  - "mock" returns configured stand-ins. No network, no SSH.
  - "grpc" is a placeholder and does not send a P4Runtime request (no
    grpcio/p4runtime protobuf dependency is assumed or pinned here).
  - "cli" shells the real `p4rt-ctl` utility over SSH to the ACC (Arm
    Compute Complex), the same mechanism
    https://github.com/ipdk-io/ipu-playbook's `ovs_offload_lnw.py` uses
    for real Intel IPU E2100 hardware. Only the subcommands confirmed
    by https://ipdk.io/p4cp-userguide/clients/p4rt-ctl.html are
    implemented (`set-pipe` is not wired yet, `get-pipe`, `add-entry`,
    `dump-entries` are); `del-entry` syntax is not confirmed there and
    is deliberately left not_implemented rather than guessed.

See docs/vendor-readiness/intel-ipu-p4-ipdk.md before enabling "cli" or
"grpc" for a real lab.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from ..base.models import (
    STATUS_ERROR,
    STATUS_NOT_IMPLEMENTED,
    STATUS_OK,
    STATUS_SKIPPED,
    CommandResult,
    OperationResult,
    TelemetrySample,
    VendorCapability,
)

LOG = logging.getLogger(__name__)
VENDOR = "intel"
MODE_MOCK = "mock"
MODE_GRPC = "grpc"
MODE_CLI = "cli"
_MODES = (MODE_MOCK, MODE_GRPC, MODE_CLI)

# p4rt-ctl subcommands confirmed against
# https://ipdk.io/p4cp-userguide/clients/p4rt-ctl.html and
# https://ipdk.io/p4cp-userguide/guides/es2k/running-infrap4d.html
# (the Intel IPU E2100-specific page). Do not add subcommands here that
# have not been confirmed against real p4rt-ctl documentation -- this
# repo does not guess vendor CLI syntax.
_P4RT_CTL = "p4rt-ctl"
DEFAULT_SWITCH = "br0"


class P4RuntimeClient:
    """Thin P4Runtime/IPDK adapter for Intel IPU.

    Real wire format is P4Runtime (gRPC, proto3, `p4.v1`) served by
    `infrap4d` on the IPU's Arm/ACC complex. This client does not embed
    that protobuf/gRPC stack -- pin `<IPDK_VERSION>` and generate/vendor
    the `p4.v1` stubs before flipping mode to "grpc" for real traffic.
    "cli" mode is the more realistic near-term path (see module
    docstring): it shells `p4rt-ctl` over SSH instead.
    """

    def __init__(
        self,
        mode: str = MODE_MOCK,
        endpoint: Optional[str] = None,
        device_id: Optional[str] = None,
        mock_pipeline_config: Optional[Mapping[str, Any]] = None,
        mock_table_entries: Optional[List[Mapping[str, Any]]] = None,
        *,
        acc_ssh: Optional[Mapping[str, Any]] = None,
        dry_run: bool = True,
    ) -> None:
        self.mode = (mode or MODE_MOCK).lower()
        if self.mode not in _MODES:
            self.mode = MODE_MOCK
        self.endpoint = endpoint
        self.device_id = device_id
        self.mock_pipeline_config = dict(mock_pipeline_config or {})
        self._entries: Dict[str, Dict[str, Any]] = {
            str(row.get("id")): dict(row) for row in (mock_table_entries or []) if row.get("id")
        }
        # Pre-resolved SSH target for the ACC ("cli" mode only). Callers
        # going through the labctl vm2vm lab config should build this
        # with plugins.base.remote.ssh_params(cfg) against the same
        # `dpu:` block NVIDIA's DPU-mode SSH already uses -- the ACC is
        # Intel's analog of BlueField's Arm side, so it reuses the same
        # shape rather than inventing a parallel one.
        self.acc_ssh = dict(acc_ssh or {})
        # dry_run mirrors remote_execution.dry_run_default (see
        # plugins.base.remote.ssh_params / docs/operations/
        # dpu-remote-execution.md) -- "cli" mode never sends a real
        # command unless a caller explicitly resolved this to False.
        self.dry_run = bool(dry_run)

    @property
    def has_mock_entries(self) -> bool:
        return bool(self._entries)

    @classmethod
    def from_config(cls, config: Optional[Mapping[str, Any]] = None) -> "P4RuntimeClient":
        """Read the flat discovery-style config (same shape as
        opi.OpiClient.from_config / discovery.load_config), not the
        nested lab-intel-vm2vm.yaml `p4:` block -- this is for the
        `labctl intel-nf` path. For the vm2vm lab config, use
        from_lab_config() instead.
        """
        cfg = dict(config or {})
        return cls(
            mode=str(cfg.get("p4_mode") or MODE_MOCK),
            endpoint=cfg.get("p4_endpoint"),
            device_id=cfg.get("p4_device_id"),
            mock_pipeline_config=cfg.get("mock_pipeline_config") or {},
            mock_table_entries=list(cfg.get("mock_table_entries") or []),
        )

    @classmethod
    def from_lab_config(cls, config: Optional[Mapping[str, Any]] = None) -> "P4RuntimeClient":
        """Read the nested lab-intel-vm2vm.yaml shape: the `p4:` block
        for mode/endpoint/device_id, the shared `dpu:` block for ACC SSH
        (same fields NVIDIA's DPU mode uses), and the shared
        `remote_execution:` block for the dry-run gate. Used by
        vm_to_vm_capability.py and IntelIpuBackend.
        """
        from ..base.remote import ssh_params

        cfg = dict(config or {})
        p4_cfg = cfg.get("p4") if isinstance(cfg.get("p4"), dict) else {}
        remote = cfg.get("remote_execution") if isinstance(cfg.get("remote_execution"), dict) else {}
        return cls(
            mode=str(p4_cfg.get("mode") or MODE_MOCK),
            endpoint=p4_cfg.get("endpoint"),
            device_id=p4_cfg.get("device_id"),
            mock_pipeline_config=p4_cfg.get("mock_pipeline_config") or {},
            mock_table_entries=list(p4_cfg.get("mock_table_entries") or []),
            acc_ssh=ssh_params(cfg),
            dry_run=bool(remote.get("dry_run_default", True)),
        )

    # -- P4Runtime-shaped operations -------------------------------------

    def get_forwarding_pipeline_config(self) -> OperationResult:
        """Analogous to P4Runtime's GetForwardingPipelineConfigRequest."""
        if self.mode == MODE_GRPC:
            return self._grpc_placeholder("get_forwarding_pipeline_config")
        if self.mode == MODE_CLI:
            return self._cli_get_pipe()
        if not self.mock_pipeline_config:
            return OperationResult(
                operation="p4_get_forwarding_pipeline_config",
                vendor=VENDOR,
                status=STATUS_SKIPPED,
                message="mock: no pipeline configured (mock_pipeline_config empty)",
                executed=True,
                capabilities=VendorCapability(vendor=VENDOR, p4_native=None),
                details={"mode": MODE_MOCK, "device_id": self.device_id, "pipeline_configured": False},
            )
        LOG.info(
            "intel P4Runtime mock get_forwarding_pipeline_config device_id=%s", self.device_id
        )
        return OperationResult(
            operation="p4_get_forwarding_pipeline_config",
            vendor=VENDOR,
            status=STATUS_OK,
            message="mock pipeline config; not read from a real infrap4d instance",
            executed=True,
            capabilities=VendorCapability(vendor=VENDOR, p4_native=True),
            details={
                "mode": MODE_MOCK,
                "device_id": self.device_id,
                "pipeline_configured": True,
                "p4info_summary": self.mock_pipeline_config.get("p4info_summary"),
                "cookie": self.mock_pipeline_config.get("cookie"),
            },
        )

    def write_table_entry(self, entry: Mapping[str, Any]) -> OperationResult:
        """Analogous to a P4Runtime WriteRequest with one TableEntry insert."""
        if self.mode == MODE_GRPC:
            return self._grpc_placeholder("write_table_entry")
        if self.mode == MODE_CLI:
            return self._cli_add_entry(entry)
        payload = dict(entry)
        entry_id = payload.get("id")
        if not entry_id:
            return OperationResult(
                operation="p4_write_table_entry",
                vendor=VENDOR,
                status=STATUS_ERROR,
                message="table entry needs an id",
                executed=False,
            )
        self._entries[str(entry_id)] = payload
        LOG.info("intel P4Runtime mock write_table_entry id=%s (not sent to infrap4d)", entry_id)
        return OperationResult(
            operation="p4_write_table_entry",
            vendor=VENDOR,
            status=STATUS_OK,
            message="mock write %s; no P4Runtime RPC issued" % entry_id,
            executed=True,
            destructive=False,
            details={"mode": MODE_MOCK, "entry": payload},
        )

    def delete_table_entry(self, entry_id: str) -> OperationResult:
        if self.mode == MODE_GRPC:
            return self._grpc_placeholder("delete_table_entry")
        if self.mode == MODE_CLI:
            return self._cli_del_entry(entry_id)
        existed = entry_id in self._entries
        self._entries.pop(entry_id, None)
        return OperationResult(
            operation="p4_delete_table_entry",
            vendor=VENDOR,
            status=STATUS_OK if existed else STATUS_SKIPPED,
            message=("mock deleted %s" % entry_id) if existed else "mock: entry %r not present" % entry_id,
            executed=True,
            destructive=True,
            details={"mode": MODE_MOCK, "entry_id": entry_id, "existed": existed},
        )

    def read_counters(self, entry_id: str) -> OperationResult:
        """Analogous to a P4Runtime ReadRequest for a table entry's counters."""
        if self.mode == MODE_GRPC:
            return self._grpc_placeholder("read_counters")
        if self.mode == MODE_CLI:
            return self._cli_dump_entries(entry_id)
        LOG.info("intel P4Runtime mock read_counters: omit missing counters for %s", entry_id)
        samples: List[TelemetrySample] = []
        row = self._entries.get(entry_id)
        if row and row.get("mock_counter_packets") is not None:
            samples.append(
                TelemetrySample(
                    source_object_id=entry_id,
                    source_kind="p4_table_entry",
                    metric_name="packets",
                    value=float(row["mock_counter_packets"]),
                    unit="packets",
                )
            )
        return OperationResult(
            operation="p4_read_counters",
            vendor=VENDOR,
            status=STATUS_OK,
            message="mock counters (no fake zeros for unset entries)",
            executed=True,
            telemetry=samples,
            details={"mode": MODE_MOCK, "entry_id": entry_id},
        )

    def _grpc_placeholder(self, operation: str) -> OperationResult:
        LOG.warning("intel P4Runtime gRPC mode placeholder: no RPC sent to %s", self.endpoint)
        return OperationResult(
            operation="p4_%s" % operation,
            vendor=VENDOR,
            status=STATUS_NOT_IMPLEMENTED,
            message=(
                "gRPC P4Runtime client is a placeholder; no RPC sent to %s. "
                "Requires a pinned p4.v1 protobuf/grpcio stub and a compiled "
                "p4info for this IPU's pipeline before enabling -- see "
                "docs/vendor-readiness/intel-ipu-p4-ipdk.md. Consider mode="
                "\"cli\" (p4rt-ctl over SSH) instead."
                % (self.endpoint or "<p4_endpoint>")
            ),
            executed=False,
            details={
                "mode": MODE_GRPC,
                "endpoint": self.endpoint,
                "device_id": self.device_id,
                "grpc_attempted": False,
            },
        )

    # -- "cli" mode: shell p4rt-ctl over SSH to the ACC -------------------

    def _run_cli(self, argv: List[str], *, reason: str) -> CommandResult:
        from ..base.remote import RemoteCommandRunner

        host = self.acc_ssh.get("host")
        user = self.acc_ssh.get("user")
        if not host or not user:
            return CommandResult(
                argv=argv,
                executed=False,
                dry_run=True,
                reason=(
                    "p4 cli mode has no ACC SSH target configured "
                    "(dpu.management_ip/dpu.ssh_user); see "
                    "config/lab-intel-vm2vm.example.yaml"
                ),
            )
        runner = RemoteCommandRunner()
        return runner.run_ssh(
            host,
            user,
            argv,
            key_path=self.acc_ssh.get("key_path"),
            port=int(self.acc_ssh.get("port") or 22),
            dry_run=self.dry_run,
            timeout=self.acc_ssh.get("command_timeout_seconds") or 30.0,
            sudo=bool(self.acc_ssh.get("sudo")),
            reason=reason,
        )

    def _cli_get_pipe(self) -> OperationResult:
        switch = self.device_id or DEFAULT_SWITCH
        argv = [_P4RT_CTL, "get-pipe", switch]
        cmd = self._run_cli(argv, reason="p4rt-ctl get-pipe (pipeline capability probe)")
        if not cmd.executed:
            return OperationResult(
                operation="p4_get_forwarding_pipeline_config",
                vendor=VENDOR,
                status=STATUS_SKIPPED,
                message=cmd.reason or "p4rt-ctl get-pipe not executed (planned only, or ACC SSH not configured)",
                executed=False,
                commands=[cmd],
                details={"mode": MODE_CLI, "switch": switch, "pipeline_configured": None},
            )
        configured = cmd.returncode == 0 and bool((cmd.stdout or "").strip())
        return OperationResult(
            operation="p4_get_forwarding_pipeline_config",
            vendor=VENDOR,
            status=STATUS_OK if configured else STATUS_SKIPPED,
            message="p4rt-ctl get-pipe %s: %s" % (switch, "pipeline present" if configured else "no pipeline / empty output"),
            executed=True,
            capabilities=VendorCapability(vendor=VENDOR, p4_native=True if configured else None),
            commands=[cmd],
            details={"mode": MODE_CLI, "switch": switch, "pipeline_configured": configured, "raw": cmd.stdout},
        )

    def _cli_add_entry(self, entry: Mapping[str, Any]) -> OperationResult:
        """entry needs: id (ours, for bookkeeping), table, flow -- flow is
        passed through verbatim as p4rt-ctl's FLOW argument, e.g.
        "hdr.ipv4.dst_addr=1.1.1.1,action=ingress.send(0)". This client
        does not build FLOW strings from a higher-level DSL; the P4
        program's exact match/action names are pipeline-specific and
        discovery-required (see docs/vendor-readiness/intel-ipu-p4-ipdk.md).
        """
        payload = dict(entry)
        entry_id = payload.get("id")
        table = payload.get("table")
        flow = payload.get("flow")
        switch = payload.get("switch") or self.device_id or DEFAULT_SWITCH
        if not entry_id or not table or not flow:
            return OperationResult(
                operation="p4_write_table_entry",
                vendor=VENDOR,
                status=STATUS_ERROR,
                message=(
                    "cli mode needs entry id, table, and a p4rt-ctl FLOW string "
                    "(id=%r table=%r flow=%r)" % (entry_id, table, flow)
                ),
                executed=False,
            )
        argv = [_P4RT_CTL, "add-entry", switch, str(table), str(flow)]
        cmd = self._run_cli(argv, reason="p4rt-ctl add-entry %s" % entry_id)
        if not cmd.executed:
            status = STATUS_SKIPPED
        elif cmd.returncode == 0:
            status = STATUS_OK
        else:
            status = STATUS_ERROR
        return OperationResult(
            operation="p4_write_table_entry",
            vendor=VENDOR,
            status=status,
            message=cmd.reason or "p4rt-ctl add-entry %s %s" % (switch, table),
            executed=cmd.executed,
            destructive=True,
            commands=[cmd],
            details={"mode": MODE_CLI, "entry_id": entry_id, "switch": switch, "table": table, "flow": flow},
        )

    def _cli_del_entry(self, entry_id: str) -> OperationResult:
        # https://ipdk.io/p4cp-userguide/clients/p4rt-ctl.html documents
        # set-pipe/get-pipe/add-entry/dump-entries; a del-entry subcommand
        # is not confirmed there for p4rt-ctl specifically (only for the
        # separate, similarly-named ovs-p4ctl tool). Do not guess it.
        return OperationResult(
            operation="p4_delete_table_entry",
            vendor=VENDOR,
            status=STATUS_NOT_IMPLEMENTED,
            message=(
                "p4rt-ctl del-entry syntax is not confirmed in this repo yet "
                "(entry_id=%s); see docs/vendor-readiness/intel-ipu-p4-ipdk.md "
                "before adding one." % entry_id
            ),
            executed=False,
            details={"mode": MODE_CLI, "entry_id": entry_id},
        )

    def _cli_dump_entries(self, entry_id: str) -> OperationResult:
        switch = self.device_id or DEFAULT_SWITCH
        argv = [_P4RT_CTL, "dump-entries", switch]
        cmd = self._run_cli(argv, reason="p4rt-ctl dump-entries (counters for %s)" % entry_id)
        return OperationResult(
            operation="p4_read_counters",
            vendor=VENDOR,
            status=STATUS_OK if cmd.executed else STATUS_SKIPPED,
            message=(
                "raw p4rt-ctl dump-entries output; not parsed into "
                "TelemetrySample (output format is not confirmed/pinned here)"
            ),
            executed=cmd.executed,
            telemetry=[],
            commands=[cmd],
            details={"mode": MODE_CLI, "entry_id": entry_id, "switch": switch, "raw": cmd.stdout},
        )


def plan_pipeline_check() -> OperationResult:
    LOG.info("intel_ipu.p4runtime: pipeline capability not probed")
    return OperationResult(
        operation="p4_pipeline_check",
        vendor=VENDOR,
        status=STATUS_NOT_IMPLEMENTED,
        message="TODO: connect to infrap4d P4Runtime service and confirm a loaded pipeline",
        executed=False,
        capabilities=VendorCapability(vendor=VENDOR, p4_native=None),
        details={"endpoint": None},
    )


def probe_pipeline_capability(
    config: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Best-effort, honest capability probe for the flat discovery-style
    config (labctl intel-nf path). For the vm2vm lab config, see
    vm_to_vm_capability.py's live-probe wiring, which uses
    P4RuntimeClient.from_lab_config() + this same honesty rule: only a
    real, executed, positive result ever returns True.
    """
    client = P4RuntimeClient.from_config(config)
    if client.mode == MODE_GRPC:
        return False
    if client.mode == MODE_CLI:
        result = client.get_forwarding_pipeline_config()
        return bool(result.executed and (result.details or {}).get("pipeline_configured"))
    result = client.get_forwarding_pipeline_config()
    return bool((result.details or {}).get("pipeline_configured"))
