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

"""Central command executor: the only module that calls :mod:`subprocess`.

Provides a structured result, dry-run, fake mode for tests, the result-vs-best-effort
split (PY-ERR-006), and tty-passthrough for interactive children (skill.md L5).
No other module may call subprocess directly (PY-CORE-005).
"""

import logging
import shutil
import subprocess  # nosec B404 - subprocess is intentional; this is the one sanctioned call site (PY-CORE-005)
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .errors import LchrootError

log = logging.getLogger(__name__)


class ExecError(LchrootError):
    """An external command failed in a way the caller did not expect."""


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Outcome of one external command invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float

    @property
    def ok(self) -> bool:
        """Return True iff the command exited zero."""
        return self.returncode == 0


@dataclass(slots=True)
class Executor:
    """Run external commands. The single sanctioned subprocess call site.

    Args:
        dry_run: when True, log intent and execute nothing.
        fake: canned results keyed by argv tuple, for tests (bypasses subprocess).
    """

    dry_run: bool = False
    fake: dict[tuple[str, ...], ExecResult] = field(default_factory=dict)

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> ExecResult:
        """Run a command and return its captured result.

        Args:
            argv: command and arguments as a list (never a shell string).
            env: process environment, or None to inherit.
            cwd: working directory, or None.
            timeout_s: timeout in seconds, or None.
            check: raise :class:`ExecError` on non-zero exit. Set False when a
                non-zero exit is a valid observable state (the ``cmd || true`` case).

        Returns:
            The :class:`ExecResult`.

        Raises:
            ExecError: on non-zero exit when ``check`` is True, or on timeout.
        """
        key = tuple(argv)
        if key in self.fake:
            return self.fake[key]
        if self.dry_run:
            log.info("DRY-RUN would execute: %s", " ".join(key))
            return ExecResult(key, 0, "", "", 0.0)
        exe = shutil.which(argv[0]) or argv[0]
        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 # nosec B603 - list argv, shell=False, inputs validated upstream
                [exe, *argv[1:]],
                env=dict(env) if env is not None else None,
                cwd=cwd,
                timeout=timeout_s,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecError(
                f"command timed out after {timeout_s}s: {' '.join(key)}"
            ) from exc
        result = ExecResult(
            key, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start
        )
        log.debug(
            "ran %s -> rc=%d (%.3fs)",
            " ".join(key),
            result.returncode,
            result.duration_s,
        )
        if check and not result.ok:
            raise ExecError(
                f"command failed (rc={result.returncode}): {' '.join(key)}\n{result.stderr}"
            )
        return result

    def run_best_effort(self, argv: Sequence[str]) -> None:
        """Run a command for its side effect, never raising (cleanup / HA sync).

        The ``cmd || true`` case made explicit (PY-ERR-006): failures are logged.
        """
        try:
            self.run(argv, check=True)
        except (ExecError, OSError) as exc:
            log.warning(
                "best-effort command failed (ignored): %s: %s", " ".join(argv), exc
            )

    def run_interactive(
        self, argv: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> int:
        """Run a child that inherits the tty (interactive shell); return its exit code.

        Does not capture stdout/stderr - capturing would break an interactive shell
        (skill.md L5). Use only for the sandbox entry path, never in unit tests.
        """
        key = tuple(argv)
        if self.dry_run:
            log.info("DRY-RUN would exec interactively: %s", " ".join(key))
            return 0
        exe = shutil.which(argv[0]) or argv[0]
        proc = subprocess.run(  # noqa: S603 # nosec B603 - interactive entry; argv is a list, shell=False
            [exe, *argv[1:]], env=dict(env) if env is not None else None, check=False
        )
        return proc.returncode
