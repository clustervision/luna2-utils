# This code is part of the TrinityX software suite
# Copyright (C) 2026  ClusterVision Solutions b.v.
# Author: Alex Ninaber
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>

"""The one ``requests``-backed implementation of the :class:`~lchroot.luna.Transport`.

Isolating ``requests`` here keeps the dependency behind a single seam: the rest of
the package and every unit test depend only on the ``Transport`` Protocol, so they
need neither the network nor the library (PY-CORE-002, PY-TEST-002).
"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import requests
import urllib3

from .errors import LunaError

log = logging.getLogger(__name__)

_VALID_CODES = frozenset({200, 201, 202})


def silence_insecure_tls_warnings() -> None:
    """Suppress urllib3's per-request ``InsecureRequestWarning`` (process-global).

    lchroot talks to the controller's self-signed cert with ``verify=False`` (the bash
    oracle used ``curl --insecure``), which otherwise prints a warning on every call. This
    is a process-global side effect, so the **entrypoint** calls it once when verification
    is off — not a transport constructor (PY-FUNC-007).
    """
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RequestsTransport:
    """HTTP transport backed by ``requests`` (implements the Transport Protocol)."""

    def __init__(
        self, *, verify: bool, session: requests.Session, timeout_s: float = 30.0
    ) -> None:
        """Initialise the transport.

        Args:
            verify: TLS certificate verification (False mirrors lchroot's --insecure
                against the self-signed controller cert).
            session: a ``requests.Session`` to reuse.
            timeout_s: per-request timeout in seconds.
        """
        self._verify = verify
        self._session = session
        self._timeout = timeout_s

    def post_json(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST ``json`` to ``url`` and return the decoded JSON object."""
        with self._reach(url):
            response = self._session.post(
                url,
                json=dict(json),
                headers=dict(headers or {}),
                verify=self._verify,
                timeout=self._timeout,
            )
        return self._decode(response)

    def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """GET ``url`` and return the decoded JSON object."""
        with self._reach(url):
            response = self._session.get(
                url,
                headers=dict(headers or {}),
                verify=self._verify,
                timeout=self._timeout,
            )
        return self._decode(response)

    @staticmethod
    @contextmanager
    def _reach(url: str) -> Iterator[None]:
        """Translate any ``requests`` transport failure into :class:`LunaError`.

        This is the single seam that keeps ``requests`` from leaking: a connection
        refused, DNS failure, TLS error, or timeout (all ``RequestException``) becomes a
        typed ``LunaError`` so an unreachable controller exits 7 with a clear message
        instead of escaping ``main`` as a raw traceback. ``RequestException`` subclasses
        ``OSError``, so the best-effort luna callers that catch ``OSError`` still work.
        """
        try:
            yield
        except requests.exceptions.RequestException as exc:
            raise LunaError(f"cannot reach Luna API at {url}: {exc}") from exc

    @staticmethod
    def _decode(response: requests.Response) -> dict[str, Any]:
        """Validate the status code and return the decoded JSON body.

        The accepted set is the curated ``{200, 201, 202}`` (``_VALID_CODES``), not the
        full 2xx range: luna answers with these on success, and narrowing the set means an
        unexpected 2xx (e.g. a 204/206/207 from a proxy) is surfaced as an error rather than
        silently parsed. Everything else — including 3xx redirects and 404 — raises
        :class:`LunaError`. (204 is excluded on purpose: it carries no body, so it could
        never decode as JSON here anyway.) See test_transport for the pinned policy.
        """
        if response.status_code not in _VALID_CODES:
            raise LunaError(
                f"Luna API {response.request.method} {response.url} -> {response.status_code}"
            )
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise LunaError(f"Luna API returned non-JSON from {response.url}") from exc
        return payload
