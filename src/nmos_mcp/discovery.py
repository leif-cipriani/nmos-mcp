"""Resolve the NMOS Registry Query API base URL.

Primary source is the configured ``NMOS_REGISTRY_URL``. If that is unset we fall
back to mDNS DNS-SD, browsing for ``_nmos-query._tcp`` advertisements (as a real
NMOS client would). The result is cached; ``refresh=True`` forces re-resolution.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .config import Settings
from .errors import RegistryUnavailableError

_QUERY_SERVICE_TYPE = "_nmos-query._tcp.local."


def _query_base(scheme: str, host: str, port: int, version: str) -> str:
    return f"{scheme}://{host}:{port}/x-nmos/query/{version}/"


def _normalise_configured_url(raw: str, settings: Settings) -> str:
    """Turn a user-supplied registry URL into a full Query API base.

    Accepts anything from a bare host to a complete versioned base:
      - "registry.local"                          -> scheme+default assumed
      - "http://registry.local:8235"              -> query path appended
      - "http://reg/x-nmos/query/v1.3/"           -> used as-is
    """
    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"{settings.scheme}://{candidate}"

    parsed = urlparse(candidate)
    scheme = parsed.scheme or settings.scheme
    host = parsed.hostname
    if not host:
        raise RegistryUnavailableError(f"Could not parse a host from NMOS_REGISTRY_URL={raw!r}.")
    port = parsed.port or (443 if scheme == "https" else 80)

    # If a Query API path is already present, respect it (normalise trailing slash).
    if "/x-nmos/query/" in parsed.path:
        return candidate.rstrip("/") + "/"

    return _query_base(scheme, host, port, settings.query_version)


def _discover_via_mdns(settings: Settings) -> str:
    """Browse mDNS for a registry Query API. Imported lazily so HTTP-only users
    without a working multicast network never pay for zeroconf."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RegistryUnavailableError(
            "No NMOS_REGISTRY_URL configured and the 'zeroconf' package is unavailable "
            "for mDNS discovery."
        ) from exc

    import socket
    import threading

    found: list[tuple[int, str]] = []  # (priority, base_url)
    done = threading.Event()

    class _Listener(ServiceListener):
        def add_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name, timeout=int(settings.mdns_timeout * 1000))
            if not info or not info.addresses:
                return
            props = {
                (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in (info.properties or {}).items()
            }
            proto = props.get("api_proto", settings.scheme)
            versions = props.get("api_ver", settings.query_version)
            version = settings.query_version if settings.query_version in versions else versions.split(",")[-1]
            priority = int(props.get("pri", 100))
            host = socket.inet_ntoa(info.addresses[0])
            base = _query_base(proto, host, info.port, version)
            found.append((priority, base))
            done.set()

        def update_service(self, *args: object) -> None:  # required by interface
            pass

        def remove_service(self, *args: object) -> None:  # required by interface
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, _QUERY_SERVICE_TYPE, _Listener())
        done.wait(timeout=settings.mdns_timeout)
    finally:
        zc.close()

    if not found:
        raise RegistryUnavailableError(
            f"No NMOS registry found via mDNS ({_QUERY_SERVICE_TYPE}) within "
            f"{settings.mdns_timeout}s, and NMOS_REGISTRY_URL is not set."
        )
    # Lowest 'pri' wins, matching NMOS registry priority semantics.
    found.sort(key=lambda item: item[0])
    return found[0][1]


class RegistryResolver:
    """Caches the resolved Query API base URL."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base: str | None = None

    def resolve(self, refresh: bool = False) -> str:
        if self._base and not refresh:
            return self._base

        if self._settings.registry_url:
            self._base = _normalise_configured_url(self._settings.registry_url, self._settings)
        else:
            self._base = _discover_via_mdns(self._settings)
        return self._base

    @property
    def source(self) -> str:
        return "config" if self._settings.registry_url else "mdns"
