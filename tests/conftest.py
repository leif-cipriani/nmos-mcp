"""Test fixtures: an in-memory mock of a Registry Query API and a Node Connection API.

We use ``respx`` to intercept httpx calls so the connect/disconnect flows can be
exercised end-to-end without a live NMOS network.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from nmos_mcp.config import Settings
from nmos_mcp.connection import ConnectionClient
from nmos_mcp.discovery import RegistryResolver
from nmos_mcp.http import make_client
from nmos_mcp.query import QueryClient
from nmos_mcp.auth import TokenProvider

REGISTRY = "http://registry.local:8235"
NODE = "http://node.local:8080"
QUERY = f"{REGISTRY}/x-nmos/query/v1.3"
CONN = f"{NODE}/x-nmos/connection/v1.1"

SENDER_ID = "11111111-1111-1111-1111-111111111111"
RECEIVER_ID = "22222222-2222-2222-2222-222222222222"
DEVICE_ID = "33333333-3333-3333-3333-333333333333"
NODE_ID = "44444444-4444-4444-4444-444444444444"

SAMPLE_SDP = "v=0\r\no=- 0 0 IN IP4 192.168.1.1\r\ns=Cam1\r\nt=0 0\r\nm=video 5004 RTP/AVP 96\r\n"

NODE = {"id": NODE_ID, "label": "box-alpha", "tags": {}}
DEVICE = {
    "id": DEVICE_ID,
    "label": "Camera Device",
    "node_id": NODE_ID,
    "tags": {"location": ["Studio A"]},
    "controls": [
        {"type": "urn:x-nmos:control:sr-ctrl/v1.1", "href": f"{CONN}"},
    ],
}
SENDER = {"id": SENDER_ID, "label": "Cam 1 Sender", "device_id": DEVICE_ID, "tags": {}}
RECEIVER = {"id": RECEIVER_ID, "label": "AES67 receiver 1", "device_id": DEVICE_ID, "tags": {}}


@pytest.fixture
def settings() -> Settings:
    return Settings(registry_url=REGISTRY, verify_tls=False)


@pytest.fixture
def services(settings: Settings):
    resolver = RegistryResolver(settings)
    client = make_client(settings, TokenProvider(settings))
    query = QueryClient(client, resolver)
    connection = ConnectionClient(client, query)
    return client, query, connection


@pytest.fixture
def mock_nmos():
    """Register a full happy-path mock of the registry + node APIs."""
    with respx.mock(assert_all_called=False) as router:
        # IS-04 Query API
        router.get(f"{QUERY}/senders").mock(return_value=Response(200, json=[SENDER]))
        router.get(f"{QUERY}/receivers").mock(return_value=Response(200, json=[RECEIVER]))
        router.get(f"{QUERY}/nodes").mock(return_value=Response(200, json=[]))
        router.get(f"{QUERY}/devices").mock(return_value=Response(200, json=[DEVICE]))
        router.get(f"{QUERY}/senders/{SENDER_ID}").mock(return_value=Response(200, json=SENDER))
        router.get(f"{QUERY}/receivers/{RECEIVER_ID}").mock(return_value=Response(200, json=RECEIVER))
        router.get(f"{QUERY}/devices/{DEVICE_ID}").mock(return_value=Response(200, json=DEVICE))
        router.get(f"{QUERY}/nodes/{NODE_ID}").mock(return_value=Response(200, json=NODE))

        # IS-05 Connection API (Node)
        router.get(f"{CONN}/single/senders/{SENDER_ID}/transportfile").mock(
            return_value=Response(200, text=SAMPLE_SDP, headers={"Content-Type": "application/sdp"})
        )
        yield router
