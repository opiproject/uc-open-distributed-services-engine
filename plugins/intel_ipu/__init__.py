"""Intel IPU enablement plugin (OPI/OpenShift-first)."""

from .backend import IntelIpuBackend
from .opi import OpiClient

__all__ = ["IntelIpuBackend", "OpiClient"]
