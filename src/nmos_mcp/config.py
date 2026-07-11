"""Runtime configuration, loaded from environment (prefix ``NMOS_``) and ``.env``.

All fields are optional with lab-friendly defaults so the server also runs against a
plain-HTTP registry with zero configuration beyond ``NMOS_REGISTRY_URL`` (or nothing
at all, in which case mDNS discovery is attempted).
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NMOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Registry / discovery -------------------------------------------------
    # Full or partial registry URL, e.g. "http://registry.local:8235" or a complete
    # Query API base. If unset, mDNS DNS-SD discovery (_nmos-query._tcp) is attempted.
    registry_url: str | None = Field(default=None)
    query_version: str = Field(default="v1.3")
    connection_version: str = Field(default="v1.1")
    mdns_timeout: float = Field(default=4.0, description="Seconds to browse for a registry via mDNS.")

    # --- Transport / TLS ------------------------------------------------------
    # Scheme used when only a host is discovered (mDNS) or when a bare host is given.
    use_https: bool = Field(default=False)
    # True/False to toggle verification, or a path to a CA bundle / client cert dir.
    verify_tls: Union[bool, str] = Field(default=True)
    request_timeout: float = Field(default=15.0)

    # --- IS-10 authorization (OAuth2 client_credentials) ----------------------
    auth_enabled: bool = Field(default=False)
    auth_token_url: str | None = Field(default=None)
    auth_client_id: str | None = Field(default=None)
    auth_client_secret: str | None = Field(default=None)
    auth_scope: str | None = Field(default="connection query")

    # --- Permissions (MCP-enforced authorization) -----------------------------
    # Path to a YAML/JSON policy file. In 'enforce' mode reads are always allowed
    # but every write action needs an explicit allow rule; with no file, all writes
    # are denied. 'open' disables all checks (dev/testing only).
    permissions_file: str | None = Field(default=None)
    permissions_mode: Literal["enforce", "open"] = Field(default="enforce")

    @field_validator("verify_tls", mode="before")
    @classmethod
    def _coerce_verify_tls(cls, value: object) -> Union[bool, str]:
        """Map boolean-like strings ("true"/"false"/"0"/...) to real bools.

        Anything else (a non-empty, non-boolean string) is treated as a CA bundle
        path and passed through to httpx unchanged.
        """
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off", ""}:
                return False
        return value

    @property
    def scheme(self) -> str:
        return "https" if self.use_https else "http"


def load_settings() -> Settings:
    """Instantiate settings (kept as a function for easy test overrides)."""
    return Settings()
