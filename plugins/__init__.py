"""ODSE vendor enablement plugins.

Common contract: plugins.base.VendorBackend. Implementations do not run
destructive commands in this skeleton; they return OperationResult.

    from plugins import load_backend
    backend = load_backend("nvidia")
    result = backend.discover()

Distinct from:
  - kubernetes/opi/adapter (OPI object API)
  - tools/*/plugins (checker/telemetry/topology hooks)
"""

from __future__ import annotations

from typing import Any, Dict, Type

from .base.backend import VendorBackend
from .base.errors import UnknownBackend
from .amd_pensando import AmdPensandoBackend
from .intel_ipu import IntelIpuBackend
from .marvell_octeon import MarvellOcteonBackend
from .nvidia_bluefield import NvidiaBlueFieldBackend

BACKENDS: Dict[str, Type[VendorBackend]] = {
    "nvidia": NvidiaBlueFieldBackend,
    "intel": IntelIpuBackend,
    "marvell": MarvellOcteonBackend,
    "amd": AmdPensandoBackend,
}


def load_backend(backend_id: str, **kwargs: Any) -> VendorBackend:
    """Instantiate a vendor plugin. Unknown ids raise UnknownBackend."""
    key = (backend_id or "").strip().lower()
    aliases = {
        "nvidia_bluefield": "nvidia",
        "nvidia-bluefield": "nvidia",
        "bluefield": "nvidia",
        "intel_ipu": "intel",
        "intel-ipu": "intel",
        "marvell_octeon": "marvell",
        "marvell-octeon": "marvell",
        "octeon": "marvell",
        "amd_pensando": "amd",
        "amd-pensando": "amd",
        "pensando": "amd",
    }
    key = aliases.get(key, key)
    cls = BACKENDS.get(key)
    if cls is None:
        raise UnknownBackend(backend_id)
    if key == "intel":
        return cls(config=kwargs.get("config") or kwargs.get("lab_config"))
    if key == "marvell":
        return cls(
            configured_dao_app=kwargs.get("configured_dao_app"),
            lab_config=kwargs.get("lab_config"),
        )
    if key == "nvidia":
        return cls(lab_config=kwargs.get("lab_config"))
    if key == "amd":
        return cls(lab_config=kwargs.get("lab_config"))
    return cls()


__all__ = ["BACKENDS", "VendorBackend", "load_backend"]
