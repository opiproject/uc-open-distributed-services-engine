#!/usr/bin/env python3
"""forwarding_intent.py — load, validate, and print a ForwardingIntent.

Standalone helper around plugins.base.vm2vm_config. Does not talk to any
vendor backend or run any command; it only parses the forwarding: section
of a VM2VM lab config (config/lab-vm2vm-single-dpu.schema.yaml) and shows
the normalized ForwardingIntent that tools/labctl.py would build and pass
to VendorBackend.configure_forwarding().

Usage:
    python3 tools/forwarding_intent.py --config config/lab-nvidia-vm2vm.example.yaml --pretty
    python3 tools/forwarding_intent.py --config config/lab-nvidia-vm2vm.example.yaml --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import (  # noqa: E402
    build_forwarding_intent,
    load_vm2vm_config,
    validate_forwarding_section,
    validate_vm2vm_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forwarding_intent",
        description="Load/validate/print the forwarding: section of a VM2VM lab config.",
    )
    parser.add_argument("--config", required=True, help="Path to a VM2VM lab YAML file.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON (implies --json).")
    return parser


def _envelope(config_path: str, config: Optional[Dict[str, Any]], issues: List[str]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "cli": "odse-forwarding-intent/v1alpha1",
        "config": config_path,
        "valid": not issues,
        "issues": issues,
    }
    if not issues and config is not None:
        intent = build_forwarding_intent(config)
        body["intent"] = asdict(intent)
    return body


def _print_text(envelope: Dict[str, Any]) -> None:
    print("config: %s" % envelope["config"])
    print("valid: %s" % envelope["valid"])
    if envelope["issues"]:
        print("issues:")
        for issue in envelope["issues"]:
            print("  - %s" % issue)
        return
    intent = envelope["intent"]
    print("name: %s" % intent["name"])
    print("type: %s" % intent["type"])
    print("mode: %s" % intent["mode"])
    print("bridge: %s" % intent["bridge"])
    print("offload.requested: %s" % intent["offload"]["requested"])
    print("endpoints:")
    for ep in intent["endpoints"]:
        print(
            "  - %s (vm=%s vf_index=%s representor=%s)"
            % (ep["name"], ep["vm"], ep["vf_index"], ep["representor"])
        )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_vm2vm_config(args.config)
    except (OSError, ValueError) as exc:
        print("error: failed to load %s: %s" % (args.config, exc), file=sys.stderr)
        return 1

    issues = validate_vm2vm_config(config)
    issues += validate_forwarding_section(config)

    envelope = _envelope(args.config, config, issues)
    if args.json or args.pretty:
        print(json.dumps(envelope, indent=2 if args.pretty else None, sort_keys=True))
    else:
        _print_text(envelope)
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
