"""IS-04 Query API client (runs against the Registry).

Handles reading every resource collection and — crucially for IS-05 — resolving a
sender/receiver to the IS-05 Connection API base URL advertised by its owning
device's ``controls`` array.
"""

from __future__ import annotations

from typing import Any

import httpx

from .discovery import RegistryResolver
from .errors import ConnectionEndpointError, RegistryUnavailableError, ResourceNotFoundError
from .models import SR_CTRL_TYPE_PREFIX


class QueryClient:
    def __init__(self, client: httpx.AsyncClient, resolver: RegistryResolver) -> None:
        self._client = client
        self._resolver = resolver

    def _base(self, refresh: bool = False) -> str:
        return self._resolver.resolve(refresh=refresh)

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = self._base().rstrip("/") + "/" + path.lstrip("/")
        try:
            resp = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise RegistryUnavailableError(f"Query API request to {url} failed: {exc}") from exc
        if resp.status_code == 404:
            raise ResourceNotFoundError(f"Not found: {url}")
        resp.raise_for_status()
        return resp.json()

    # --- Collections ----------------------------------------------------------
    async def list_resources(self, kind: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """List a resource collection, passing IS-04 basic-query params through.

        Example filters: ``{"label": "Cam 1"}`` or ``{"query.rql": "eq(label,Cam1)"}``.
        """
        data = await self._get(kind, params=filters or None)
        return data if isinstance(data, list) else [data]

    async def get_resource(self, kind: str, resource_id: str) -> dict[str, Any]:
        return await self._get(f"{kind}/{resource_id}")

    # --- IS-05 endpoint resolution -------------------------------------------
    def _connection_base_from_device(self, device: dict[str, Any]) -> str:
        for control in device.get("controls", []):
            ctype = control.get("type", "")
            if ctype.startswith(SR_CTRL_TYPE_PREFIX):
                href = control.get("href", "").strip()
                if href:
                    return href.rstrip("/")
        raise ConnectionEndpointError(
            f"Device {device.get('id')} exposes no IS-05 connection control "
            f"(no control of type {SR_CTRL_TYPE_PREFIX}*)."
        )

    async def connection_base_for_device(self, device_id: str) -> str:
        device = await self.get_resource("devices", device_id)
        return self._connection_base_from_device(device)

    async def connection_base_for(self, kind: str, resource_id: str) -> tuple[str, dict[str, Any]]:
        """Return ``(is05_base_url, resource)`` for a sender or receiver.

        ``is05_base_url`` is the device's advertised Connection API base, e.g.
        ``http://node:port/x-nmos/connection/v1.1``.
        """
        if kind not in ("senders", "receivers"):
            raise ValueError("connection_base_for expects kind 'senders' or 'receivers'.")
        resource = await self.get_resource(kind, resource_id)
        device_id = resource.get("device_id")
        if not device_id:
            raise ConnectionEndpointError(f"{kind[:-1]} {resource_id} has no device_id.")
        base = await self.connection_base_for_device(device_id)
        return base, resource

    async def registry_info(self) -> dict[str, Any]:
        """Resolved registry base + reachability + coarse resource counts."""
        base = self._base()
        info: dict[str, Any] = {"query_api_base": base, "source": self._resolver.source, "reachable": False}
        try:
            counts = {}
            for kind in ("nodes", "devices", "senders", "receivers"):
                counts[kind] = len(await self.list_resources(kind))
            info["reachable"] = True
            info["counts"] = counts
        except Exception as exc:  # surfaced to the caller as diagnostic text
            info["error"] = str(exc)
        return info
