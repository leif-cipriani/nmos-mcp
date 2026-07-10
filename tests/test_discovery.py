"""Registry URL normalisation tests (mDNS path is exercised manually / e2e)."""

from __future__ import annotations

import pytest

from nmos_mcp.config import Settings
from nmos_mcp.discovery import RegistryResolver
from nmos_mcp.errors import RegistryUnavailableError


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://registry.local:8235", "http://registry.local:8235/x-nmos/query/v1.3/"),
        ("registry.local", "http://registry.local:80/x-nmos/query/v1.3/"),
        ("https://reg:1234", "https://reg:1234/x-nmos/query/v1.3/"),
        ("http://reg/x-nmos/query/v1.3/", "http://reg/x-nmos/query/v1.3/"),
        ("http://reg/x-nmos/query/v1.3", "http://reg/x-nmos/query/v1.3/"),
    ],
)
def test_configured_url_normalisation(raw, expected):
    resolver = RegistryResolver(Settings(registry_url=raw))
    assert resolver.resolve() == expected
    assert resolver.source == "config"


def test_https_scheme_applied_to_bare_host():
    resolver = RegistryResolver(Settings(registry_url="reg.local", use_https=True))
    assert resolver.resolve() == "https://reg.local:443/x-nmos/query/v1.3/"


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("false", False), ("1", True), ("off", False), ("", False)],
)
def test_verify_tls_boolean_strings_coerced(raw, expected):
    # Env values are strings; boolean-like ones must become real bools, not a CA path.
    assert Settings(verify_tls=raw).verify_tls is expected


def test_verify_tls_path_passthrough():
    assert Settings(verify_tls="/etc/ssl/ca.pem").verify_tls == "/etc/ssl/ca.pem"


def test_resolution_is_cached():
    resolver = RegistryResolver(Settings(registry_url="http://a:1"))
    first = resolver.resolve()
    # Even after changing nothing, a second call returns the cached base.
    assert resolver.resolve() is first
