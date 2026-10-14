"""Intel IPU OPI-mapping discovery plugin (scaffold).

Intel's IPU control plane (OPI / IMC) is typically reached over a
separate management path (Redfish/gRPC to the IMC) rather than through
host-visible sysfs/devlink. This mirrors the `discovery_required=True`
posture already used by plugins/intel_ipu/backend.py's
configure_forwarding(): core VF discovery on the host can enumerate
VFs, but resolving representor identity usually requires an OPI query.

TODO (tracked, not yet implemented):
- Accept opi_endpoint/redfish_endpoint (e.g. via --plugin-dir-config or
  environment variables — TBD) and query OPI for the VF -> port mapping.
- Set mapping["representor_netdev"] / mapping["mapping_method"] =
  "vendor_plugin:intel_opi" only when the OPI query succeeds and
  identifies a specific port for this vf_pci_address.
- Never fabricate a representor name from convention; if OPI is
  unreachable, leave core's no_representor_reason and hints intact and
  add a plugin-specific hint under extra_sections().

Until implemented, this plugin is a documented no-op.
"""

from __future__ import annotations

from typing import Any, Dict

from vendor_plugins import VendorPlugin


class IntelIpuPlugin(VendorPlugin):
    name = "intel_ipu"

    def annotate_mapping(self, mapping: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: query OPI for VF -> port mapping when an endpoint is configured.
        return mapping

    def extra_sections(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "not_implemented", "note": "see module docstring"}
