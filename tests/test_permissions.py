"""Tests for the MCP-enforced permission engine."""

from __future__ import annotations

import json

import pytest
from httpx import Response

from nmos_mcp import permissions as perm
from nmos_mcp.connection import ConnectionClient
from nmos_mcp.errors import NmosError, PermissionDeniedError
from nmos_mcp.permissions import (
    Group,
    MetadataResolver,
    Policy,
    PolicyEngine,
    ResourceRef,
    TargetMeta,
    load_policy,
)
from tests.conftest import CONN, RECEIVER_ID, SENDER_ID


# --- Group / selector matching ----------------------------------------------
def _meta(**kw) -> TargetMeta:
    return TargetMeta(ref=ResourceRef("receivers", "r1"), **kw)


def test_group_matches_by_tag():
    g = Group.parse("g", {"tags": {"location": ["Studio A"]}})
    assert g.matches(_meta(tags={"location": ["Studio A"]}))
    assert not g.matches(_meta(tags={"location": ["Studio B"]}))


def test_group_matches_by_device_uuid():
    g = Group.parse("g", {"devices": ["dev-1"]})
    assert g.matches(_meta(device_id="dev-1"))
    assert not g.matches(_meta(device_id="dev-2"))


def test_group_matches_by_label_regex():
    g = Group.parse("g", {"labels": ["^AES67 receiver"]})
    assert g.matches(_meta(labels={"AES67 receiver 4"}))
    assert not g.matches(_meta(labels={"Camera 1"}))


def test_group_matches_by_node_id_or_label():
    g = Group.parse("g", {"nodes": ["node-1", "box-alpha"]})
    assert g.matches(_meta(node_id="node-1"))
    assert g.matches(_meta(node_label="box-alpha"))
    assert not g.matches(_meta(node_id="node-9", node_label="box-beta"))


def test_all_group_matches_everything():
    assert Group("all").matches(_meta())


# --- Rule parsing & verb hierarchy ------------------------------------------
def test_write_verb_grants_all_specific_write_actions():
    rule = perm.Rule.parse({"actions": ["write"]}, set())
    for a in (perm.CONNECT, perm.DISCONNECT, perm.ENABLE, perm.DISABLE, perm.STAGE):
        assert rule.grants(a)
    assert not rule.grants(perm.READ)


def test_specific_verb_does_not_leak():
    rule = perm.Rule.parse({"actions": ["connect"]}, set())
    assert rule.grants(perm.CONNECT)
    assert not rule.grants(perm.DISCONNECT)


def test_unknown_action_rejected():
    with pytest.raises(NmosError):
        perm.Rule.parse({"actions": ["frobnicate"]}, set())


def test_rule_referencing_undefined_group_rejected():
    with pytest.raises(NmosError):
        Policy.parse({"rules": [{"actions": ["connect"], "groups": ["ghost"]}]}, "test")


# --- Policy file loading -----------------------------------------------------
def test_load_policy_yaml(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("groups:\n  g:\n    labels: ['^Cam']\nrules:\n  - actions: [connect]\n    groups: [g]\n")
    policy = load_policy(str(p))
    assert "g" in policy.groups
    assert policy.rules[0].grants(perm.CONNECT)


def test_load_policy_json(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"rules": [{"actions": ["write"], "groups": ["all"]}]}))
    policy = load_policy(str(p))
    assert policy.rules[0].grants(perm.STAGE)


def test_load_policy_none_denies_all_writes():
    policy = load_policy(None)
    engine = PolicyEngine(policy, resolver=None, mode="enforce")  # resolver unused for _decide
    assert engine._decide(perm.CONNECT, _meta()).allowed is False
    assert engine._decide(perm.READ, _meta()).allowed is True


# --- Engine decision logic ---------------------------------------------------
def _engine(policy_dict, mode="enforce") -> PolicyEngine:
    return PolicyEngine(Policy.parse(policy_dict, "test"), resolver=None, mode=mode)


def test_read_always_allowed():
    eng = _engine({})
    assert eng._decide(perm.READ, _meta()).allowed


