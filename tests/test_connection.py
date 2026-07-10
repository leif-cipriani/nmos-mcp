"""IS-05 connect / disconnect orchestration tests."""

from __future__ import annotations

import json

import pytest
from httpx import Response

from nmos_mcp.errors import ConnectionManagementError
from tests.conftest import CONN, RECEIVER_ID, SAMPLE_SDP, SENDER_ID


def _active(sender_id, master_enable):
    return {
        "sender_id": sender_id,
        "master_enable": master_enable,
        "activation": {"mode": None},
        "transport_params": [{"multicast_ip": "239.0.0.1", "destination_port": 5004}],
    }


async def test_connect_sends_correct_patch(services, mock_nmos):
    _, _, conn = services
    patch_route = mock_nmos.patch(f"{CONN}/single/receivers/{RECEIVER_ID}/staged").mock(
        return_value=Response(200, json={"sender_id": SENDER_ID, "master_enable": True})
    )
    mock_nmos.get(f"{CONN}/single/receivers/{RECEIVER_ID}/active").mock(
        return_value=Response(200, json=_active(SENDER_ID, True))
    )

    result = await conn.connect(SENDER_ID, RECEIVER_ID)

    # Assert the staged PATCH carried the expected IS-05 body.
    body = json.loads(patch_route.calls.last.request.content)
    assert body["sender_id"] == SENDER_ID
    assert body["master_enable"] is True
    assert body["activation"]["mode"] == "activate_immediate"
    assert body["transport_file"]["type"] == "application/sdp"
    assert body["transport_file"]["data"] == SAMPLE_SDP

    assert result.master_enable is True
    assert result.sender_id == SENDER_ID
    assert result.transport_params[0]["multicast_ip"] == "239.0.0.1"


async def test_disconnect_clears_subscription(services, mock_nmos):
    _, _, conn = services
    patch_route = mock_nmos.patch(f"{CONN}/single/receivers/{RECEIVER_ID}/staged").mock(
        return_value=Response(200, json={"sender_id": None, "master_enable": False})
    )
    mock_nmos.get(f"{CONN}/single/receivers/{RECEIVER_ID}/active").mock(
        return_value=Response(200, json=_active(None, False))
    )

    result = await conn.disconnect(RECEIVER_ID)

    body = json.loads(patch_route.calls.last.request.content)
    assert body["sender_id"] is None
    assert body["master_enable"] is False
    assert result.master_enable is False
    assert result.sender_id is None


async def test_connect_surfaces_node_rejection(services, mock_nmos):
    _, _, conn = services
    mock_nmos.patch(f"{CONN}/single/receivers/{RECEIVER_ID}/staged").mock(
        return_value=Response(500, text="constraint violation")
    )
    with pytest.raises(ConnectionManagementError):
        await conn.connect(SENDER_ID, RECEIVER_ID)


async def test_enable_sender(services, mock_nmos):
    _, _, conn = services
    mock_nmos.patch(f"{CONN}/single/senders/{SENDER_ID}/staged").mock(
        return_value=Response(200, json={"master_enable": True})
    )
    mock_nmos.get(f"{CONN}/single/senders/{SENDER_ID}/active").mock(
        return_value=Response(200, json={"master_enable": True})
    )
    result = await conn.set_sender_enabled(SENDER_ID, True)
    assert result["master_enable"] is True
