"""Marvell OCTEON DPDK-representor discovery plugin (scaffold).

Marvell's OCTEON DPU stack commonly manages representors via DPDK/DAO
rather than exposing them as netdevs the kernel `ip -d link` / `devlink
port show` can see from the host. Core discovery therefore frequently
reports `no_representor_reason` pointing at "no devlink pcivf port and
no phys_port_name" for Marvell VFs even when a representor conceptually
exists in the DAO control plane.

TODO (tracked, not yet implemented):
- If a DAO/DPDK inventory API or CLI is available in the run environment,
  query it for the VF -> representor mapping and set
  mapping["representor_netdev"] / mapping["mapping_method"] =
  "vendor_plugin:marvell_dao" when found, without overwriting a mapping
  core already resolved with higher confidence.
- Clear mapping["no_representor_reason"] only when this plugin actually
  fills representor_netdev, so partial failures stay visible.
- Surface raw DAO/DPDK query output under
  extra_sections()["marvell"]["dao_query"] for troubleshooting.

Until implemented, this plugin is a documented no-op.
"""

from __future__ import annotations

from typing import Any, Dict

from vendor_plugins import VendorPlugin


class MarvellOcteonPlugin(VendorPlugin):
    name = "marvell_octeon"

    def annotate_mapping(self, mapping: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: query DAO/DPDK representor inventory when available.
        return mapping

    def extra_sections(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "not_implemented", "note": "see module docstring"}