def test_write_allowed_by_matching_rule():
    eng = _engine({"groups": {"g": {"labels": ["^Cam"]}}, "rules": [{"actions": ["connect"], "groups": ["g"]}]})
    assert eng._decide(perm.CONNECT, _meta(labels={"Cam 1"})).allowed
    # Same verb, target outside the group -> denied.
    assert not eng._decide(perm.CONNECT, _meta(labels={"Mic 1"})).allowed


def test_deny_overrides_allow():
    eng = _engine(
        {
            "rules": [{"actions": ["write"], "groups": ["all"]}],
            "deny": [{"actions": ["stage"], "groups": ["all"]}],
        }
    )
    assert eng._decide(perm.CONNECT, _meta()).allowed
    assert not eng._decide(perm.STAGE, _meta()).allowed


def test_open_mode_allows_everything():
    eng = _engine({}, mode="open")
    assert eng._decide(perm.STAGE, _meta()).allowed


# --- End-to-end enforcement: no HTTP write escapes a denial ------------------
def _guarded_connection(services, policy_dict, mode="enforce") -> ConnectionClient:
    client, query, _ = services
    engine = PolicyEngine(Policy.parse(policy_dict, "test"), MetadataResolver(query), mode=mode)
    return ConnectionClient(client, query, engine)


async def test_connect_allowed_when_policy_permits(services, mock_nmos):
    # Receiver label is "AES67 receiver 1" -> matches the group.
    conn = _guarded_connection(
        services,
        {"groups": {"rx": {"labels": ["^AES67 receiver"]}}, "rules": [{"actions": ["connect"], "groups": ["rx"]}]},
    )
    patch_route = mock_nmos.patch(f"{CONN}/single/receivers/{RECEIVER_ID}/staged").mock(
        return_value=Response(200, json={})
    )
    mock_nmos.get(f"{CONN}/single/receivers/{RECEIVER_ID}/active").mock(
        return_value=Response(200, json={"sender_id": SENDER_ID, "master_enable": True, "transport_params": []})
    )
    result = await conn.connect(SENDER_ID, RECEIVER_ID)
    assert result.master_enable is True
    assert patch_route.called


async def test_connect_denied_issues_no_patch(services, mock_nmos):
    # Empty policy -> all writes denied. The IS-05 PATCH must never be sent.
    conn = _guarded_connection(services, {})
    patch_route = mock_nmos.patch(f"{CONN}/single/receivers/{RECEIVER_ID}/staged").mock(
        return_value=Response(200, json={})
    )
    with pytest.raises(PermissionDeniedError):
        await conn.connect(SENDER_ID, RECEIVER_ID)
    assert patch_route.called is False


async def test_bulk_connect_atomic_denial(services, mock_nmos):
    conn = _guarded_connection(services, {})
    bulk_route = mock_nmos.post(f"{CONN}/bulk/receivers").mock(return_value=Response(200, json=[]))
    with pytest.raises(PermissionDeniedError):
        await conn.bulk_connect([{"sender_id": SENDER_ID, "receiver_id": RECEIVER_ID}])
    assert bulk_route.called is False


async def test_stage_denied_by_default(services, mock_nmos):
    conn = _guarded_connection(services, {"rules": [{"actions": ["connect"], "groups": ["all"]}]})
    with pytest.raises(PermissionDeniedError):
        await conn.stage("receivers", RECEIVER_ID, {"master_enable": True})


async def test_open_mode_bypasses_enforcement(services, mock_nmos):
    conn = _guarded_connection(services, {}, mode="open")
    patch_route = mock_nmos.patch(f"{CONN}/single/receivers/{RECEIVER_ID}/staged").mock(
        return_value=Response(200, json={})
    )
    mock_nmos.get(f"{CONN}/single/receivers/{RECEIVER_ID}/active").mock(
        return_value=Response(200, json={"sender_id": SENDER_ID, "master_enable": True, "transport_params": []})
    )
    await conn.connect(SENDER_ID, RECEIVER_ID)
    assert patch_route.called
