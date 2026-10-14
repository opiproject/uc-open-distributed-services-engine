"""NVIDIA BlueField subfunction (SF) annotation plugin (scaffold).

Core discovery in vf_to_representor_map.py already parses `pf0sf0`-style
phys_port_name into kind="sf" with a sf_index — but core never emits SF
rows into `mappings` (mappings are VF-indexed, and an SF is not a VF).
This plugin is where SF-specific enrichment belongs: annotating a VF row
with its co-located SF representors (when the lab topology uses SFs
alongside VFs on the same PF), or emitting an `extra_sections` block
listing SFs discovered via `context["raw"]["ip_phys"]`.

TODO (tracked, not yet implemented):
- Walk context["raw"]["ip_phys"] for phys_port_name kind == "sf" and
  surface them under extra_sections()["nvidia_sf"]["subfunctions"].
- Cross-reference SF netdevs with `devlink port show` flavour "pcisf"
  (not handled by core match_representor, which only matches "pcivf").
- Optionally resolve mlx5 SF auxiliary-bus device names
  (/sys/bus/auxiliary/devices/mlx5_core.sf.N) when run on the DPU OS.

Until implemented, this plugin is a documented no-op: it loads (so
`--no-bundled-plugins` behavior can be tested against it) but does not
alter mappings or add sections.
"""

from __future__ import annotations

from typing import Any, Dict

# Loaded dynamically via importlib (see vendor_plugins/__init__.py:load_plugins),
# not as part of the `vendor_plugins` package itself, so this must be an
# absolute import resolved through the `vendor_plugins/` directory
# `_load_plugins()` adds to sys.path — a relative `from . import
# VendorPlugin` fails silently here (caught by load_plugins' try/except,
# plugin just never loads).
from vendor_plugins import VendorPlugin


class NvidiaSubfunctionPlugin(VendorPlugin):
    name = "nvidia_sf"

    def annotate_mapping(self, mapping: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: annotate VF rows with sibling SF representor info when
        # both share a PF and eswitch. No-op until implemented.
        return mapping

    def extra_sections(self, context: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: list SFs discovered via context["raw"]["ip_phys"].
        return {"status": "not_implemented", "note": "see module docstring"}
