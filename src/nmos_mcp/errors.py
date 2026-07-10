"""Typed errors that map to clean, actionable MCP tool error messages."""

from __future__ import annotations


class NmosError(Exception):
    """Base class for all NMOS MCP errors."""


class RegistryUnavailableError(NmosError):
    """The NMOS registry could not be resolved or reached."""


class ResourceNotFoundError(NmosError):
    """A requested NMOS resource (node/device/sender/receiver/...) does not exist."""


class ConnectionEndpointError(NmosError):
    """A device does not expose a usable IS-05 connection (sr-ctrl) control href."""


class ConnectionManagementError(NmosError):
    """An IS-05 staged PATCH / activation failed."""


class AuthError(NmosError):
    """IS-10 token acquisition failed."""
