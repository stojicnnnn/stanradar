"""Source adapter contract and HTTP clients.

A source does two things:
  * `fetch_new()`  - pull the newest-first listing page and return shallow `Listing` objects.
  * `enrich()`     - optionally fetch one listing's detail page to fill in description and
                     photos. Only called for listings that passed the rule filters, so a
                     detail request is never wasted on a flat that is out of budget.

Keep `fetch_new()` to a single cheap request. It runs every poll cycle, forever.

Two HTTP backends, because two of these portals check more than headers:

  httpx       - plain async client. Used for JSON APIs and the Discord webhook.
  curl_cffi   - same request, but with Chrome's actual TLS fingerprint. halooglasi.com and
                nekretnine.rs sit behind a WAF that fingerprints the TLS handshake: curl
                gets a 200 where Python's ssl module gets a 403, no matter what headers
                are sent. Sources that need it set `needs_browser_tls = True`.

Both clients expose the same `.get()` -> `.text` / `.json()` / `.raise_for_status()`
surface, so adapters do not care which one they were handed.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..models import Listing

log = logging.getLogger(__name__)

# A plain desktop Chrome fingerprint. Several Serbian portals reject default
# client user-agents outright.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sr-RS,sr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


def make_client(timeout: int = 25) -> httpx.AsyncClient:
    """Plain async client, for JSON APIs and Discord."""
    return httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True)


def make_browser_client(timeout: int = 25) -> Any:
    """Async client that presents Chrome's TLS fingerprint.

    Deliberately does not pass HEADERS. `impersonate="chrome"` installs Chrome's own
    header set in Chrome's own order, and overriding it with our list makes the headers
    disagree with the TLS fingerprint - which is exactly what these WAFs look for.
    nekretnine.rs returns 200 without custom headers and 403 with them.
    """
    from curl_cffi import requests as curl_requests

    return curl_requests.AsyncSession(
        impersonate="chrome",
        timeout=timeout,
        headers={"Accept-Language": HEADERS["Accept-Language"]},
        allow_redirects=True,
    )


class Source(ABC):
    """Base class for one portal."""

    name: str = "unnamed"
    label: str = "Unnamed"
    # Set True when the portal fingerprints TLS and needs the curl_cffi client.
    needs_browser_tls: bool = False
    # Set True when `enrich()` is the only place description and photos become available,
    # so the listing-page rule pass knows to defer the rules that depend on them.
    has_detail_fetch: bool = False
    # Set False for adapters that are scaffolded but not yet implemented.
    implemented: bool = True

    def __init__(self, client: Any):
        self.http = client

    @abstractmethod
    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        """Newest listings first. Must not raise for an empty result - return []."""

    async def enrich(self, listing: Listing) -> Listing:
        """Fill in description and photos. Default: nothing more to fetch."""
        return listing


class NotImplementedSource(Source):
    """Placeholder for a portal whose adapter still needs to be written.

    Registered on purpose, so `stanradar sources` lists it and so enabling it in config
    fails loudly instead of silently monitoring nothing.
    """

    implemented = False
    reason = "adapter not implemented yet"

    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        raise NotImplementedError(f"{self.name}: {self.reason}")


class ClientPool:
    """Holds one client per HTTP backend, each created only if something asks for it.

    Most runs only need the plain client; the browser-TLS client pulls in curl_cffi and
    a native libcurl, so it stays unbuilt unless a source actually requires it.
    """

    def __init__(self, timeout: int = 25):
        self.timeout = timeout
        self._plain: httpx.AsyncClient | None = None
        self._browser: Any | None = None

    @property
    def plain(self) -> httpx.AsyncClient:
        if self._plain is None:
            self._plain = make_client(self.timeout)
        return self._plain

    @property
    def browser(self) -> Any:
        if self._browser is None:
            self._browser = make_browser_client(self.timeout)
        return self._browser

    def for_source(self, source: type[Source]) -> Any:
        return self.browser if source.needs_browser_tls else self.plain

    async def aclose(self) -> None:
        if self._plain is not None:
            await self._plain.aclose()
        if self._browser is not None:
            await self._browser.close()
