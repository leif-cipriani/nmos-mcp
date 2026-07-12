"""Tests for the crosspoint matrix renderer."""

from __future__ import annotations

from nmos_mcp.matrix import MARK_ACTIVE, MARK_INACTIVE, MARK_NONE, render_crosspoint

SENDERS = [{"id": "s1", "label": "Cam A"}, {"id": "s2", "label": "Cam B"}]
RECEIVERS = [
    {"id": "r1", "label": "Mon 1", "subscription": {"sender_id": "s2", "active": True}},
    {"id": "r2", "label": "Mon 2", "subscription": {"sender_id": None, "active": False}},
    {"id": "r3", "label": "Mon 3", "subscription": {"sender_id": "s1", "active": False}},
]


def _row(text: str, code: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(code + " "))


def _cells(line: str) -> str:
    # Only the grid cells live to the right of the '│' gutter; labels can contain
    # letters that collide with the markers (e.g. the 'o' in "Mon 2").
    return line.split("│", 1)[1]


def test_active_and_inactive_marks():
    out = render_crosspoint(SENDERS, RECEIVERS)
    assert MARK_ACTIVE in _cells(_row(out, "R1"))       # connected + active
    assert MARK_INACTIVE in _cells(_row(out, "R3"))     # subscribed but inactive
    r2 = _cells(_row(out, "R2"))
    assert MARK_ACTIVE not in r2 and MARK_INACTIVE not in r2 and MARK_NONE in r2
    assert "1 active route(s)" in out


def test_column_alignment_places_mark_under_right_sender():
    out = render_crosspoint(SENDERS, RECEIVERS)
    header = _cells(next(line for line in out.splitlines() if "S1" in line and "S2" in line))
    r1 = _cells(_row(out, "R1"))
    # R1 is connected to s2 -> the X must sit at the S2 column position, not S1.
    assert r1.index(MARK_ACTIVE) == header.index("S2")


def test_legend_lists_codes_and_ids():
    out = render_crosspoint(SENDERS, RECEIVERS)
    assert "Senders (columns):" in out and "Receivers (rows):" in out
    assert "s1" in out and "r1" in out
    assert "S1" in out and "R1" in out


def test_off_grid_subscription_noted():
    receivers = [{"id": "r1", "label": "Mon 1", "subscription": {"sender_id": "ghost", "active": True}}]
    out = render_crosspoint(SENDERS, receivers)
    assert "not present in the registry" in out
    assert "ghost" in out


def test_empty_registry():
    assert "No senders or receivers" in render_crosspoint([], [])
