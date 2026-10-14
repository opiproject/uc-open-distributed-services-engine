"""Vendor telemetry plugins (placeholders).

Core collector is vendor-neutral. Plugins add keys under
document['vendor_extension'][<name>]. They must not raise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List


class VendorTelemetryPlugin:
    name = "unnamed"

    def collect(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Return a JSON-serialisable object for vendor_extension[name]."""
        return {"implemented": False, "note": "placeholder"}


def load_plugins(plugin_dirs: List[Path]) -> List[VendorTelemetryPlugin]:
    loaded: List[VendorTelemetryPlugin] = []
    seen = set()
    for plugin_dir in plugin_dirs:
        if not plugin_dir.is_dir():
            continue
        for path in sorted(plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"odse_tel_plugin_{path.stem}_{abs(hash(str(path)))}"
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
                    and issubclass(obj, VendorTelemetryPlugin)
                    and obj is not VendorTelemetryPlugin
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
