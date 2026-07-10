"""IS-04 query + IS-05 endpoint-resolution tests."""

from __future__ import annotations

import pytest

from nmos_mcp.errors import ConnectionEndpointError
from tests.conftest import CONN, DEVICE_ID, RECEIVER_ID, SENDER_ID


async def test_list_senders(services, mock_nmos):
    _, query, _ = services
    senders = await query.list_resources("senders")
    assert senders[0]["id"] == SENDER_ID


async def test_get_device(services, mock_nmos):
    _, query, _ = services
    device = await query.get_resource("devices", DEVICE_ID)
    assert device["id"] == DEVICE_ID


async def test_connection_base_resolution(services, mock_nmos):
    _, query, _ = services
    base, resource = await query.connection_base_for("receivers", RECEIVER_ID)
    assert base == CONN
    assert resource["id"] == RECEIVER_ID


async def test_missing_sr_ctrl_control_raises(services, mock_nmos):
    _, query, _ = services
    device = await query.get_resource("devices", DEVICE_ID)
    device["controls"] = []  # simulate a device without an IS-05 control
    with pytest.raises(ConnectionEndpointError):
        query._connection_base_from_device(device)


async def test_registry_info(services, mock_nmos):
    _, query, _ = services
    info = await query.registry_info()
    assert info["reachable"] is True
    assert info["source"] == "config"
    assert info["counts"]["senders"] == 1
