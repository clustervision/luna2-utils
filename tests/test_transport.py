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

"""Tests for the requests-backed transport's response decoding (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests

from utils.lchroot.errors import LunaError
from utils.lchroot.transport import RequestsTransport


class FakeResp:
    """Duck-typed stand-in for requests.Response."""

    def __init__(self, status: int, payload: Any, *, valid_json: bool = True) -> None:
        self.status_code = status
        self._payload = payload
        self._valid = valid_json
        self.request = SimpleNamespace(method="GET")
        self.url = "http://ctrl/endpoint"

    def json(self) -> Any:
        if not self._valid:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Duck-typed stand-in for requests.Session that returns a fixed response."""

    def __init__(self, resp: FakeResp) -> None:
        self.resp = resp
        self.calls: list[tuple[str, str]] = []

    def post(self, url: str, **_kw: Any) -> FakeResp:
        self.calls.append(("POST", url))
        return self.resp

    def get(self, url: str, **_kw: Any) -> FakeResp:
        self.calls.append(("GET", url))
        return self.resp


def test_decode_ok() -> None:
    assert RequestsTransport._decode(FakeResp(200, {"a": 1})) == {"a": 1}  # type: ignore[arg-type]  # duck-typed Response


def test_decode_bad_status_raises() -> None:
    with pytest.raises(LunaError, match="503"):
        RequestsTransport._decode(FakeResp(503, {}))  # type: ignore[arg-type]


def test_decode_non_json_raises() -> None:
    with pytest.raises(LunaError, match="non-JSON"):
        RequestsTransport._decode(FakeResp(200, None, valid_json=False))  # type: ignore[arg-type]


@pytest.mark.parametrize("status", [201, 202])
def test_decode_accepts_curated_2xx(status: int) -> None:
    # the accepted set is exactly {200, 201, 202}; _decode gates on the status.
    assert RequestsTransport._decode(FakeResp(status, {"ok": status})) == {"ok": status}  # type: ignore[arg-type]


@pytest.mark.parametrize("status", [204, 302, 404])
def test_decode_rejects_uncurated_status(status: int) -> None:
    # Outside the curated success set → raise. 204 is excluded on purpose (no body, can
    # never decode as JSON, finding D12); 302 is NOT a 2xx; 404 is the osimage-not-found
    # case the resolver depends on.
    with pytest.raises(LunaError, match=str(status)):
        RequestsTransport._decode(FakeResp(status, {}))  # type: ignore[arg-type]


def test_post_and_get_delegate_to_session() -> None:
    session = FakeSession(FakeResp(200, {"token": "T"}))
    transport = RequestsTransport(verify=False, session=session)  # type: ignore[arg-type]  # duck-typed Session
    assert transport.post_json("http://ctrl/token", json={"u": 1}) == {"token": "T"}
    assert transport.get_json("http://ctrl/ha") == {"token": "T"}
    assert [c[0] for c in session.calls] == ["POST", "GET"]


class UnreachableSession:
    """A session whose every call fails like an unreachable controller."""

    def post(self, url: str, **_kw: Any) -> Any:
        raise requests.exceptions.ConnectionError("connection refused")

    def get(self, url: str, **_kw: Any) -> Any:
        raise requests.exceptions.ConnectionError("connection refused")


@pytest.mark.parametrize("call", ["get", "post"])
def test_network_failure_becomes_luna_error(call: str) -> None:
    # A down/unreachable controller (RequestException) must surface as a typed LunaError
    # (-> exit 7) naming the URL, NOT escape as a raw OSError traceback. This is the
    # transport "single seam" that keeps requests from leaking past it.
    transport = RequestsTransport(verify=False, session=UnreachableSession())  # type: ignore[arg-type]

    def invoke() -> dict[str, Any]:
        if call == "get":
            return transport.get_json("http://ctrl/x")
        return transport.post_json("http://ctrl/x", json={})

    with pytest.raises(LunaError, match=r"cannot reach Luna API at http://ctrl/x"):
        invoke()
