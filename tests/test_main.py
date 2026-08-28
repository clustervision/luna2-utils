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

"""Tests for the run() orchestration with injected fakes (no network/subprocess)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import utils.lchroot.__main__ as main_mod
import utils.lchroot.lock as lock_mod
from conftest import FakeTransport, osimage_response
from utils.lchroot.__main__ import build_parser, run
from utils.lchroot.config import ApiConfig
from utils.lchroot.errors import LockedError, SandboxError, SecondaryControllerError
from utils.lchroot.executor import Executor
from utils.lchroot.lock import ImageLock, ProcInfo
from utils.lchroot.luna import LunaClient

CFG = ApiConfig(username="luna", password="p", endpoint="ctrl:7050")


def _client(
    image_root: Path, *, master: bool = True, enabled: bool = True
) -> tuple[LunaClient, FakeTransport]:
    transport = FakeTransport(
        {
            "/token": {"token": "T"},
            "/ha/state": {"ha": {"enabled": enabled, "master": master}},
            "/config/osimage/cib": osimage_response("cib", image_root),
            "/config/osimage": {"config": {"osimage": {"cib": {}, "login": {}}}},
        }
    )
    return LunaClient(CFG, transport), transport


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args([*argv])


def _parse_hoisted(*argv: str) -> argparse.Namespace:
    """Parse the way main() does — through the option-hoisting reorder."""
    parser = build_parser()
    return parser.parse_args(main_mod._hoist_image_options([*argv], parser))


@pytest.fixture(autouse=True)
def _interactive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Most entry tests model an operator at a terminal (a tty): a bare `lchroot <img>`
    # drops into a shell, and a live lock prompts. The non-interactive cases (refuse a live
    # lock, require a command) set this False explicitly.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)


def test_options_after_image_are_hoisted() -> None:
    # `lchroot img --ro` must mean read-only, like `lchroot --ro img`.
    assert _parse_hoisted("cib", "--ro").ro is True
    assert _parse_hoisted("cib", "--force").force is True
    assert _parse_hoisted("cib", "--status").status is True
    a = _parse_hoisted("cib", "--ro", "dnf", "install", "vim")
    assert a.ro is True
    assert a.command == ["dnf", "install", "vim"]
    # a value-taking option after the image keeps its value, command follows
    a = _parse_hoisted("cib", "--hostname", "c1", "dnf", "x")
    assert a.hostname == "c1"
    assert a.command == ["dnf", "x"]


def test_command_flags_after_command_word_are_untouched() -> None:
    # The collision case: once the command word is reached, its own flags stay with it
    # (this is why we do not use parse_known_args, which would steal `-v`).
    a = _parse_hoisted("cib", "tar", "-v", "-C", "/x")
    assert a.command == ["tar", "-v", "-C", "/x"]
    assert a.verbose is False  # the command's -v was NOT eaten as lchroot's --verbose
    # `lchroot img dnf -y install` keeps -y for dnf
    assert _parse_hoisted("cib", "dnf", "-y", "install").command == [
        "dnf",
        "-y",
        "install",
    ]


def test_double_dash_disables_hoist() -> None:
    # An explicit `--` is the operator drawing the boundary; never reorder across it.
    a = _parse_hoisted("cib", "--", "--ro")
    assert a.ro is False
    assert a.command == ["--ro"]


def test_hoist_leaves_path_mode_and_bare_invocations_alone() -> None:
    # --path mode: the first bare word is folded into the command by _resolve_target;
    # hoisting must not disturb that (osimage captures the command head as today).
    a = _parse_hoisted("--path", "/trinity/images/foo", "dnf", "x")
    assert a.path == "/trinity/images/foo"
    assert a.osimage == "dnf"
    assert a.command == ["x"]
    # nothing to hoist: --list-images / no positional
    assert _parse_hoisted("--list-images").list_images is True


def test_dry_run_prints_plan_and_creates_no_lock(
    image_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = _client(image_root)
    rc = run(_args("--dry-run", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert "lchroot plan" in capsys.readouterr().out
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_ro_entry_acquires_lock_and_releases(image_root: Path) -> None:
    client, _ = _client(image_root)
    rc = run(_args("--ro", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # released


def test_rw_entry_enters_directly_on_master(image_root: Path) -> None:
    # rw is the default and enters with NO prompt (like lchroot); on the master HA
    # controller it is allowed, and no image sync happens on exit (ADR 0011).
    client, transport = _client(image_root, master=True)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert not any("syncimage" in url for _, url in transport.calls)


def test_two_runs_back_to_back_succeed(image_root: Path) -> None:
    # Re-entrancy (C5): run 1 must release its lock on clean exit so run 2 is not falsely
    # blocked (exit 9) by its predecessor's leftover lock. The single-run tests can't catch
    # a lock leaked on the exit path — only a back-to-back entry does.
    client, _ = _client(image_root)
    assert run(_args("cib"), client, Executor(dry_run=True)) == 0
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # released
    assert run(_args("cib"), client, Executor(dry_run=True)) == 0  # not refused


def test_list_images_prints_names_and_exits_zero(
    image_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = _client(image_root)
    rc = run(_args("--list-images"), client, Executor(dry_run=True))
    assert rc == 0
    names = capsys.readouterr().out.split()
    assert names == ["cib", "login"]  # sorted, one per line


def test_no_osimage_without_list_images_is_error(image_root: Path) -> None:
    client, _ = _client(image_root)
    assert run(_args(), client, Executor(dry_run=True)) == 7


def test_status_reports_not_in_use(
    image_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = _client(image_root)
    rc = run(_args("--status", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert "not in use" in capsys.readouterr().out


def test_status_reports_holder_and_tree(
    image_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/9\n", encoding="utf-8"
    )
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        main_mod,
        "holder_tree",
        lambda _pid: [ProcInfo(4242, 0, "bwrap"), ProcInfo(4243, 1, "dnf update")],
    )
    client, _ = _client(image_root)
    rc = run(_args("--status", "cib"), client, Executor(dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "PID 4242 on /dev/pts/9 (alive)" in out
    assert "dnf update" in out


def _seed_live_holder(
    image_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cmdline: str = "bwrap",
    pkg: str | None = None,
) -> set[int]:
    """Write a live lock held by PID 4242 and wire the inspection/kill seams.

    Returns the mutable ``alive`` set; the stubbed ``os.kill`` discards a pid from it, so
    ``terminate_holder`` sees the holder die after SIGTERM. ``pkg`` sets what
    ``running_package_manager`` reports (None = nothing rpmdb-risky running).
    """
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/9\n", encoding="utf-8"
    )
    alive = {4242}
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda pid: pid in alive)
    tree = [ProcInfo(4242, 0, cmdline)]
    monkeypatch.setattr(main_mod, "holder_tree", lambda _pid: tree)
    monkeypatch.setattr(lock_mod, "holder_tree", lambda _pid: tree)
    monkeypatch.setattr(main_mod, "running_package_manager", lambda _pid: pkg)
    monkeypatch.setattr("os.kill", lambda pid, _sig: alive.discard(pid))
    return alive


def test_force_dry_run_does_not_break_lock(
    image_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --dry-run runs (and mutates) nothing: a live lock is left intact even with --force.
    lock_file = image_root / "tmp" / "lchroot.lock"
    _seed_live_holder(image_root, monkeypatch)
    monkeypatch.setattr(
        "os.kill",
        lambda *_a: (_ for _ in ()).throw(AssertionError("dry-run must not kill")),
    )
    client, _ = _client(image_root)
    rc = run(_args("--force", "--dry-run", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert lock_file.exists()  # nothing removed
    assert "lchroot plan" in capsys.readouterr().out


def test_live_lock_tty_prompt_yes_breaks_then_enters(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a terminal a live lock prompts; "yes" stops the holder, then we enter (rc 0).
    alive = _seed_live_holder(image_root, monkeypatch)
    monkeypatch.setattr(main_mod, "_confirm", lambda _q: True)
    client, _ = _client(image_root)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert alive == set()  # the holder was stopped
    # entered then released our own lock on exit
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_live_lock_tty_prompt_no_aborts_and_spares_holder(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Declining the prompt leaves the holder and its lock untouched and does not enter.
    lock_file = image_root / "tmp" / "lchroot.lock"
    _seed_live_holder(image_root, monkeypatch)
    monkeypatch.setattr(main_mod, "_confirm", lambda _q: False)
    monkeypatch.setattr(
        "os.kill",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not kill on decline")),
    )
    client, _ = _client(image_root)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 9
    assert lock_file.exists()  # holder untouched


def test_force_live_lock_tty_breaks_without_prompt(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --force never asks: it breaks the live lock and enters even on a terminal.
    alive = _seed_live_holder(image_root, monkeypatch)

    def no_prompt(_q: str) -> bool:
        raise AssertionError("--force must not prompt")

    monkeypatch.setattr(main_mod, "_confirm", no_prompt)
    client, _ = _client(image_root)
    rc = run(_args("--force", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert alive == set()


def test_force_live_lock_non_tty_breaks_and_runs_command(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a terminal, --force breaks the live lock and runs the given command (rc 0).
    alive = _seed_live_holder(image_root, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    client, _ = _client(image_root)
    rc = run(
        _args("--force", "cib", "dnf", "-y", "update"), client, Executor(dry_run=True)
    )
    assert rc == 0
    assert alive == set()


def test_force_warns_about_running_package_manager(
    image_root: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Breaking a lock while dnf/rpm runs surfaces the rpmdb-corruption warning first.
    _seed_live_holder(image_root, monkeypatch, cmdline="dnf update", pkg="dnf")
    client, _ = _client(image_root)
    with caplog.at_level("WARNING"):
        rc = run(_args("--force", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert "rpmdb" in caplog.text


def test_stale_lock_is_reclaimed_then_enters(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stale lock (dead holder) never prompts/refuses: acquire() reclaims it and we enter.
    (image_root / "tmp" / "lchroot.lock").write_text(
        "PID 4242 on /dev/pts/9\n", encoding="utf-8"
    )
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: False)

    def must_not_resolve(*_a: object) -> int:
        raise AssertionError("a stale lock must not reach the live-lock policy")

    monkeypatch.setattr(main_mod, "_resolve_live_lock", must_not_resolve)
    client, _ = _client(image_root)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # reclaimed then released


def test_non_tty_without_command_is_rejected(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No terminal + no command would launch a shell that reads EOF and does nothing — that
    # looks like success while nothing ran, so it is an explicit error (exit 7).
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    client, _ = _client(image_root)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 7
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # never entered


def test_confirm_no_tty_returns_false_without_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def fail(_prompt: str) -> str:
        raise AssertionError("input must not be called without a tty")

    monkeypatch.setattr("builtins.input", fail)
    assert main_mod._confirm("kill?") is False


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y", True),
        ("yes", True),
        ("Y", True),
        ("", False),
        ("n", False),
        ("nope", False),
    ],
)
def test_confirm_reads_tty_answer(
    answer: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert main_mod._confirm("kill?") is expected


def test_confirm_eof_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def raise_eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    assert main_mod._confirm("kill?") is False


def test_live_lock_non_tty_without_force_is_refused(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a terminal and without --force, a live lock is refused (LockedError -> exit
    # 9): automation never silently kills a running session. The message is also what
    # `luna osimage pack` surfaces (pack does NOT break a locked image itself, #1): it must
    # name the image (the osimage name, not the path basename) and point at the remedy.
    lock_file = image_root / "tmp" / "lchroot.lock"
    lock_file.write_text("PID 4242 on /dev/pts/1\n", encoding="utf-8")
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        main_mod, "holder_tree", lambda _pid: [ProcInfo(4242, 0, "bwrap")]
    )
    monkeypatch.setattr(main_mod, "running_package_manager", lambda _pid: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def fail_kill(_pid: int, _sig: int) -> None:
        raise AssertionError("must not kill a live holder without a tty or --force")

    monkeypatch.setattr("os.kill", fail_kill)
    client, _ = _client(image_root)
    with pytest.raises(LockedError) as excinfo:
        run(_args("cib", "true"), client, Executor(dry_run=True))
    msg = str(excinfo.value)
    assert "'cib' is held by a live session" in msg
    assert "lchroot --status cib" in msg
    assert "--force" in msg
    assert lock_file.exists()  # holder's lock left intact


def test_sandbox_launch_failure_raises_sandbox_error(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing/unexecutable bwrap surfaces as OSError from the launch; run() must turn
    # it into a typed SandboxError (-> exit 1 at the boundary) rather than letting a raw
    # traceback escape. The lock taken to enter must still be released on the way out.
    def boom(self: Executor, *_a: object, **_k: object) -> int:
        raise FileNotFoundError(2, "No such file or directory", "bwrap")

    monkeypatch.setattr(Executor, "run_interactive", boom)
    client, _ = _client(image_root)
    with pytest.raises(SandboxError, match="bubblewrap"):
        run(_args("cib"), client, Executor(dry_run=False))
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # released on unwind


def test_path_mode_enters_luna_free(image_root: Path) -> None:
    # --path passes client=None: it must enter (rw) with NO luna client at all and
    # release the lock on exit. The None client is the proof there are zero luna calls.
    rc = run(_args("--path", str(image_root)), None, Executor(dry_run=True))
    assert rc == 0
    assert not (image_root / "tmp" / "lchroot.lock").exists()  # acquired then released


def test_path_mode_dry_run_needs_no_client(
    image_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run(
        _args("--dry-run", "--path", str(image_root)), None, Executor(dry_run=True)
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "lchroot plan" in out
    assert image_root.name in out  # basename is the image name


def test_path_mode_folds_osimage_token_back_into_command(
    image_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # With --path there is no name positional, so argparse's `osimage` ("echo") is really
    # the first command word; it must be folded back so the command is `echo hi`.
    rc = run(
        _args("--dry-run", "--path", str(image_root), "echo", "hi"),
        None,
        Executor(dry_run=True),
    )
    assert rc == 0
    plan = capsys.readouterr().out
    assert plan.rstrip().endswith("-- echo hi")


def test_path_mode_status_is_luna_free(
    image_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run(_args("--status", "--path", str(image_root)), None, Executor(dry_run=True))
    assert rc == 0
    assert "not in use" in capsys.readouterr().out


def test_list_images_without_client_is_error(image_root: Path) -> None:
    # --list-images is luna-backed; combined with --path (client=None) it must error 7.
    assert run(_args("--list-images"), None, Executor(dry_run=True)) == 7


def test_non_master_rw_is_refused(image_root: Path) -> None:
    # HA enabled and this host is NOT the master: a read-write entry is refused outright
    # (no override) so images are only mutated on the master (ADR 0011). The lock must
    # never be taken — we refuse before entering.
    client, _ = _client(image_root, master=False, enabled=True)
    with pytest.raises(SecondaryControllerError, match="non-master"):
        run(_args("cib"), client, Executor(dry_run=True))
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_non_master_ro_is_allowed(image_root: Path) -> None:
    # --ro cannot mutate, so inspection on the secondary is allowed even under HA.
    client, _ = _client(image_root, master=False, enabled=True)
    rc = run(_args("--ro", "cib"), client, Executor(dry_run=True))
    assert rc == 0


def test_ha_disabled_rw_is_allowed_on_non_master(image_root: Path) -> None:
    # HA off (standalone install): the gate does not apply even if 'master' is False —
    # there is no secondary to diverge from. rw entry proceeds normally.
    client, _ = _client(image_root, master=False, enabled=False)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 0


# ---------------------------------------------------------------------------
# Operation ordering in run() (O-series). The gates must fire in the right sequence, and
# a plan or a refusal must mutate NO host state (foreign-arch binfmt, the lock) before the
# decision is reached. We spy on emulation setup (`setup_emulation_for_entry`) and lock `acquire`.
# ---------------------------------------------------------------------------


def _spy_emulation(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """Record an 'emulate' event when run() sets up foreign-arch emulation (native: None)."""

    def fake(_path: Path, *, no_emulate: bool = False) -> None:
        events.append("emulate")

    monkeypatch.setattr(main_mod, "setup_emulation_for_entry", fake)


def test_entry_emulates_then_locks_then_enters(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real-entry order is: set up emulation (host binfmt) → take the lock → enter.
    events: list[str] = []
    _spy_emulation(monkeypatch, events)
    orig_acquire = ImageLock.acquire

    def spy_acquire(self: ImageLock, *, force: bool = False) -> None:
        events.append("acquire")
        orig_acquire(self, force=force)

    monkeypatch.setattr(ImageLock, "acquire", spy_acquire)

    def spy_enter(self: Executor, argv: object, **k: object) -> int:
        events.append("enter")
        return 0

    monkeypatch.setattr(Executor, "run_interactive", spy_enter)
    client, _ = _client(image_root)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert events == ["emulate", "acquire", "enter"]


def test_dry_run_sets_up_no_emulation_and_no_lock(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plan mutates nothing: no binfmt registration, no lock file (PY-CORE-004).
    events: list[str] = []
    _spy_emulation(monkeypatch, events)
    client, _ = _client(image_root)
    rc = run(_args("--dry-run", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert events == []
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_ha_refusal_precedes_emulation_and_lock(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The non-master gate (exit 10) fires BEFORE any host mutation — no binfmt, no lock.
    events: list[str] = []
    _spy_emulation(monkeypatch, events)
    client, _ = _client(image_root, master=False, enabled=True)
    with pytest.raises(SecondaryControllerError):
        run(_args("cib"), client, Executor(dry_run=True))
    assert events == []
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_non_tty_no_command_refused_before_emulation(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The "no command on a non-tty" guard (exit 7) also fires before emulation/lock.
    events: list[str] = []
    _spy_emulation(monkeypatch, events)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    client, _ = _client(image_root)
    rc = run(_args("cib"), client, Executor(dry_run=True))
    assert rc == 7
    assert events == []
    assert not (image_root / "tmp" / "lchroot.lock").exists()


def test_live_lock_is_broken_before_acquire(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live-lock policy runs BEFORE acquire(), so acquire() never faces a live holder.
    # Proof: with --force, by the time acquire() runs the holder's lock is already cleared.
    _seed_live_holder(image_root, monkeypatch)
    lock_file = image_root / "tmp" / "lchroot.lock"
    seen: dict[str, bool] = {}
    orig_acquire = ImageLock.acquire

    def spy_acquire(self: ImageLock, *, force: bool = False) -> None:
        seen["holder_cleared_first"] = not lock_file.exists()
        orig_acquire(self, force=force)

    monkeypatch.setattr(ImageLock, "acquire", spy_acquire)
    client, _ = _client(image_root)
    rc = run(_args("--force", "cib"), client, Executor(dry_run=True))
    assert rc == 0
    assert seen["holder_cleared_first"] is True


# ---------------------------------------------------------------------------
# More option-ordering permutations (extends test_options_after_image_are_hoisted).
# ---------------------------------------------------------------------------


def test_hoist_handles_multiple_flags_both_sides_and_equals() -> None:
    # multiple value-less lchroot flags after the image are all hoisted
    a = _parse_hoisted("cib", "--ro", "--force", "--status")
    assert (a.ro, a.force, a.status) == (True, True, True)
    # a flag before AND after the image both take effect; the command still follows
    a = _parse_hoisted("--ro", "cib", "--force", "dnf", "x")
    assert a.ro is True
    assert a.force is True
    assert a.command == ["dnf", "x"]
    # equals-style value option is recognised and hoisted as a single token
    a = _parse_hoisted("cib", "--hostname=h1", "--ro", "dnf")
    assert a.hostname == "h1"
    assert a.ro is True
    assert a.command == ["dnf"]
    # a value option consumes its NEXT token, never mistaking it for the command word
    a = _parse_hoisted("cib", "--log-file", "out.log", "dnf", "x")
    assert a.log_file == "out.log"
    assert a.command == ["dnf", "x"]
    # an UNKNOWN flag after the image is not an lchroot option -> left for the command
    a = _parse_hoisted("cib", "--bogus", "dnf")
    assert a.command == ["--bogus", "dnf"]
