"""AMD Pensando discovery-first plugin (scaffold).

Mirrors plugins/amd_pensando/backend.py's documented capability gap:
representor/forwarding control on Pensando DSC is not yet established
in this repository as a host-visible devlink/sysfs construct, so core
discovery is expected to report `no_representor_reason` for Pensando
VFs today. This plugin exists as the designated extension point once a
concrete discovery mechanism (PSM/DSC API, or a host-visible construct)
is confirmed, rather than guessing at naming conventions now.

TODO (tracked, not yet implemented):
- Identify and document the actual Pensando mechanism for VF ->
  representor identity (PSM API, DSC CLI, or kernel construct) before
  writing any matching logic here.
- Once known, set mapping["representor_netdev"] /
  mapping["mapping_method"] = "vendor_plugin:amd_pensando" only on a
  confirmed match; otherwise leave core's diagnostic fields untouched.

Until implemented, this plugin is a documented no-op.
"""

from __future__ import annotations

from typing import Any, Dict

from vendor_plugins import VendorPlugin


class AmdPensandoPlugin(VendorPlugin):
    name = "amd_pensando"

    def annotate_mapping(self, mapping: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: implement once a discovery mechanism is confirmed (see docstring).
        return mapping

    def extra_sections(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "not_implemented", "note": "see module docstring"}
