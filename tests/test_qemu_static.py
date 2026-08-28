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

"""Tests for utils.qemu_static (foreign-arch qemu-user + binfmt registry)."""

import os
import types
from pathlib import Path

import pytest

from utils.qemu_static import registry
from utils.qemu_static.registry import QemuError


def _elf(e_machine_le: bytes) -> bytes:
    """A minimal 20-byte little-endian 64-bit ELF header with the given e_machine."""
    return b"\x7fELF\x02\x01\x01" + b"\x00" * 11 + e_machine_le


def test_arch_from_elf_x86_64() -> None:
    assert registry._arch_from_elf(_elf(b"\x3e\x00")) == "x86_64"


def test_arch_from_elf_aarch64() -> None:
    assert registry._arch_from_elf(_elf(b"\xb7\x00")) == "aarch64"


def test_arch_from_elf_non_elf() -> None:
    assert registry._arch_from_elf(b"#!/bin/sh\n#padding-bytes") is None


def test_arch_from_elf_too_short() -> None:
    assert registry._arch_from_elf(b"\x7fELF") is None


def test_arch_from_elf_unknown_machine() -> None:
    assert registry._arch_from_elf(_elf(b"\x99\x00")) is None


def test_host_arch_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(machine="arm64"))
    assert registry.host_arch() == "aarch64"


def test_detect_image_arch_reads_systemd(tmp_path: Path) -> None:
    systemd = tmp_path / "usr/lib/systemd/systemd"
    systemd.parent.mkdir(parents=True)
    systemd.write_bytes(_elf(b"\xb7\x00"))
    assert registry.detect_image_arch(tmp_path) == "aarch64"


def test_detect_image_arch_none_when_no_binary(tmp_path: Path) -> None:
    (tmp_path / "usr/bin").mkdir(parents=True)
    assert registry.detect_image_arch(tmp_path) is None


def test_detect_image_arch_ignores_symlink_escaping_rootfs(tmp_path: Path) -> None:
    binary = tmp_path / "usr/lib/systemd/systemd"
    binary.parent.mkdir(parents=True)
    binary.symlink_to("/bin/true")  # absolute symlink to a host path
    assert registry.detect_image_arch(tmp_path) is None


def test_ensure_for_arch_native_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "host_arch", lambda: "x86_64")
    assert registry.ensure_for_arch("x86_64") is None


def test_ensure_for_arch_unsupported_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "host_arch", lambda: "x86_64")
    with pytest.raises(QemuError, match="unsupported"):
        registry.ensure_for_arch("sparc64")


def test_ensure_for_arch_skips_when_already_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(registry, "_is_registered", lambda _n: True)
    calls: list[Path] = []
    monkeypatch.setattr(registry, "_register", lambda _a, i: calls.append(i))
    assert registry.ensure_for_arch("aarch64") == "aarch64"
    assert calls == []


def test_ensure_for_arch_missing_qemu_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(registry, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(registry, "_is_registered", lambda _n: False)
    monkeypatch.setattr(registry, "_bundled_qemu", lambda _a: tmp_path / "absent")
    with pytest.raises(QemuError, match="no bundled"):
        registry.ensure_for_arch("aarch64")


def test_ensure_for_arch_registers_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(registry, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(registry, "_is_registered", lambda _n: False)
    qemu = tmp_path / "qemu-aarch64-static"
    qemu.write_bytes(_elf(b"\x3e\x00"))
    monkeypatch.setattr(registry, "_bundled_qemu", lambda _a: qemu)
    registered: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        registry, "_register", lambda a, i: registered.append((a.name, i))
    )
    assert registry.ensure_for_arch("aarch64") == "aarch64"
    assert registered == [("aarch64", qemu)]


def test_ensure_for_image_native(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(registry, "detect_image_arch", lambda _p: None)
    assert registry.ensure_for_image(tmp_path) is None


def test_ensure_for_image_foreign_delegates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(registry, "detect_image_arch", lambda _p: "aarch64")
    monkeypatch.setattr(registry, "ensure_for_arch", lambda a: a)
    assert registry.ensure_for_image(tmp_path) == "aarch64"


def test_supported_arches() -> None:
    # riscv64 was added with its interpreter but this expectation was not updated,
    # so the delivered suite failed on its own tree. Derived from the shipped
    # interpreters rather than restated, so the next architecture cannot drift.
    shipped = {
        path.name.removeprefix("qemu-").removesuffix("-static")
        for path in (Path(registry.__file__).parent).glob("qemu-*-static")
    }
    assert set(registry.supported_arches()) == shipped
