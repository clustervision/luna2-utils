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

"""Host-side mutual-exclusion lock for an image, managed outside the sandbox.

lchroot-legacy kept a lock at ``<image>/tmp/lchroot.lock``; lchroot keeps the equivalent
at ``<image>/tmp/lchroot.lock`` but, unlike the legacy tool, detects a stale lock left by
a dead holder and reclaims it automatically (PY-CORE-004 re-entrancy). The lock lives on
the host filesystem, not inside the namespace.

Beyond acquire/release this module can also *inspect* and *terminate* a holder
(:meth:`ImageLock.read_holder` / :meth:`ImageLock.terminate_holder`), backing the
``--status`` command and the ``--force`` entry path. Stopping a holder tears its whole
process tree down — the outer ``bwrap`` dying collapses the ``--unshare-pid`` namespace —
before dropping the lock, so no orphaned session is left racing the image's rpmdb. When
``--force`` breaks a live lock it goes through :meth:`terminate_holder` (stop the holder
first), never the low-level ``acquire(force=True)`` *steal* primitive (reclaim without
stopping the holder), which remains only for completeness and tests — a running session is
never stolen out from under itself. See ADRs 0002, 0003, 0012 and 0014.
"""

import logging
import os
import re
import signal
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .errors import LockedError

log = logging.getLogger(__name__)

_LOCK_RELPATH = ("tmp", "lchroot.lock")
_HOLDER_RE = re.compile(r"PID\s+(\d+)\s+on\s+(.*)")
# Package managers whose presence in the holder tree makes a kill rpmdb-risky.
_PKG_MANAGERS = ("dnf", "yum", "rpm", "apt", "apt-get", "dpkg", "zypper")


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def _current_tty() -> str:
    """Return the controlling tty name, or 'unknown' if there is none."""
    try:
        return os.ttyname(sys.stdin.fileno())
    except OSError:
        return "unknown"


@dataclass(frozen=True, slots=True)
class LockHolder:
    """The session recorded in a lock file (parsed, with liveness resolved)."""

    pid: int | None
    tty: str
    alive: bool
    raw: str


@dataclass(frozen=True, slots=True)
class ProcInfo:
    """One process in a holder's tree, for display and kill ordering."""

    pid: int
    depth: int
    cmdline: str


@dataclass(frozen=True, slots=True)
class KillReport:
    """Outcome of terminating a lock holder (PY-CORE-004 observable result)."""

    holder: LockHolder | None
    signalled: tuple[int, ...]
    package_manager: str | None
    escalated: bool  # True if a SIGKILL was needed after SIGTERM
    stale: bool  # True if the holder was already dead (lock just cleared)


def _parse_holder(text: str) -> LockHolder:
    """Parse a lock file's contents into a :class:`LockHolder`."""
    raw = text.strip()
    match = _HOLDER_RE.search(raw)
    pid = int(match.group(1)) if match else None
    tty = match.group(2).strip() if match else "unknown"
    alive = pid is not None and _pid_alive(pid)
    return LockHolder(pid=pid, tty=tty, alive=alive, raw=raw)


def _ppid_from_stat(stat: str) -> int | None:
    """Parse the parent PID out of a ``/proc/<pid>/stat`` line.

    The line is ``pid (comm) state ppid ...`` and the kernel does **not** escape spaces
    or parens in ``comm`` — the classic parsing trap. Splitting *after* the final ``)``
    is correct because every field past ``comm`` is numeric/single-char (no ``)``), so
    the last ``)`` always closes ``comm``; the remaining fields are ``state ppid ...``.
    Pure (no I/O) so it can be exhaustively property-tested against hostile ``comm``
    values (PY-TEST-006).
    """
    rparen = stat.rfind(")")
    fields = stat[rparen + 1 :].split()
    return int(fields[1]) if len(fields) > 1 else None


def _proc_ppid(pid: int) -> int | None:
    """Return the parent PID from ``/proc/<pid>/stat``, or None if the pid is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return _ppid_from_stat(stat)


def _proc_cmdline(pid: int) -> str:
    """Return a process's command line (NUL-separated argv joined with spaces)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def holder_tree(root_pid: int) -> list[ProcInfo]:
    """Return ``root_pid`` and all descendants, parent-before-child, depth-annotated.

    Built from a single ``/proc`` scan (pure stdlib, Linux-only — as is bwrap).
    """
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        ppid = _proc_ppid(int(entry.name))
        if ppid is not None:
            children.setdefault(ppid, []).append(int(entry.name))

    out: list[ProcInfo] = []
    seen: set[int] = set()
    stack: list[tuple[int, int]] = [(root_pid, 0)]
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(ProcInfo(pid=pid, depth=depth, cmdline=_proc_cmdline(pid)))
        # push children reversed so the natural order is preserved on pop
        stack.extend((child, depth + 1) for child in reversed(children.get(pid, [])))
    return out


