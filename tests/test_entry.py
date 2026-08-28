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

"""Tests for main() exit-code mapping (the lchroot compatibility contract).

The exit-code contract (why each code exists — callers/scripts branch on these, and none
may ever leak as a raw traceback):

* 7  config/luna/image-resolve = the oracle's "bad input" class (ConfigError, LunaError).
* 8  foreign-arch emulation could not be arranged (EmulationError) — a class the bash
     oracle never had (ADR 0009).
* 9  image locked / in use (LockedError) — the oracle's second code.
* 10 refused rw on a non-master HA controller (SecondaryControllerError, ADR 0011) — kept
     distinct from "locked" (9) and "bad input" (7).
* 1  generic "the tool broke" for typed errors with no dedicated code (ExecError,
     SandboxError) and for the unforeseen-OSError boundary backstop (e.g. an unwritable
     lock file) — caught so the operator sees a clean message, not a trace.
* 130 clean interrupt (KeyboardInterrupt / EOFError, PY-ERR-007) — the rule was added after
     a raw traceback on Ctrl-C; both are BaseException so they need an explicit catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.lchroot import __main__ as m
from utils.lchroot.config import ApiConfig
from utils.lchroot.errors import (
    ConfigError,
    EmulationError,
    LockedError,
    LunaError,
    SandboxError,
    SecondaryControllerError,
)
from utils.lchroot.executor import ExecError

CFG = ApiConfig(username="u", password="p", endpoint="h:1")

# (exception raised by run(), expected exit code) — the full mapping table, one row per
# rationale line in the module docstring. See there for WHY each code.
_RUN_RAISES: list[tuple[BaseException, int]] = [
    (LunaError("api down"), 7),
    (EmulationError("no qemu-aarch64-static"), 8),
    (LockedError("busy"), 9),
    (SecondaryControllerError("not the master"), 10),
    (ExecError("bwrap failed"), 1),
    (SandboxError("cannot launch the sandbox; is bubblewrap installed?"), 1),
    (PermissionError(13, "Permission denied", "/img/tmp/lchroot.lock"), 1),
    (KeyboardInterrupt(), 130),
    (EOFError(), 130),
]


@pytest.mark.parametrize(
    ("error", "code"),
    _RUN_RAISES,
    ids=[type(e).__name__ for e, _ in _RUN_RAISES],
)
def test_run_error_maps_to_exit_code(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, code: int
) -> None:
    # Whatever run() raises, main() must map it to the contracted code (never a traceback).
    monkeypatch.setattr(m, "load_api_config", lambda: CFG)
    monkeypatch.setattr(m, "RequestsTransport", lambda **_kw: object())

    def boom(*_a: object, **_k: object) -> int:
        raise error

    monkeypatch.setattr(m, "run", boom)
    assert m.main(["cib"]) == code


def test_config_error_maps_to_7(monkeypatch: pytest.MonkeyPatch) -> None:
    # ConfigError is raised at load_api_config (before the client is built), so it has a
    # different injection point than the run() cases above — cover it separately.
    def boom() -> ApiConfig:
        raise ConfigError("bad luna.ini")

    monkeypatch.setattr(m, "load_api_config", boom)
    assert m.main(["cib"]) == 7


def test_success_returns_run_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The happy path returns run()'s own value, and --log-file / --debug are wired through.
    monkeypatch.setattr(m, "load_api_config", lambda: CFG)
    monkeypatch.setattr(m, "RequestsTransport", lambda **_kw: object())
    monkeypatch.setattr(m, "run", lambda *_a, **_k: 0)
    log_file = tmp_path / "lb.log"
    assert m.main(["--debug", "--log-file", str(log_file), "cib"]) == 0
