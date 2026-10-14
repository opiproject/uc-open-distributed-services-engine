"""Vendor plugin hooks for the OVS offload checker.

Core checker logic is vendor-neutral. Vendor-specific extra commands,
counter names, or flow annotations belong in a plugin that subclasses
`VendorPlugin` and lives in this directory (or another directory passed
via `--plugin-dir`).

A plugin module must define one or more `VendorPlugin` subclasses. The
loader instantiates each concrete subclass with no constructor arguments.

Plugins must never raise: wrap command failures and return them as data
so a missing vendor tool cannot abort the rest of the report.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Type


class VendorPlugin:
    """Base class for optional vendor-specific report extensions."""

    name = "unnamed"

    def extra_sections(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Return additional JSON sections to merge under report['plugins'][name].

        `context` includes at least:
          - interfaces: list of interface names the user asked about
          - report: the core report dict built so far (read-only by convention)
        """
        return {}

    def annotate_flow(self, flow: Dict[str, Any]) -> Dict[str, Any]:
        """Optionally add vendor fields to a parsed datapath flow dict.

        Must return the (possibly mutated) flow dict. Default is a no-op.
        """
        return flow


def load_plugins(plugin_dirs: List[Path]) -> List[VendorPlugin]:
    """Import concrete VendorPlugin subclasses from the given directories."""
    loaded: List[VendorPlugin] = []
    seen_names = set()

    for plugin_dir in plugin_dirs:
        if not plugin_dir.is_dir():
            continue
        for path in sorted(plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"odse_offload_plugin_{path.stem}_{abs(hash(str(path)))}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                continue
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if not _is_concrete_plugin(obj):
                    continue
                try:
                    instance = obj()
                except Exception:
                    continue
                plugin_name = getattr(instance, "name", obj.__name__)
                if plugin_name in seen_names:
                    continue
                seen_names.add(plugin_name)
                loaded.append(instance)

    return loaded


def _is_concrete_plugin(obj: Any) -> bool:
    return (
        isinstance(obj, type)
        and issubclass(obj, VendorPlugin)
        and obj is not VendorPlugin
    )


def plugin_dir_default() -> Path:
    return Path(__file__).resolve().parent
