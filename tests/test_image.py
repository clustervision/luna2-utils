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

"""Tests for luna-free rootfs resolution (the --path mode). No network/subprocess.

Aim: --path must validate a rootfs and build a ResolvedImage exactly as strictly as luna
resolution, but touching only the filesystem (the whole point: no luna calls).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.lchroot.errors import ImageResolveError
from utils.lchroot.image import detect_kernelversion, resolve_local_path


def test_resolve_builds_image_from_directory(image_root: Path) -> None:
    img = resolve_local_path(str(image_root))
    assert img.path == image_root
    assert img.name == image_root.name  # basename becomes the sandbox hostname/prompt


def test_resolve_refuses_root() -> None:
    with pytest.raises(ImageResolveError, match="unsafe"):
        resolve_local_path("/")


def test_resolve_refuses_relative_path() -> None:
    with pytest.raises(ImageResolveError, match="unsafe"):
        resolve_local_path("relative/rootfs")


def test_resolve_refuses_dir_without_usr(tmp_path: Path) -> None:
    # a directory that is not a rootfs (no usr/) must be rejected, like luna resolution
    with pytest.raises(ImageResolveError, match="not a rootfs"):
        resolve_local_path(str(tmp_path))


def test_resolve_refuses_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ImageResolveError, match="does not exist"):
        resolve_local_path(str(tmp_path / "does-not-exist"))


def test_resolve_refuses_traversal_escaping_to_root(tmp_path: Path) -> None:
    # A '..' traversal that resolves to '/' must be refused, not textually accepted:
    # `<abs>/a/../../../../../..` collapses to '/' and would otherwise rw-bind the host
    # root into the sandbox (security regression guard, finding D1).
    escape = tmp_path / "a" / (".." + "/.." * 40)
    with pytest.raises(ImageResolveError, match=r"unsafe|does not exist"):
        resolve_local_path(str(escape))


def test_resolve_refuses_symlink_pointing_at_root(tmp_path: Path) -> None:
    # A symlink whose target is '/' must be refused after resolution (finding D1).
    link = tmp_path / "rootlink"
    link.symlink_to("/")
    with pytest.raises(ImageResolveError, match="resolves to /"):
        resolve_local_path(str(link))


def test_resolve_follows_symlink_to_real_rootfs(tmp_path: Path) -> None:
    # A symlink to a genuine rootfs resolves through and is accepted under the real name.
    real = tmp_path / "real-image"
    (real / "usr").mkdir(parents=True)
    link = tmp_path / "link-image"
    link.symlink_to(real)
    img = resolve_local_path(str(link))
    assert img.path == real  # resolved to the real target, not the link
    assert img.name == "real-image"


def test_detect_kernelversion_picks_numeric_greatest(tmp_path: Path) -> None:
    # 5.14 must win over 5.9 — a lexical sort would wrongly pick "5.9" (finding D10).
    modules = tmp_path / "lib" / "modules"
    modules.mkdir(parents=True)
    (modules / "5.9.0-1.el9.aarch64").mkdir()
    (modules / "5.14.0-2.el9.aarch64").mkdir()
    assert detect_kernelversion(tmp_path) == "5.14.0-2.el9.aarch64"


def test_detect_kernelversion_none_when_no_modules(tmp_path: Path) -> None:
    assert detect_kernelversion(tmp_path) is None


def test_resolve_reads_kernelversion_from_image(image_root: Path) -> None:
    (image_root / "lib" / "modules" / "6.1.0-test.aarch64").mkdir(parents=True)
    assert resolve_local_path(str(image_root)).kernelversion == "6.1.0-test.aarch64"


def test_resolve_kernelversion_none_propagates(image_root: Path) -> None:
    # no /lib/modules in the minimal image_root fixture -> None (uname reports host)
    assert resolve_local_path(str(image_root)).kernelversion is None
