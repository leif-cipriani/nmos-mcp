"""IS-10 authorization: OAuth2 ``client_credentials`` bearer-token provider.

A no-op when ``auth_enabled`` is false, so the same code path works for plain-HTTP
lab registries and secured deployments alike.
"""

from __future__ import annotations

import time

import httpx

from .config import Settings
from .errors import AuthError

# Refresh a little before the token actually expires to avoid edge-of-expiry 401s.
_EXPIRY_SKEW_SECONDS = 30.0


class TokenProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._settings.auth_enabled

    async def auth_header(self) -> dict[str, str]:
        """Return an ``Authorization`` header dict, or ``{}`` when auth is disabled."""
        if not self.enabled:
            return {}
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def _get_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._expires_at:
            return self._token

        s = self._settings
        if not s.auth_token_url or not s.auth_client_id or not s.auth_client_secret:
            raise AuthError(
                "auth_enabled is true but NMOS_AUTH_TOKEN_URL / NMOS_AUTH_CLIENT_ID / "
                "NMOS_AUTH_CLIENT_SECRET are not fully configured."
            )

        data = {
            "grant_type": "client_credentials",
            "client_id": s.auth_client_id,
            "client_secret": s.auth_client_secret,
        }
        if s.auth_scope:
            data["scope"] = s.auth_scope

        try:
            async with httpx.AsyncClient(verify=s.verify_tls, timeout=s.request_timeout) as client:
                resp = await client.post(s.auth_token_url, data=data)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:  # network / status errors
            raise AuthError(f"Failed to obtain IS-10 token from {s.auth_token_url}: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            raise AuthError("Token endpoint response did not contain an access_token.")

        expires_in = float(payload.get("expires_in", 3600))
        self._token = token
        self._expires_at = time.monotonic() + max(0.0, expires_in - _EXPIRY_SKEW_SECONDS)
        return token
