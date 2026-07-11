"""IS-05 Connection API client and the connect/disconnect orchestration.

The Connection API is hosted per-Node (not on the registry); base URLs are resolved
via :class:`~nmos_mcp.query.QueryClient` from each device's ``sr-ctrl`` control.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx

from . import permissions as perm
from .errors import ConnectionManagementError, ResourceNotFoundError
from .models import SDP_MIME_TYPE, ActivationRequest, ConnectResult
from .permissions import PolicyEngine, ResourceRef
from .query import QueryClient


class ConnectionClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        query: QueryClient,
        enforcer: PolicyEngine | None = None,
    ) -> None:
        self._client = client
        self._query = query
        self._enforcer = enforcer

    async def _require(self, action: str, refs: list[ResourceRef]) -> None:
        # Enforcement runs here (service layer) before any HTTP write, so no tool
        # can accidentally skip it. None enforcer => unguarded (tests only).
        if self._enforcer is not None:
            await self._enforcer.require(action, refs)

    # --- Low-level IS-05 calls ------------------------------------------------
    async def _get_json(self, url: str) -> Any:
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise ConnectionManagementError(f"GET {url} failed: {exc}") from exc
        if resp.status_code == 404:
            raise ResourceNotFoundError(f"Not found: {url}")
        resp.raise_for_status()
        return resp.json()

    async def _patch_staged(self, base: str, kind: str, resource_id: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{base}/single/{kind}/{resource_id}/staged"
        try:
            resp = await self._client.patch(url, json=body)
        except httpx.HTTPError as exc:
            raise ConnectionManagementError(f"PATCH {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ConnectionManagementError(
                f"Staged PATCH to {url} rejected (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    async def get_endpoint(self, kind: str, resource_id: str, endpoint: str) -> Any:
        """Fetch ``staged`` | ``active`` | ``constraints`` for a sender/receiver."""
        base, _ = await self._query.connection_base_for(kind, resource_id)
        return await self._get_json(f"{base}/single/{kind}/{resource_id}/{endpoint}")

    async def get_transportfile(self, sender_id: str, base: str | None = None) -> str:
        """Fetch a sender's SDP transport file text via IS-05."""
        if base is None:
            base, _ = await self._query.connection_base_for("senders", sender_id)
        url = f"{base}/single/senders/{sender_id}/transportfile"
        try:
            resp = await self._client.get(url, headers={"Accept": SDP_MIME_TYPE})
        except httpx.HTTPError as exc:
            raise ConnectionManagementError(f"GET {url} failed: {exc}") from exc
        if resp.status_code == 404:
            raise ResourceNotFoundError(f"Sender {sender_id} has no transport file at {url}.")
        resp.raise_for_status()
        return resp.text

    # --- Orchestration --------------------------------------------------------
    async def connect(self, sender_id: str, receiver_id: str) -> ConnectResult:
        """Route ``sender_id`` -> ``receiver_id`` with an immediate activation."""
        await self._require(perm.CONNECT, [ResourceRef("receivers", receiver_id)])
        receiver_base, _ = await self._query.connection_base_for("receivers", receiver_id)
        sender_base, _ = await self._query.connection_base_for("senders", sender_id)
        sdp = await self.get_transportfile(sender_id, base=sender_base)

        body = {
            "sender_id": sender_id,
            "master_enable": True,
            "activation": ActivationRequest().to_body(),
            "transport_file": {"data": sdp, "type": SDP_MIME_TYPE},
        }
        await self._patch_staged(receiver_base, "receivers", receiver_id, body)
        return await self._result_from_active(
            receiver_base, receiver_id, f"Connected sender {sender_id} -> receiver {receiver_id}."
        )

    async def disconnect(self, receiver_id: str) -> ConnectResult:
        await self._require(perm.DISCONNECT, [ResourceRef("receivers", receiver_id)])
        receiver_base, _ = await self._query.connection_base_for("receivers", receiver_id)
        body = {
            "sender_id": None,
            "master_enable": False,
            "activation": ActivationRequest().to_body(),
        }
        await self._patch_staged(receiver_base, "receivers", receiver_id, body)
        return await self._result_from_active(
            receiver_base, receiver_id, f"Disconnected receiver {receiver_id}."
        )

    async def set_sender_enabled(self, sender_id: str, enabled: bool) -> dict[str, Any]:
        await self._require(perm.ENABLE if enabled else perm.DISABLE, [ResourceRef("senders", sender_id)])
        sender_base, _ = await self._query.connection_base_for("senders", sender_id)
        body = {"master_enable": enabled, "activation": ActivationRequest().to_body()}
        await self._patch_staged(sender_base, "senders", sender_id, body)
        active = await self._get_json(f"{sender_base}/single/senders/{sender_id}/active")
        return {
            "sender_id": sender_id,
            "master_enable": active.get("master_enable"),
            "message": f"Sender {sender_id} {'enabled' if enabled else 'disabled'}.",
        }

    async def stage(self, kind: str, resource_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Advanced: pass an arbitrary IS-05 staged PATCH body straight through."""
        await self._require(perm.STAGE, [ResourceRef(kind, resource_id)])
        base, _ = await self._query.connection_base_for(kind, resource_id)
        return await self._patch_staged(base, kind, resource_id, patch)

    async def bulk_connect(self, pairs: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Connect many sender->receiver pairs, batched per Node via ``/bulk/receivers``.

        ``pairs`` is a list of ``{"sender_id": ..., "receiver_id": ...}``.
        """
        # Atomic authorization: reject the whole batch if any receiver is not
        # permitted, before issuing any HTTP request.
        await self._require(perm.CONNECT, [ResourceRef("receivers", p["receiver_id"]) for p in pairs])
        # Group by the receiver's Connection API base (bulk is per-Node).
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for pair in pairs:
            sender_id, receiver_id = pair["sender_id"], pair["receiver_id"]
            receiver_base, _ = await self._query.connection_base_for("receivers", receiver_id)
            sdp = await self.get_transportfile(sender_id)
            grouped[receiver_base].append(
                {
                    "id": receiver_id,
                    "params": {
                        "sender_id": sender_id,
                        "master_enable": True,
                        "activation": ActivationRequest().to_body(),
                        "transport_file": {"data": sdp, "type": SDP_MIME_TYPE},
                    },
                }
            )

        results: list[dict[str, Any]] = []
        for base, items in grouped.items():
            url = f"{base}/bulk/receivers"
            try:
                resp = await self._client.post(url, json=items)
            except httpx.HTTPError as exc:
                raise ConnectionManagementError(f"POST {url} failed: {exc}") from exc
            if resp.status_code >= 400:
                raise ConnectionManagementError(
                    f"Bulk connect to {url} rejected (HTTP {resp.status_code}): {resp.text}"
                )
            results.extend(resp.json())
        return results

    # --- Helpers --------------------------------------------------------------
    async def _result_from_active(self, base: str, receiver_id: str, message: str) -> ConnectResult:
        active = await self._get_json(f"{base}/single/receivers/{receiver_id}/active")
        transport_params = active.get("transport_params", [])
        return ConnectResult(
            receiver_id=receiver_id,
            sender_id=active.get("sender_id"),
            master_enable=bool(active.get("master_enable")),
            activation_mode=(active.get("activation") or {}).get("mode"),
            transport_params=transport_params,
            message=message,
        )
