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

"""Tests for the qemu-static CLI entrypoint (arg dispatch + exit codes).

Aim: the CLI was previously untested (finding D16). Drive main([...]) with the registry
seams monkeypatched so no host binfmt state is touched, and pin the exit-code contract:
0 ok, 1 QemuError, 130 interrupt, argparse-2 on a usage error.
"""

from __future__ import annotations

import pytest

from utils.qemu_static import __main__ as cli
from utils.qemu_static.registry import QemuError


def test_arch_dispatches_to_ensure_for_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_ensure_for_arch(arch: str) -> str:
        seen.append(arch)
        return arch

    monkeypatch.setattr(cli, "ensure_for_arch", fake_ensure_for_arch)
    assert cli.main(["aarch64"]) == 0
    assert seen == ["aarch64"]


def test_image_dispatches_to_ensure_for_image(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_ensure_for_image(root: object) -> str:
        seen.append(str(root))
        return "aarch64"

    monkeypatch.setattr(cli, "ensure_for_image", fake_ensure_for_image)
    assert cli.main(["--image", "/trinity/images/arm"]) == 0
    assert seen == ["/trinity/images/arm"]


def test_native_image_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # a native image -> ensure_for_image returns None (nothing to do), still exit 0
    monkeypatch.setattr(cli, "ensure_for_image", lambda _root: None)
    assert cli.main(["--image", "/trinity/images/x86"]) == 0


def test_neither_arch_nor_image_is_usage_error() -> None:
    # exactly-one-of ARCH/--image; giving neither is an argparse usage error (exit 2)
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_both_arch_and_image_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["aarch64", "--image", "/trinity/images/arm"])
    assert exc.value.code == 2


def test_qemu_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_arch: str) -> str:
        raise QemuError("no static interpreter bundled for sparc")

    monkeypatch.setattr(cli, "ensure_for_arch", boom)
    assert cli.main(["sparc"]) == 1


def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_arch: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "ensure_for_arch", interrupt)
    assert cli.main(["aarch64"]) == 130
