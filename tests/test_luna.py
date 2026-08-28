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

"""Tests for the Luna API client, driven by FakeTransport (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeTransport, osimage_response
from utils.lchroot.config import ApiConfig
from utils.lchroot.errors import ImageResolveError, LunaError
from utils.lchroot.luna import LunaClient

CFG = ApiConfig(username="luna", password="p", endpoint="ctrl:7050")


def _client(responses: dict[str, object]) -> tuple[LunaClient, FakeTransport]:
    transport = FakeTransport(responses)
    return LunaClient(CFG, transport), transport


def test_token_is_fetched_once_and_cached() -> None:
    client, transport = _client({"/token": {"token": "ABC"}})
    assert client.token == "ABC"
    assert client.token == "ABC"
    assert sum(1 for c in transport.calls if c[0] == "POST") == 1


def test_missing_token_raises() -> None:
    client, _ = _client({"/token": {"nope": 1}})
    with pytest.raises(LunaError, match="no token"):
        _ = client.token


def test_ha_state_parsed() -> None:
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/ha/state": {"ha": {"enabled": True, "master": False}},
        }
    )
    ha = client.ha_state()
    assert ha.enabled is True
    assert ha.master is False


def test_resolve_valid_image(image_root: Path) -> None:
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/cib": osimage_response("cib", image_root),
            # resolve_image now also fetches the collection to reject ambiguous
            # (shared-path) targets; a single-entry collection is unambiguous.
            "/config/osimage": osimage_response("cib", image_root),
        }
    )
    image = client.resolve_image("cib")
    assert image.path == image_root
    assert image.kernelversion == "5.14.0-x"


def test_resolve_unknown_image_raises() -> None:
    client, _ = _client(
        {"/token": {"token": "T"}, "/config/osimage/ghost": {"config": {"osimage": {}}}}
    )
    with pytest.raises(ImageResolveError, match="unknown osimage"):
        client.resolve_image("ghost")


def test_resolve_unsafe_root_path_refused() -> None:
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/bad": osimage_response("bad", Path("/")),
        }
    )
    with pytest.raises(ImageResolveError, match="unsafe image path"):
        client.resolve_image("bad")


def test_resolve_non_rootfs_refused(tmp_path: Path) -> None:
    # directory exists but has no usr/ -> not a rootfs
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/nr": osimage_response("nr", tmp_path),
        }
    )
    with pytest.raises(ImageResolveError, match="not a rootfs"):
        client.resolve_image("nr")


def test_resolve_missing_kernelversion_is_none(image_root: Path) -> None:
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/nk": osimage_response(
                "nk", image_root, kernelversion="null"
            ),
            "/config/osimage": osimage_response("nk", image_root, kernelversion="null"),
        }
    )
    assert client.resolve_image("nk").kernelversion is None


def test_list_images_returns_sorted_names() -> None:
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage": {"config": {"osimage": {"b-img": {}, "a-img": {}}}},
        }
    )
    assert client.list_images() == ["a-img", "b-img"]


def test_list_images_empty_when_daemon_errors() -> None:
    client, _ = _client(
        {"/token": {"token": "T"}, "/config/osimage": LunaError("down")}
    )
    assert client.list_images() == []


def test_ha_state_carries_auth_token_header() -> None:
    # The follow-up GET must carry the token under `x-access-tokens`; the POST /token
    # that mints it is itself unauthenticated.
    client, transport = _client(
        {
            "/token": {"token": "TKN"},
            "/ha/state": {"ha": {"enabled": False, "master": False}},
        }
    )
    client.ha_state()
    token_posts = [
        h for h in transport.headers if h[0] == "POST" and h[1].endswith("/token")
    ]
    ha_gets = [
        h for h in transport.headers if h[0] == "GET" and h[1].endswith("/ha/state")
    ]
    assert token_posts
    assert token_posts[0][2].get("x-access-tokens") is None  # /token is unauthenticated
    assert ha_gets
    assert ha_gets[0][2].get("x-access-tokens") == "TKN"  # follow-up GET carries it


def test_resolve_relative_path_refused() -> None:
    # A non-absolute path is unsafe: refuse rather than resolve it against the CWD.
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/rel": osimage_response("rel", Path("trinity/images/x")),
        }
    )
    with pytest.raises(ImageResolveError, match="unsafe image path"):
        client.resolve_image("rel")


def test_resolve_refuses_ambiguous_shared_path(image_root: Path) -> None:
    # Two osimages mapping to one path is a luna misconfiguration; entering it would
    # be an ambiguous destructive target, so resolve_image must refuse (regression A6).
    shared = str(image_root)
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/a": osimage_response("a", image_root),
            "/config/osimage": {
                "config": {
                    "osimage": {
                        "a": {"path": shared, "kernelversion": "5.14.0-x"},
                        "b": {"path": shared, "kernelversion": "5.14.0-x"},
                    }
                }
            },
        }
    )
    with pytest.raises(ImageResolveError, match="ambiguous"):
        client.resolve_image("a")


def test_resolve_unaffected_when_collection_fetch_fails(image_root: Path) -> None:
    # The ambiguity check is best-effort: a failed collection fetch must NOT turn a
    # valid resolve into a failure (a daemon hiccup can't block every entry).
    client, _ = _client(
        {
            "/token": {"token": "T"},
            "/config/osimage/cib": osimage_response("cib", image_root),
            "/config/osimage": LunaError("collection unavailable"),
        }
    )
    assert client.resolve_image("cib").path == image_root
