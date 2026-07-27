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

"""Contract + Hypothesis property tests for the bwrap argv builder - the security core.

(Not golden/snapshot tests: the argv is asserted inline and via structural properties, so
there are no golden files and no --update step.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from utils.lchroot.sandbox import (
    DEFAULT_SHELL,
    build_bwrap_argv,
    render_plan,
)
from utils.lchroot.types import ResolvedImage

IMG = ResolvedImage(
    name="compute-image-bubble",
    path=Path("/trinity/images/cib"),
    kernelversion="5.14.0-x",
)


def test_rw_argv_has_all_hardening() -> None:
    argv = build_bwrap_argv(IMG, read_only=False)
    joined = " ".join(argv)
    assert argv[0] == "bwrap"
    assert "--bind /trinity/images/cib /" in joined
    assert "--dev /dev" in joined
    assert "--proc /proc" in joined
    assert "--ro-bind /sys /sys" in joined
    assert "--tmpfs /run" in joined
    assert "--unshare-pid" in argv
    assert "--unshare-uts" in argv
    assert "--die-with-parent" in argv
    assert "--hostname compute-image-bubble" in joined
    # the load-bearing capability drop (skill.md L6)
    assert "--cap-drop CAP_SYS_ADMIN" in joined
    assert "--setenv LD_PRELOAD libluna-fakeuname.so" in joined
    assert "--setenv FAKE_KERN 5.14.0-x" in joined
    # host TMPDIR must not leak into the sandbox (breaks dnf) - skill.md L13
    assert "--setenv TMPDIR /tmp" in joined
    # systemd tooling must treat the image's init as offline (no live manager in
    # the --unshare-pid sandbox), so `systemctl enable`/Ansible's systemd module
    # do file-only ops instead of failing on a missing D-Bus.
    assert "--setenv SYSTEMD_OFFLINE 1" in joined
    assert argv[-1] == "/bin/bash"


def test_hostname_override_replaces_image_name_only() -> None:
    """Aim: the optional hostname override changes the UTS *name*, nothing else.

    The Ansible `lchroot` connection plugin enters an image on behalf of the
    controller and passes the controller's hostname so that in-image steps see the
    same hostname a raw `chroot` would (raw chroot shares the host UTS namespace;
    lchroot does not). UTS isolation (`--unshare-uts`) must stay intact.
    """
    argv = build_bwrap_argv(IMG, read_only=False, hostname="controller1")
    joined = " ".join(argv)
    assert "--hostname controller1" in joined
    assert "--hostname compute-image-bubble" not in joined
    # isolation unchanged: still a private UTS namespace, just renamed.
    assert "--unshare-uts" in argv


def test_hostname_defaults_to_image_name() -> None:
    argv = build_bwrap_argv(IMG, read_only=False, hostname=None)
    assert "--hostname compute-image-bubble" in " ".join(argv)


@pytest.mark.parametrize("read_only", [False, True])
def test_die_with_parent_present_in_both_modes(read_only: bool) -> None:
    """Aim: the teardown guarantee is load-bearing and mode-independent (ADR 0006).

    Without `--die-with-parent`, bwrap's default pid-1 reaper waits for any
    backgrounded/`setsid` child: a detached process keeps the session alive (an `exit`
    hangs) and a SIGKILLed launcher orphans a bwrap+child tree on the host that holds
    locks (the rpmdb-lock cascade in RESULTS-2026-05-31-destructive.md). The flag makes
    "nothing you start inside survives leaving the image" true (skill.md L21). Verified
    live on bwrap 0.6.3; if this assertion disappears, the footgun returns silently.
    """
    argv = build_bwrap_argv(IMG, read_only=read_only)
    assert "--die-with-parent" in argv


def test_ro_argv_uses_ro_bind_and_drops_all_caps() -> None:
    argv = build_bwrap_argv(IMG, read_only=True)
    joined = " ".join(argv)
    assert "--ro-bind /trinity/images/cib /" in joined
    assert "--bind /trinity/images/cib /" not in joined
    assert "--cap-drop ALL" in joined


def test_missing_kernelversion_omits_fake_kern() -> None:
    img = ResolvedImage(name="x", path=Path("/img"), kernelversion=None)
    argv = build_bwrap_argv(img, read_only=False)
    assert "FAKE_KERN" not in " ".join(argv)
    assert "--setenv LD_PRELOAD libluna-fakeuname.so" in " ".join(argv)


@pytest.mark.parametrize(
    "command",
    [["rpm", "-qa"], ["/bin/sh", "-c", "echo hi"]],
)
def test_command_is_appended_after_separator(command: list[str]) -> None:
    argv = build_bwrap_argv(IMG, read_only=False, command=command)
    sep = argv.index("--")
    assert argv[sep + 1 :] == command


def test_no_command_defaults_to_shell() -> None:
    argv = build_bwrap_argv(IMG, read_only=False)
    assert argv[argv.index("--") + 1 :] == ["/bin/bash"]


def test_rw_argv_chdir_and_ps1_prefix() -> None:
    """Aim: the operator always lands at `/` and the prompt advertises rw mode.

    `--chdir /` guarantees a predictable cwd inside the sandbox regardless of where
    lchroot was launched; the `lchroot(rw)` PS1 prefix makes a *mutating* session
    visually obvious in the shell (the safety cue that replaced the rejected y/N prompt,
    L18/D13).
    """
    argv = build_bwrap_argv(IMG, read_only=False)
    assert "--chdir /" in " ".join(argv)
    ps1 = argv[argv.index("PS1") + 1]
    assert ps1.startswith("lchroot(rw)")


def test_ro_argv_ps1_prefix() -> None:
    """Aim: a read-only inspection session is visually distinct from a rw one.

    The `lchroot(ro)` prompt prefix tells the operator at a glance they are in the safe,
    non-mutating mode — the rw/ro distinction must never be silent.
    """
    argv = build_bwrap_argv(IMG, read_only=True)
    ps1 = argv[argv.index("PS1") + 1]
    assert ps1.startswith("lchroot(ro)")


def test_render_plan_mentions_mode_and_argv() -> None:
    argv = build_bwrap_argv(IMG, read_only=True)
    plan = render_plan(IMG, argv, read_only=True)
    assert "READ-ONLY" in plan
    assert "compute-image-bubble" in plan
    assert "bwrap" in plan


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis, PY-TEST-006). The example tests above pin
# specific argvs; these prove the *invariants* hold for arbitrary inputs — no image
# name, path, kernelversion, mode, or command can produce an argv that drops a hardening
# flag, mismatches the cap-drop to the mode, or lets a command token displace the
# sandbox prefix. Image fields are domain-valid-ish; commands are deliberately arbitrary
# (incl. ``--``, ``""``, spaces) to prove command tokens can never perturb the prefix.
# ---------------------------------------------------------------------------

_path_seg = st.text(
    alphabet=st.characters(
        min_codepoint=33, max_codepoint=126, blacklist_characters="/"
    ),
    min_size=1,
    max_size=12,
).filter(lambda s: s not in {".", ".."})
_abs_path = st.lists(_path_seg, min_size=1, max_size=4).map(
    lambda segs: Path("/" + "/".join(segs))
)
_image_name = st.text(min_size=1, max_size=24)
_kernelversion = st.none() | st.text(min_size=1, max_size=24)
_command = st.lists(st.text(max_size=12), max_size=5)
_resolved = st.builds(
    ResolvedImage, name=_image_name, path=_abs_path, kernelversion=_kernelversion
)


def _split_command(argv: list[str], command: list[str]) -> tuple[list[str], list[str]]:
    """Return ``(sandbox_flags, command)`` by stripping the trailing ``-- <command>``.

    Splits from the *end* (by length) so an arbitrary command token — even a bare
    ``--`` — can never be mistaken for lchroot's separator. Asserts the separator is where
    it must be, then returns the pure flag prefix so downstream checks can't be fooled by
    a command that happens to repeat a sandbox token.
    """
    expected = command or [DEFAULT_SHELL]
    sep = len(argv) - len(expected) - 1
    assert argv[sep] == "--", argv
    return argv[:sep], argv[sep + 1 :]


def _adjacent(seq: list[str], first: str, second: str) -> bool:
    """True iff ``second`` immediately follows ``first`` somewhere in ``seq``."""
    return any(seq[i] == first and seq[i + 1] == second for i in range(len(seq) - 1))


def _setenv(flags: list[str], key: str) -> str | None:
    """Value of the first ``--setenv <key> <value>`` triple in ``flags``, else None."""
    for i in range(len(flags) - 2):
        if flags[i] == "--setenv" and flags[i + 1] == key:
            return flags[i + 2]
    return None


@given(img=_resolved, read_only=st.booleans(), command=_command)
def test_prop_command_is_verbatim_suffix(
    img: ResolvedImage, read_only: bool, command: list[str]
) -> None:
    """Aim: the command is always the exact argv tail after lchroot's ``--``, verbatim.

    Whatever tokens a caller passes (including ``--``, empty strings, spaces) appear as
    the suffix and nowhere else — the structural reason command injection is impossible
    (PY-CORE-005) and that the hardening prefix is never displaced. No command => the
    image shell.
    """
    argv = build_bwrap_argv(img, read_only=read_only, command=command)
    _flags, got = _split_command(argv, command)
    assert got == (command or [DEFAULT_SHELL])


@given(img=_resolved, read_only=st.booleans(), command=_command)
def test_prop_hardening_prefix_invariant(
    img: ResolvedImage, read_only: bool, command: list[str]
) -> None:
    """Aim: every security flag is present for ANY input — they are structural.

    Private PID/UTS namespaces, the die-with-parent teardown (ADR 0006), the namespaced
    ``/proc`` + ``/dev``, the read-only ``/sys``, the ``/run`` tmpfs, the TMPDIR pin
    (L13), fakeuname, and ``--chdir /`` must hold regardless of name/path/kernel/mode/
    command. Checked on the flag prefix so a command repeating a token cannot fake it.
    """
    argv = build_bwrap_argv(img, read_only=read_only, command=command)
    flags, _ = _split_command(argv, command)
    assert flags[0] == "bwrap"
    for tok in ("--unshare-pid", "--unshare-uts", "--die-with-parent"):
        assert tok in flags
    assert _adjacent(flags, "--dev", "/dev")
    assert _adjacent(flags, "--proc", "/proc")
    assert _adjacent(flags, "--ro-bind", "/sys")
    assert _adjacent(flags, "--tmpfs", "/run")
    assert _adjacent(flags, "--chdir", "/")
    assert _setenv(flags, "TMPDIR") == "/tmp"
    assert _setenv(flags, "LD_PRELOAD") == "libluna-fakeuname.so"


@given(img=_resolved, read_only=st.booleans(), command=_command)
def test_prop_bind_and_capdrop_match_mode(
    img: ResolvedImage, read_only: bool, command: list[str]
) -> None:
    """Aim: the root bind + cap-drop are exactly coupled to the mode, for any input.

    ro => ``--ro-bind <path> /`` + ``--cap-drop ALL``; rw => ``--bind <path> /`` +
    ``--cap-drop CAP_SYS_ADMIN``. The cap-drop is load-bearing (L6); this pins that it
    can never silently weaken, whatever the image or command.
    """
    argv = build_bwrap_argv(img, read_only=read_only, command=command)
    flags, _ = _split_command(argv, command)
    assert flags[1] == ("--ro-bind" if read_only else "--bind")
    assert flags[2] == str(img.path)
    assert flags[3] == "/"
    expected_cap = "ALL" if read_only else "CAP_SYS_ADMIN"
    assert flags[flags.index("--cap-drop") + 1] == expected_cap


@given(img=_resolved, read_only=st.booleans(), command=_command)
def test_prop_fake_kern_iff_kernelversion(
    img: ResolvedImage, read_only: bool, command: list[str]
) -> None:
    """Aim: FAKE_KERN is set exactly when the image has a kernelversion, with its value.

    A single equality covers both branches: ``_setenv`` returns None when no
    ``--setenv FAKE_KERN`` triple exists, which equals ``img.kernelversion`` when that is
    None too.
    """
    argv = build_bwrap_argv(img, read_only=read_only, command=command)
    flags, _ = _split_command(argv, command)
    assert _setenv(flags, "FAKE_KERN") == img.kernelversion
