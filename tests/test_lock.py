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

"""Re-entrancy tests for the host-side image lock (PY-TEST-004)."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import utils.lchroot.lock as lock_mod
from utils.lchroot.errors import LockedError
from utils.lchroot.lock import ImageLock, ProcInfo


def test_acquire_then_release_clean(image_root: Path) -> None:
    lock = ImageLock(image_root)
    lock.acquire()
    assert (image_root / "tmp" / "lchroot.lock").exists()
    lock.release()
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_context_manager_releases(image_root: Path) -> None:
    with ImageLock(image_root) as lock:
        lock.acquire()
        assert (image_root / "tmp" / "lchroot.lock").exists()
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_live_lock_blocks(image_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/0\n", encoding="utf-8"
    )
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)
    with pytest.raises(LockedError, match="is locked"):
        ImageLock(image_root).acquire()


def test_stale_lock_auto_reclaimed_without_force(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a dead holder's lock is reclaimed automatically (no --force, no error)
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/0\n", encoding="utf-8"
    )
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: False)
    lock = ImageLock(image_root)
    lock.acquire()  # must not raise
    lock.release()


def test_force_reclaims_even_a_live_lock(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/0\n", encoding="utf-8"
    )
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)
    lock = ImageLock(image_root)
    lock.acquire(force=True)  # must not raise despite a live holder
    lock.release()


def test_release_without_ownership_is_noop(image_root: Path) -> None:
    # someone else's lock present; we never acquired -> release leaves it alone
    other = image_root / "tmp" / "lchroot.lock"
    other.write_text("PID 1 on x\n", encoding="utf-8")
    ImageLock(image_root).release()
    assert other.exists()


def test_read_holder_parses_pid_and_tty(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/3\n", encoding="utf-8"
    )
    holder = ImageLock(image_root).read_holder()
    assert holder is not None
    assert holder.pid == 4242
    assert holder.tty == "/dev/pts/3"
    assert holder.alive is True


def test_read_holder_none_when_absent(image_root: Path) -> None:
    assert ImageLock(image_root).read_holder() is None


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        ("/usr/bin/dnf update", "dnf"),
        ("/usr/bin/rpm -q kernel", "rpm"),
        ("/bin/bash", None),
        ("", None),
    ],
)
def test_detect_package_manager(cmdline: str, expected: str | None) -> None:
    tree = [ProcInfo(1, 0, "bwrap"), ProcInfo(2, 1, cmdline)]
    assert lock_mod._detect_package_manager(tree) == expected


def test_holder_tree_includes_self_at_root() -> None:
    # Walk the real /proc for this test process: it must be the depth-0 root.
    tree = lock_mod.holder_tree(os.getpid())
    assert tree[0].pid == os.getpid()
    assert tree[0].depth == 0
    assert tree[0].cmdline  # the pytest process has a command line


def test_terminate_holder_no_lock_is_noop(image_root: Path) -> None:
    report = ImageLock(image_root).terminate_holder()
    assert report.holder is None
    assert report.signalled == ()


def test_terminate_holder_clears_stale_lock(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_file = image_root / "tmp" / "lchroot.lock"
    lock_file.write_text("PID 4242 on /dev/pts/0\n", encoding="utf-8")
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: False)
    report = ImageLock(image_root).terminate_holder()
    assert report.stale is True
    assert report.signalled == ()
    assert not lock_file.exists()


def test_terminate_holder_sigterms_tree_and_clears_lock(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_file = image_root / "tmp" / "lchroot.lock"
    lock_file.write_text("PID 4242 on /dev/pts/0\n", encoding="utf-8")
    alive = {4242, 4243}
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(
        lock_mod,
        "holder_tree",
        lambda _pid: [ProcInfo(4242, 0, "bwrap"), ProcInfo(4243, 1, "dnf update")],
    )
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        alive.discard(pid)  # SIGTERM stops them promptly

    monkeypatch.setattr("os.kill", fake_kill)
    report = ImageLock(image_root).terminate_holder()
    assert not lock_file.exists()
    assert report.package_manager == "dnf"
    assert set(report.signalled) == {4242, 4243}
    assert report.escalated is False
    assert (4242, signal.SIGTERM) in sent
    assert (4243, signal.SIGTERM) in sent


def test_terminate_holder_escalates_to_sigkill(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_file = image_root / "tmp" / "lchroot.lock"
    lock_file.write_text("PID 4242 on /dev/pts/0\n", encoding="utf-8")
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)  # never dies
    monkeypatch.setattr(
        lock_mod, "holder_tree", lambda _pid: [ProcInfo(4242, 0, "bash")]
    )
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append((pid, sig)))
    # grace_s=0 -> no wait, straight to escalation
    report = ImageLock(image_root).terminate_holder(grace_s=0.0)
    assert report.escalated is True
    assert (4242, signal.SIGTERM) in sent
    assert (4242, signal.SIGKILL) in sent
    assert not lock_file.exists()


def test_pid_alive_handles_dead_and_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_lookup(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("os.kill", raise_lookup)
    assert lock_mod._pid_alive(123) is False

    def raise_perm(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr("os.kill", raise_perm)
    assert lock_mod._pid_alive(123) is True


# ---------------------------------------------------------------------------
# Property-based test (Hypothesis, PY-TEST-006). /proc/<pid>/stat is
# `pid (comm) state ppid ...` and the kernel does NOT escape spaces or parens in comm —
# the classic parsing trap. `_ppid_from_stat` splits after the FINAL `)`; this proves it
# recovers the right ppid for *any* comm (incl. embedded `(`, `)`, spaces, newlines).
# ---------------------------------------------------------------------------

_PID = st.integers(min_value=1, max_value=2**31)
_PPID = st.integers(min_value=0, max_value=2**31)
_COMM = st.text(max_size=40)  # arbitrary: may contain "(", ")", spaces, newlines
_STATE = st.sampled_from("RSDZTtWXxKPI")  # a single process-state char, never ")"
# Fields after ppid (pgrp, session, ...) are numeric, so they never contain a ")".
_TRAILING = st.lists(st.integers(min_value=0, max_value=2**31), max_size=10)


@given(pid=_PID, comm=_COMM, state=_STATE, ppid=_PPID, trailing=_TRAILING)
def test_prop_ppid_parsed_across_hostile_comm(
    pid: int, comm: str, state: str, ppid: int, trailing: list[int]
) -> None:
    """Aim: `_ppid_from_stat` extracts the parent PID for ANY comm value.

    The wrapping ``)`` we append is always the last ``)`` in the line (every field past
    comm is numeric), so splitting after the final ``)`` recovers ``state ppid ...`` even
    when comm itself contains parens or spaces — the bug this guards against. If the
    parser ever reverts to a naive ``split()[3]``, a comm with a space fails this.
    """
    rest = " ".join(str(n) for n in trailing)
    line = f"{pid} ({comm}) {state} {ppid} {rest}\n"
    assert lock_mod._ppid_from_stat(line) == ppid


# ---------------------------------------------------------------------------
# Malformed / partial lock files (C8). A crashed or half-written holder must never wedge
# the next run: an unparseable lock is treated as having no live holder, so acquire()
# reclaims it rather than raising. (`_parse_holder` yields pid=None -> alive=False.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contents",
    [
        "",  # empty (created but not yet written)
        "garbage with no pid\n",  # no PID/tty at all
        "PID notanumber on /dev/pts/0\n",  # non-numeric pid
        "PID 4242\n",  # truncated mid-write: no "on <tty>"
        "PID 4242 on \n",  # pid but empty tty
        "\x00\x00\x00",  # binary noise
    ],
)
def test_parse_holder_tolerates_malformed_contents(contents: str) -> None:
    holder = lock_mod._parse_holder(contents)
    # Either we recovered nothing (pid=None) or, for "PID 4242 on ", just the pid; in no
    # case is the holder considered alive (pid=None can't be alive), so it is reclaimable.
    assert holder.pid in (None, 4242)
    if holder.pid is None:
        assert holder.alive is False


def test_acquire_reclaims_a_malformed_lock_file(image_root: Path) -> None:
    # A corrupt/partial lock (pid unparseable) is reclaimed, not a hard error.
    (image_root / "tmp" / "lchroot.lock").write_text("garbage\n", encoding="utf-8")
    lock = ImageLock(image_root)
    lock.acquire()  # must not raise
    holder = lock.read_holder()
    assert holder is not None
    assert holder.pid == os.getpid()  # now ours
    lock.release()


def test_custom_name_appears_in_locked_error(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The luna osimage name (which may differ from the path basename) is what the operator
    # typed, so the LockedError must quote *it*, not the tmp-dir basename.
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/0\n", encoding="utf-8"
    )
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)
    with pytest.raises(LockedError, match="'compute-cib' is locked"):
        ImageLock(image_root, name="compute-cib").acquire()


# ---------------------------------------------------------------------------
# Process-tree walking under adverse /proc conditions (C9). The helpers must degrade, not
# crash, when a pid vanishes mid-scan or is simply not present.
# ---------------------------------------------------------------------------


def test_proc_helpers_return_empty_when_pid_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A process that exits between /proc enumeration and the read raises OSError; both
    # readers swallow it (ppid -> None, cmdline -> "") so the tree walk keeps going.
    def raise_oserror(*_a: object, **_k: object) -> bytes:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr("pathlib.Path.read_text", raise_oserror)
    monkeypatch.setattr("pathlib.Path.read_bytes", raise_oserror)
    assert lock_mod._proc_ppid(999999) is None
    assert lock_mod._proc_cmdline(999999) == ""


def test_holder_tree_for_absent_root_is_a_safe_singleton() -> None:
    # An orphan/absent root (no children, no /proc entry) yields just itself at depth 0
    # with an empty cmdline — no crash, no infinite walk. (2**31-1 is never a live pid.)
    tree = lock_mod.holder_tree(2**31 - 1)
    assert len(tree) == 1
    assert tree[0].pid == 2**31 - 1
    assert tree[0].depth == 0
    assert tree[0].cmdline == ""


def test_proc_cmdline_joins_nul_separated_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pathlib.Path.read_bytes", lambda _self: b"dnf\x00-y\x00install\x00vim\x00"
    )
    assert lock_mod._proc_cmdline(1234) == "dnf -y install vim"


# ---------------------------------------------------------------------------
# terminate_holder signal ordering + the "won't die" (NFS D-state) bound (C10/H-series).
# ---------------------------------------------------------------------------


def test_terminate_holder_signals_children_before_parents(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Killing the outer bwrap first could orphan its children; the tree must be signalled
    # deepest-child-first so the namespace collapses cleanly.
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 100 on /dev/pts/0\n", encoding="utf-8"
    )
    tree = [
        ProcInfo(100, 0, "bwrap"),
        ProcInfo(101, 1, "bash"),
        ProcInfo(102, 2, "dnf"),
    ]
    alive = {100, 101, 102}
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(lock_mod, "holder_tree", lambda _pid: tree)
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        alive.discard(pid)

    monkeypatch.setattr("os.kill", fake_kill)
    ImageLock(image_root).terminate_holder()
    sigterms = [pid for pid, sig in sent if sig == signal.SIGTERM]
    assert sigterms == [102, 101, 100]  # deepest descendant first, root last


def test_terminate_holder_is_bounded_when_holder_will_not_die(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The NFS / uninterruptible-sleep (D-state) case: a process stuck in kernel I/O cannot
    # be reaped even by SIGKILL. terminate_holder must NOT spin forever — it polls only to
    # the grace deadline, escalates to SIGKILL, then gives up: clears the lock and reports
    # `escalated` with the survivor recorded, so lchroot never hangs on wedged storage.
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/0\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        lock_mod, "_pid_alive", lambda _pid: True
    )  # never dies (D-state)
    monkeypatch.setattr(lock_mod, "holder_tree", lambda _pid: [ProcInfo(4242, 0, "dd")])
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append((pid, sig)))

    # Drive the poll loop on a fake clock so the bound is asserted without real wall-time.
    clock = {"t": 0.0}
    sleeps = {"n": 0}
    # lock.py does `import time; time.monotonic()/time.sleep()`, resolved at call time, so
    # patching the stdlib functions by path drives its poll loop on a fake clock.
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])

    def fake_sleep(seconds: float) -> None:
        sleeps["n"] += 1
        clock["t"] += seconds
        if sleeps["n"] > 1000:  # safety net: prove it is bounded, don't loop forever
            raise AssertionError(
                "terminate_holder polled unboundedly on a wedged holder"
            )

    monkeypatch.setattr("time.sleep", fake_sleep)

    report = ImageLock(image_root).terminate_holder(grace_s=3.0, poll_s=0.2)

    assert report.escalated is True  # SIGTERM did not work -> SIGKILL was sent
    assert (4242, signal.SIGKILL) in sent
    assert report.signalled == (4242,)  # the wedged pid is reported, not hidden
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # lock cleared anyway
    assert sleeps["n"] <= 3.0 / 0.2 + 2  # polled only to the deadline, then stopped


def test_terminate_holder_race_holder_dies_before_signal(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The holder exits between read_holder() and the kill: SIGTERM hits a gone pid
    # (ProcessLookupError, swallowed), nothing survives, no SIGKILL, lock still cleared.
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/0\n", encoding="utf-8"
    )
    alive = {4242}  # alive at read_holder time...
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(
        lock_mod, "holder_tree", lambda _pid: [ProcInfo(4242, 0, "bwrap")]
    )

    def kill_gone(_pid: int, _sig: int) -> None:
        alive.discard(4242)  # ...gone by the time we signal it
        raise ProcessLookupError

    monkeypatch.setattr("os.kill", kill_gone)
    report = ImageLock(image_root).terminate_holder()
    assert report.escalated is False
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_release_is_a_noop_when_lock_already_gone(image_root: Path) -> None:
    # The lock file vanishing before release() (e.g. someone cleared it) must not raise.
    lock = ImageLock(image_root)
    lock.acquire()
    (image_root / "tmp" / "lchroot.lock").unlink()  # disappear out from under us
    lock.release()  # missing_ok -> no error