def _detect_package_manager(tree: Iterable[ProcInfo]) -> str | None:
    """Return the first package-manager name found running in ``tree``, else None."""
    for proc in tree:
        argv0 = proc.cmdline.split()[0] if proc.cmdline else ""
        name = Path(argv0).name
        if name in _PKG_MANAGERS:
            return name
    return None


def running_package_manager(root_pid: int) -> str | None:
    """Return the package manager (dnf/rpm/...) running in ``root_pid``'s tree, if any.

    Used to warn before killing a holder: stopping ``dnf``/``rpm`` mid-transaction can
    leave the image's rpmdb inconsistent.
    """
    return _detect_package_manager(holder_tree(root_pid))


def _signal_tree(pids: Iterable[int], sig: int) -> None:
    """Send ``sig`` to each pid, ignoring ones that have already exited."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except PermissionError as exc:  # pragma: no cover - root in practice
            log.warning("cannot signal PID %d: %s", pid, exc)


class ImageLock:
    """Context manager for the per-image host-side lock."""

    def __init__(self, image_path: Path, *, name: str | None = None) -> None:
        """Bind the lock to an image rootfs path.

        Args:
            image_path: the image root; the lock file is ``<path>/tmp/lchroot.lock``.
            name: the image's display name (the token the operator passes to
                ``--status``/``--force``); defaults to the path basename. Used only to
                make the :class:`LockedError` message actionable — for a luna osimage
                this is the osimage name, which may differ from the path basename.
        """
        self._file = image_path.joinpath(*_LOCK_RELPATH)
        self._name = name or image_path.name
        self._owned = False

    def read_holder(self) -> LockHolder | None:
        """Return the current lock holder, or None if the image is not locked."""
        if not self._file.exists():
            return None
        return _parse_holder(self._file.read_text(encoding="utf-8"))

    def acquire(self, *, force: bool = False) -> None:
        """Acquire the lock, refusing only if another *live* session holds it.

        A stale lock (holder PID no longer running) is reclaimed automatically with a
        warning — no flag needed — so a crashed session never blocks the next run.
        ``force`` steals the lock from a live holder *without stopping it*; the CLI does
        not use it — to break a live lock, ``--force`` stops the holder first via
        :meth:`terminate_holder` and only then acquires.

        Args:
            force: reclaim even if the holder still appears alive.

        Raises:
            LockedError: if a live session holds the lock and ``force`` is False.
        """
        holder = self.read_holder()
        if holder is not None:
            if holder.alive and not force:
                raise LockedError(
                    f"image {self._name!r} is locked ({holder.raw}); "
                    f"inspect with `lchroot --status {self._name}` "
                    f"or break it with `lchroot --force {self._name}`"
                )
            if holder.alive:
                log.warning(
                    "forcibly reclaiming lock held by a live process (%s) — "
                    "it keeps running; prefer --force (which stops it first)",
                    holder.raw,
                )
            else:
                log.warning("reclaiming stale lock from dead holder (%s)", holder.raw)

        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            f"PID {os.getpid()} on {_current_tty()}\n", encoding="utf-8"
        )
        self._owned = True

    def terminate_holder(
        self, *, grace_s: float = 3.0, poll_s: float = 0.2
    ) -> KillReport:
        """Stop the lock holder's whole process tree, then drop the lock.

        SIGTERM the tree (children first), wait up to ``grace_s`` for it to exit, then
        SIGKILL any survivor. A dead/absent holder just clears the lock (re-entrant,
        PY-CORE-004). Logs a loud warning first if a package manager is mid-run, since
        killing ``dnf``/``rpm`` can leave the image's rpmdb inconsistent.

        Returns:
            A :class:`KillReport` describing what was signalled.
        """
        holder = self.read_holder()
        if holder is None:
            return KillReport(None, (), None, escalated=False, stale=False)
        if holder.pid is None or not holder.alive:
            self._file.unlink(missing_ok=True)
            return KillReport(holder, (), None, escalated=False, stale=True)

        tree = holder_tree(holder.pid)
        pids = [p.pid for p in tree]
        pkg = _detect_package_manager(tree)
        if pkg is not None:
            log.warning(
                "%s is running inside the held image; killing it may corrupt the "
                "rpmdb — consider letting it finish",
                pkg,
            )

        _signal_tree(reversed(pids), signal.SIGTERM)  # children before parents
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if not any(_pid_alive(pid) for pid in pids):
                break
            time.sleep(poll_s)

        survivors = [pid for pid in pids if _pid_alive(pid)]
        if survivors:
            log.warning("SIGTERM did not stop %s; escalating to SIGKILL", survivors)
            _signal_tree(reversed(survivors), signal.SIGKILL)

        self._file.unlink(missing_ok=True)
        return KillReport(
            holder, tuple(pids), pkg, escalated=bool(survivors), stale=False
        )

    def release(self) -> None:
        """Remove the lock if (and only if) this process owns it."""
        if self._owned:
            self._file.unlink(missing_ok=True)
            self._owned = False

    def __enter__(self) -> "ImageLock":
        """Enter the context (the lock is acquired explicitly via :meth:`acquire`)."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the lock on context exit, even on error."""
        self.release()
