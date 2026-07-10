"""Shared ``httpx.AsyncClient`` factory with TLS config and IS-10 bearer injection."""

from __future__ import annotations

import httpx

from .auth import TokenProvider
from .config import Settings


def make_client(settings: Settings, token_provider: TokenProvider) -> httpx.AsyncClient:
    """Build a single reusable async client.

    A request event hook injects the ``Authorization`` header on every outgoing
    request (a no-op when IS-10 auth is disabled), so callers never handle tokens.
    """

    async def _inject_auth(request: httpx.Request) -> None:
        for key, value in (await token_provider.auth_header()).items():
            request.headers[key] = value

    return httpx.AsyncClient(
        timeout=settings.request_timeout,
        verify=settings.verify_tls,
        follow_redirects=True,
        headers={"Accept": "application/json"},
        event_hooks={"request": [_inject_auth]},
    )
