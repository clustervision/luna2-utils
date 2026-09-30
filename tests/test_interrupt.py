# This code is part of the TrinityX software suite
# Copyright (C) 2026  ClusterVision Solutions b.v.
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

"""Tests for the shared Ctrl-C handling (TRIX-1962).

Pin the contract of utils.utils.interrupt.exit_on_interrupt: a normal run passes its
return value through, Ctrl-C becomes one line on stderr and exit 130, and nothing else
is swallowed. Then check that each tool's entry point actually goes through it.
"""

from __future__ import annotations

import importlib

import pytest

from utils.utils.interrupt import INTERRUPT_EXIT_CODE, INTERRUPT_MESSAGE, exit_on_interrupt


def test_passes_arguments_and_return_value_through() -> None:
    assert exit_on_interrupt(lambda a, b=0: a + b, 2, b=3) == 5


def test_keyboard_interrupt_exits_130_with_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exc:
        exit_on_interrupt(interrupted)
    assert exc.value.code == INTERRUPT_EXIT_CODE == 130
    err = capsys.readouterr().err
    assert err == f'\n{INTERRUPT_MESSAGE}\n'
    assert 'Traceback' not in err


def test_other_exceptions_are_not_swallowed() -> None:
    def broken() -> None:
        raise ValueError('boom')

    with pytest.raises(ValueError, match='boom'):
        exit_on_interrupt(broken)


def test_tool_exit_codes_are_kept() -> None:
    def exits_three() -> None:
        raise SystemExit(3)

    with pytest.raises(SystemExit) as exc:
        exit_on_interrupt(exits_three)
    assert exc.value.code == 3


# tools whose module can be imported without a controller (lpower, lexport and lmaster
# read luna.ini at import time, so they are covered by the on-controller check instead)
@pytest.mark.parametrize('module, deps', [
    ('utils.lnode', ['jwt', 'requests']),
    ('utils.lrack', ['argcomplete', 'hostlist', 'prettytable', 'termcolor']),
    ('utils.lconsole', ['termios', 'requests']),
    ('utils.trinity_diagnosis', ['termcolor']),
    ('utils.bootutil', ['requests']),
])
def test_tool_entry_point_handles_ctrl_c(module: str, deps: list[str],
                                         monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    for dep in deps:
        pytest.importorskip(dep)
    tool = importlib.import_module(module)

    def interrupted(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(tool, '_main', interrupted)
    with pytest.raises(SystemExit) as exc:
        tool.main()
    assert exc.value.code == 130
    assert capsys.readouterr().err == f'\n{INTERRUPT_MESSAGE}\n'
