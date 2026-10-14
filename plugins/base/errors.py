"""Plugin errors. Prefer OperationResult for unimplemented stubs.

Raise only when a caller passes invalid arguments or a future
implementation maps a real tool failure. Do not leak vendor SDK
exceptions.
"""

from __future__ import annotations


class PluginError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class UnknownBackend(PluginError):
    def __init__(self, backend_id: str) -> None:
        super().__init__(f"unknown vendor backend {backend_id!r}", retryable=False)
        self.backend_id = backend_id


class PrerequisiteError(PluginError):
    """Host/DPU missing a required tool or sysfs node (when checks exist)."""


class DiscoveryError(PluginError):
    """Discovery failed unexpectedly (not merely empty inventory)."""


class UnsupportedOperation(PluginError):
    """Capability absent or not implemented — optional vs returning status."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)
