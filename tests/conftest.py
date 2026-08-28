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

"""Shared test fixtures and fakes. No real network or subprocess (PY-TEST-002)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from utils.lchroot.config import ApiConfig
from utils.lchroot.types import ResolvedImage


class FakeTransport:
    """In-memory Transport: returns canned payloads by URL suffix, records calls."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        # Additive header capture: keeps `.calls` shape (method, url) so existing
        # `for _, url in transport.calls` unpacking still works, while letting tests
        # assert the auth header sent on each request.
        self.headers: list[tuple[str, str, dict[str, str]]] = []

    def post_json(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("POST", url))
        self.headers.append(("POST", url, dict(headers or {})))
        return self._match(url)

    def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append(("GET", url))
        self.headers.append(("GET", url, dict(headers or {})))
        return self._match(url)

    def _match(self, url: str) -> dict[str, Any]:
        for suffix, payload in self.responses.items():
            if url.endswith(suffix):
                if isinstance(payload, Exception):
                    raise payload
                return cast("dict[str, Any]", payload)
        raise AssertionError(f"no fake response registered for {url}")


@pytest.fixture
def api_config() -> ApiConfig:
    return ApiConfig(username="luna", password="secret", endpoint="ctrl:7050")


@pytest.fixture
def image_root(tmp_path: Path) -> Path:
    """A minimal fake rootfs that passes resolve_image validation."""
    (tmp_path / "usr").mkdir()
    (tmp_path / "tmp").mkdir()
    return tmp_path


@pytest.fixture
def resolved_image(image_root: Path) -> ResolvedImage:
    return ResolvedImage(
        name="compute-image-bubble", path=image_root, kernelversion="5.14.0-x"
    )


def osimage_response(
    name: str, path: Path, kernelversion: str | None = "5.14.0-x"
) -> dict[str, Any]:
    return {
        "config": {
            "osimage": {name: {"path": str(path), "kernelversion": kernelversion}}
        }
    }
