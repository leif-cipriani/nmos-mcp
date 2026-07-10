"""Lightweight typed models and constants for NMOS payloads.

NMOS resources are large and versioned; rather than model every field we keep raw
dicts for pass-through data and add small typed helpers for the pieces we construct
or reason about (control hrefs, staged PATCH bodies, connect results).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# IS-04 Query API resource collections.
ResourceKind = Literal[
    "nodes",
    "devices",
    "sources",
    "flows",
    "senders",
    "receivers",
    "subscriptions",
]

RESOURCE_KINDS: tuple[str, ...] = (
    "nodes",
    "devices",
    "sources",
    "flows",
    "senders",
    "receivers",
    "subscriptions",
)

# The device control 'type' URN prefix that points at an IS-05 Connection API.
SR_CTRL_TYPE_PREFIX = "urn:x-nmos:control:sr-ctrl/"

# MIME type advertised for SDP transport files.
SDP_MIME_TYPE = "application/sdp"


class ActivationRequest(BaseModel):
    """IS-05 activation block. Immediate activation is the common interactive case."""

    mode: Literal["activate_immediate", "activate_scheduled_absolute", "activate_scheduled_relative"] = (
        "activate_immediate"
    )
    requested_time: str | None = None

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"mode": self.mode}
        if self.requested_time is not None:
            body["requested_time"] = self.requested_time
        return body


class ConnectResult(BaseModel):
    """Compact, human-readable result of a connect/disconnect operation."""

    receiver_id: str
    sender_id: str | None
    master_enable: bool
    activation_mode: str | None
    transport_params: list[dict[str, Any]] = []
    message: str = ""
