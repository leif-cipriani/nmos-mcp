"""FastMCP server exposing NMOS IS-04 (query) and IS-05 (connection) as MCP tools.

Run over stdio (default) for Claude Desktop / Claude Code, or ``--http`` for the
streamable-HTTP transport.
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from .auth import TokenProvider
from .config import Settings, load_settings
from .connection import ConnectionClient
from .discovery import RegistryResolver
from .errors import NmosError
from .http import make_client
from .models import RESOURCE_KINDS
from .query import QueryClient

mcp = FastMCP(
    "nmos",
    instructions=(
        "Query an AMWA NMOS registry (IS-04) and manage device connections (IS-05). "
        "Use the list_/get_ tools to discover senders and receivers, then "
        "connect_sender_to_receiver to route media between devices."
    ),
)


class _Services:
    """Lazily-built, shared clients. Created inside the event loop on first use."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.resolver = RegistryResolver(settings)
        self.token_provider = TokenProvider(settings)
        self._client = None
        self._query: QueryClient | None = None
        self._connection: ConnectionClient | None = None

    def _ensure(self) -> None:
        if self._client is None:
            self._client = make_client(self.settings, self.token_provider)
            self._query = QueryClient(self._client, self.resolver)
            self._connection = ConnectionClient(self._client, self._query)

    @property
    def query(self) -> QueryClient:
        self._ensure()
        assert self._query is not None
        return self._query

    @property
    def connection(self) -> ConnectionClient:
        self._ensure()
        assert self._connection is not None
        return self._connection


_services: _Services | None = None


def _svc() -> _Services:
    global _services
    if _services is None:
        _services = _Services(load_settings())
    return _services


def _kind_guard(kind: str) -> str:
    if kind not in RESOURCE_KINDS:
        raise NmosError(f"Unknown resource kind {kind!r}. Valid kinds: {', '.join(RESOURCE_KINDS)}.")
    return kind


# --- IS-04: discovery / query ------------------------------------------------
@mcp.tool()
async def registry_info() -> dict[str, Any]:
    """Show the resolved NMOS registry (config or mDNS), reachability and resource counts."""
    return await _svc().query.registry_info()


@mcp.tool()
async def list_nodes(label: str | None = None) -> list[dict[str, Any]]:
    """List NMOS Nodes in the registry. Optionally filter by label substring."""
    return await _svc().query.list_resources("nodes", {"label": label} if label else None)


@mcp.tool()
async def list_devices(label: str | None = None) -> list[dict[str, Any]]:
    """List NMOS Devices (a Node hosts one or more Devices)."""
    return await _svc().query.list_resources("devices", {"label": label} if label else None)


@mcp.tool()
async def list_senders(label: str | None = None) -> list[dict[str, Any]]:
    """List Senders (media egress points) advertised in the registry."""
    return await _svc().query.list_resources("senders", {"label": label} if label else None)


@mcp.tool()
async def list_receivers(label: str | None = None) -> list[dict[str, Any]]:
    """List Receivers (media ingest points) advertised in the registry."""
    return await _svc().query.list_resources("receivers", {"label": label} if label else None)


@mcp.tool()
async def list_flows(label: str | None = None) -> list[dict[str, Any]]:
    """List Flows (the essence a Sender transmits)."""
    return await _svc().query.list_resources("flows", {"label": label} if label else None)


@mcp.tool()
async def list_sources(label: str | None = None) -> list[dict[str, Any]]:
    """List Sources (the abstract origin of one or more Flows)."""
    return await _svc().query.list_resources("sources", {"label": label} if label else None)


@mcp.tool()
async def get_resource(kind: str, resource_id: str) -> dict[str, Any]:
    """Get one resource by id. ``kind`` is one of: nodes, devices, sources, flows, senders, receivers, subscriptions."""
    return await _svc().query.get_resource(_kind_guard(kind), resource_id)


