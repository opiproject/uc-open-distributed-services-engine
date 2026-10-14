#!/usr/bin/env python3
"""remote_exec.py — plan DPU SSH commands (dry-run default).

Usage:
    python tools/remote_exec.py --config config/lab-nvidia-vm2vm.example.yaml \\
        --command ovs-vsctl -- show --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.remote import RemoteCommandRunner, ssh_params  # noqa: E402
from plugins.base.vm2vm_config import load_vm2vm_config  # noqa: E402

LOG = logging.getLogger("remote_exec")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan/run a DPU-side command. Default dry-run.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--command", required=True, nargs="+")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    config = load_vm2vm_config(args.config)
    dry = True if args.dry_run or not args.apply else False
    params = ssh_params(config)
    if not params.get("host") or not params.get("user"):
        LOG.error("dpu.management_ip and dpu.ssh_user required")
        return 1
    vendor = ((config.get("vendor") or {}).get("type") or "unknown")
    out_dir = ROOT / "tests" / "results" / str(vendor) / "remote"
    runner = RemoteCommandRunner(output_dir=out_dir)
    result = runner.run_ssh(
        params["host"],
        params["user"],
        list(args.command),
        key_path=params.get("key_path"),
        port=params["port"],
        dry_run=dry,
        sudo=params.get("sudo") or False,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print("executed=%s dry_run=%s argv=%s" % (result.executed, result.dry_run, result.argv))
    return 0 if result.returncode in (None, 0) or dry else 1


if __name__ == "__main__":
    sys.exit(main())
