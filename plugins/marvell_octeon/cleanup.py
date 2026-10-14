"""Marvell DAO/DPDK cleanup planner. Dry-run default. No untracked unbind."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from ..base.models import STATUS_OK, STATUS_SKIPPED, CommandResult, OperationResult

LOG = logging.getLogger(__name__)
VENDOR = "marvell"
UNTRACKED_DPDK_BINDING_REFUSED = "UNTRACKED_DPDK_BINDING_REFUSED"


def plan_dao_cleanup(
    config: Optional[Dict[str, Any]] = None,
    *,
    run_state: Optional[Mapping[str, Any]] = None,
    apply: bool = False,
    dry_run: bool = True,
) -> OperationResult:
    cfg = config or {}
    state = dict(run_state or {})
    dao_cfg = cfg.get("dao") if isinstance(cfg.get("dao"), dict) else {}
    stop_dao = bool(dao_cfg.get("stop_on_cleanup"))
    lab_owned_bridge = bool((cfg.get("ovs") or {}).get("lab_owned")) or bool(
        (state.get("prepare-ovs") or state.get("prepare_ovs") or {}).get("lab_owned")
    )
    tracked_bindings = list(
        (state.get("dpdk_bindings") or state.get("lab_owned_dpdk_bindings") or [])
    )
    if not tracked_bindings:
        prior = state.get("discover-representors") or state.get("discover_representors") or {}
        tracked_bindings = list((prior.get("lab_owned_dpdk_bindings") or []))

    commands: List[CommandResult] = []
    refused: List[str] = []
    planned: List[str] = ["stop traffic (lab-owned guests only)"]
    if stop_dao:
        planned.append("stop DAO process if this run started it")
        commands.append(
            CommandResult(
                argv=["<DAO_OVS_APP>", "stop"],
                dry_run=True,
                executed=False,
                reason="marvell cleanup: DAO stop placeholder",
            )
        )
    else:
        planned.append("leave DAO process running (dao.stop_on_cleanup is not true)")

    if lab_owned_bridge:
        bridge = (cfg.get("ovs") or {}).get("bridge") or "br-int"
        planned.append("remove lab-created DAO/OVS logical ports on %s" % bridge)
        commands.append(
            CommandResult(
                argv=["<DAO_OVS_APP>", "del-br", bridge],
                dry_run=True,
                executed=False,
                reason="marvell cleanup: lab-owned bridge only",
            )
        )
    else:
        planned.append("skip bridge delete (not lab_owned)")

    if tracked_bindings:
        for pci in tracked_bindings:
            planned.append("restore lab-owned DPDK binding %s" % pci)
            commands.append(
                CommandResult(
                    argv=["dpdk-devbind.py", "--status", str(pci)],
                    dry_run=True,
                    executed=False,
                    reason="marvell cleanup: tracked binding only (no unbind unless apply+owned)",
                )
            )
    else:
        refused.append(UNTRACKED_DPDK_BINDING_REFUSED)
        planned.append("refuse to reset DPDK bindings not marked lab_owned")

    executed = False
    if apply and not dry_run:
        LOG.warning("marvell cleanup apply requested but real DAO argv is not frozen")
    LOG.info("marvell cleanup plan refused=%s", refused)
    return OperationResult(
        operation="plan_dao_cleanup",
        vendor=VENDOR,
        status=STATUS_SKIPPED if refused and not tracked_bindings else STATUS_OK,
        message=(
            UNTRACKED_DPDK_BINDING_REFUSED
            if UNTRACKED_DPDK_BINDING_REFUSED in refused
            else "DAO cleanup planned (dry-run); no untracked DPDK unbind"
        ),
        destructive=True,
        executed=executed,
        commands=commands,
        details={
            "planned": planned,
            "refused": refused,
            "lab_owned_bridge": lab_owned_bridge,
            "tracked_bindings": tracked_bindings,
            "stop_dao": stop_dao,
            "dry_run": dry_run,
            "apply": apply,
        },
    )
