"""Vendor plugin hooks for VF-to-representor mapping.

Core mapping is vendor-neutral (sysfs, ip, devlink, ethtool). Vendor
quirks — SF representors, non-standard phys_port_name, DPU serial
identity — belong in a plugin subclassing `VendorPlugin`.

Plugins must not raise. Treat missing vendor tools as data.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List


class VendorPlugin:
    name = "unnamed"

    def annotate_mapping(self, mapping: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Return the mapping dict, optionally with extra vendor fields."""
        return mapping

    def extra_sections(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """JSON merged under report['plugins'][name]."""
        return {}


def load_plugins(plugin_dirs: List[Path]) -> List[VendorPlugin]:
    loaded: List[VendorPlugin] = []
    seen = set()
    for plugin_dir in plugin_dirs:
        if not plugin_dir.is_dir():
            continue
        for path in sorted(plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"odse_topo_plugin_{path.stem}_{abs(hash(str(path)))}"
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
            for attr in dir(module):
                obj = getattr(module, attr)
                if not (
                    isinstance(obj, type)
                    and issubclass(obj, VendorPlugin)
                    and obj is not VendorPlugin
                ):
                    continue
                try:
                    instance = obj()
                except Exception:
                    continue
                plugin_name = getattr(instance, "name", obj.__name__)
                if plugin_name in seen:
                    continue
                seen.add(plugin_name)
                loaded.append(instance)
    return loaded


def plugin_dir_default() -> Path:
    return Path(__file__).resolve().parent