@mcp.tool()
async def query_resources(kind: str, rql: str | None = None, label: str | None = None) -> list[dict[str, Any]]:
    """Query a resource collection with an IS-04 filter.

    Pass ``label`` for a simple label match, or ``rql`` for an IS-04 RQL expression
    (e.g. ``eq(transport,urn:x-nmos:transport:rtp.mcast)``).
    """
    filters: dict[str, Any] = {}
    if label:
        filters["label"] = label
    if rql:
        filters["query.rql"] = rql
    return await _svc().query.list_resources(_kind_guard(kind), filters or None)


# --- IS-05: connection management --------------------------------------------
@mcp.tool()
async def get_sender(sender_id: str) -> dict[str, Any]:
    """Show a Sender's IS-05 staged + active connection state and its constraints."""
    conn = _svc().connection
    return {
        "staged": await conn.get_endpoint("senders", sender_id, "staged"),
        "active": await conn.get_endpoint("senders", sender_id, "active"),
        "constraints": await conn.get_endpoint("senders", sender_id, "constraints"),
    }


@mcp.tool()
async def get_receiver(receiver_id: str) -> dict[str, Any]:
    """Show a Receiver's IS-05 staged + active connection state and its constraints."""
    conn = _svc().connection
    return {
        "staged": await conn.get_endpoint("receivers", receiver_id, "staged"),
        "active": await conn.get_endpoint("receivers", receiver_id, "active"),
        "constraints": await conn.get_endpoint("receivers", receiver_id, "constraints"),
    }


@mcp.tool()
async def get_sender_transport_file(sender_id: str) -> str:
    """Fetch a Sender's SDP transport file (the description a Receiver needs to subscribe)."""
    return await _svc().connection.get_transportfile(sender_id)


@mcp.tool()
async def connect_sender_to_receiver(sender_id: str, receiver_id: str) -> dict[str, Any]:
    """Connect a Sender to a Receiver (route media) with an immediate IS-05 activation.

    Pulls the Sender's SDP transport file and stages it on the Receiver with
    master_enable=true, then activates immediately. Returns the Receiver's resulting
    active state.
    """
    return (await _svc().connection.connect(sender_id, receiver_id)).model_dump()


@mcp.tool()
async def disconnect_receiver(receiver_id: str) -> dict[str, Any]:
    """Disconnect a Receiver (clear its subscription and disable it) with immediate activation."""
    return (await _svc().connection.disconnect(receiver_id)).model_dump()


@mcp.tool()
async def enable_sender(sender_id: str) -> dict[str, Any]:
    """Enable a Sender (master_enable=true) so it transmits, with immediate activation."""
    return await _svc().connection.set_sender_enabled(sender_id, True)


@mcp.tool()
async def disable_sender(sender_id: str) -> dict[str, Any]:
    """Disable a Sender (master_enable=false) so it stops transmitting, with immediate activation."""
    return await _svc().connection.set_sender_enabled(sender_id, False)


@mcp.tool()
async def bulk_connect(pairs: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Connect several Sender->Receiver pairs at once.

    ``pairs`` is a list of objects like ``{"sender_id": "...", "receiver_id": "..."}``.
    Batched per Node via the IS-05 /bulk/receivers endpoint.
    """
    return await _svc().connection.bulk_connect(pairs)


@mcp.tool()
async def stage_receiver(receiver_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Advanced: send a raw IS-05 staged PATCH body to a Receiver (full control of transport_params/activation)."""
    return await _svc().connection.stage("receivers", receiver_id, patch)


@mcp.tool()
async def stage_sender(sender_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Advanced: send a raw IS-05 staged PATCH body to a Sender (e.g. set multicast destination)."""
    return await _svc().connection.stage("senders", sender_id, patch)


def main() -> None:
    parser = argparse.ArgumentParser(description="NMOS MCP server (IS-04 query + IS-05 connection).")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over streamable-HTTP instead of stdio.",
    )
    args = parser.parse_args()
    mcp.run(transport="streamable-http" if args.http else "stdio")


if __name__ == "__main__":
    main()
