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

"""Tests for the central executor (fake mode, dry-run, error handling)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from utils.lchroot.errors import LchrootError
from utils.lchroot.executor import ExecError, ExecResult, Executor


def test_fake_mode_returns_canned_result() -> None:
    canned = ExecResult(("bwrap", "true"), 0, "out", "", 0.0)
    ex = Executor(fake={("bwrap", "true"): canned})
    assert ex.run(["bwrap", "true"]) is canned


def test_dry_run_executes_nothing(tmp_path: Path) -> None:
    # Use a harmless sentinel, NOT `rm -rf /`: if dry-run ever regressed (exactly what this
    # test guards), a real payload would run — and the suite runs as root on a controller
    # (finding D3). Assert the side effect did NOT happen, which also strengthens the check.
    victim = tmp_path / "must-not-be-created"
    ex = Executor(dry_run=True)
    result = ex.run(["touch", str(victim)])
    assert result.ok
    assert result.returncode == 0
    assert not victim.exists()  # dry-run must not have touched the filesystem


def test_real_run_success() -> None:
    result = Executor().run(["true"])
    assert result.ok


def test_real_run_nonzero_raises_when_checked() -> None:
    with pytest.raises(ExecError, match="failed"):
        Executor().run(["false"])


def test_nonzero_is_a_state_when_check_false() -> None:
    # the `cmd || true` case: non-zero is a valid observable answer, not an error
    result = Executor().run(["false"], check=False)
    assert not result.ok
    assert result.returncode != 0


def test_best_effort_swallows_failure() -> None:
    Executor().run_best_effort(["false"])  # must not raise


def test_interactive_dry_run_returns_zero() -> None:
    assert Executor(dry_run=True).run_interactive(["bwrap", "bash"]) == 0


def test_result_ok_property() -> None:
    assert ExecResult(("x",), 0, "", "", 0.0).ok
    assert not ExecResult(("x",), 1, "", "", 0.0).ok


def test_exec_error_is_a_lchroot_error() -> None:
    # so the top-level boundary's `except LchrootError` maps it to a clean exit code
    assert issubclass(ExecError, LchrootError)


def test_best_effort_swallows_missing_binary() -> None:
    # a non-existent binary raises FileNotFoundError (OSError); best-effort eats it
    Executor().run_best_effort(["definitely-not-a-real-binary-xyz"])  # must not raise


def test_run_timeout_raises_exec_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A command that overruns its timeout must surface as a typed ExecError (bounded), not
    # hang the caller. Drive the seam by faking subprocess.run -> TimeoutExpired (no sleep).
    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="bwrap", timeout=0.5)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ExecError, match=r"timed out after 0\.5s"):
        Executor().run(["bwrap", "sleep", "9999"], timeout_s=0.5)
