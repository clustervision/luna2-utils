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

"""Tests for utils.lchroot.emulation (the thin adapter delegating to qemu_static)."""

from pathlib import Path

import pytest

from utils import qemu_static
from utils.lchroot import emulation
from utils.lchroot.errors import EmulationError
from utils.qemu_static import QemuError


def test_native_image_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qemu_static, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(qemu_static, "detect_image_arch", lambda _p: "x86_64")
    assert emulation.setup_emulation_for_entry(tmp_path, no_emulate=False) is None


def test_unknown_arch_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qemu_static, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(qemu_static, "detect_image_arch", lambda _p: None)
    assert emulation.setup_emulation_for_entry(tmp_path, no_emulate=False) is None


def test_no_emulate_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qemu_static, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(qemu_static, "detect_image_arch", lambda _p: "aarch64")
    with pytest.raises(EmulationError, match="no-emulate"):
        emulation.setup_emulation_for_entry(tmp_path, no_emulate=True)


def test_foreign_image_delegates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qemu_static, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(qemu_static, "detect_image_arch", lambda _p: "aarch64")
    delegated: list[str] = []

    def _record(arch: str) -> str:
        delegated.append(arch)
        return arch

    monkeypatch.setattr(qemu_static, "ensure_for_arch", _record)
    assert emulation.setup_emulation_for_entry(tmp_path, no_emulate=False) == "aarch64"
    assert delegated == ["aarch64"]


def test_qemu_error_becomes_emulation_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qemu_static, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(qemu_static, "detect_image_arch", lambda _p: "aarch64")

    def _boom(_arch: str) -> str:
        raise QemuError("no bundled qemu-aarch64-static")

    monkeypatch.setattr(qemu_static, "ensure_for_arch", _boom)
    with pytest.raises(EmulationError, match="no bundled"):
        emulation.setup_emulation_for_entry(tmp_path, no_emulate=False)
