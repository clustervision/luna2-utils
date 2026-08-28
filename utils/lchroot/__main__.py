#!/trinity/local/python/bin/python3

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

"""lchroot entrypoint: argparse -> run(); the only place process exit codes are set.

Exit codes preserve the lchroot contract and extend it (see ``main`` and
``findings-lchroot.md`` for the canonical table): 0 success, 7 config / image-resolve /
luna (the oracle's "bad input"), 8 emulation setup, 9 image locked (oracle), 10 refused
on a non-master HA controller, 130 interrupt, 1 any other error (PY-CORE-006, PY-ERR
pattern).
"""

import argparse
import logging
import os
import sys

import requests

from .config import load_api_config
from .emulation import setup_emulation_for_entry
from .errors import (
    ConfigError,
    EmulationError,
    ImageResolveError,
    LchrootError,
    LockedError,
    LunaError,
    SandboxError,
    SecondaryControllerError,
)
from .executor import Executor
from .image import resolve_local_path
from .lock import ImageLock, LockHolder, ProcInfo, holder_tree, running_package_manager
from .luna import LunaClient, Transport
from .sandbox import build_bwrap_argv, render_plan
from .transport import RequestsTransport, silence_insecure_tls_warnings
from .types import ResolvedImage

log = logging.getLogger("lchroot")

_EXAMPLES = """\
examples:
  lchroot compute-image                     rw shell to customise the image
  lchroot compute-image dnf -y install vim  run one command (non-interactive)
  lchroot --ro compute-image                inspect read-only (no changes)
  lchroot --path /trinity/images/foo        enter a rootfs by path (no luna)
  lchroot --status compute-image            show the holder + in-image process tree
  lchroot --force compute-image             break a live lock, then enter
  lchroot --force compute-image dnf -y ...  break a live lock, then run a command
  lchroot --dry-run compute-image           print the bwrap plan, run nothing

exit codes:
  0 ok   7 bad args/config/image   8 emulation   9 locked
  10 refused on a non-master HA controller   130 interrupted
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser; the positional contract matches lchroot."""
    parser = argparse.ArgumentParser(
        prog="lchroot",
        description="Enter a TrinityX OS image in a bubblewrap sandbox to safely "
        "customise it.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "osimage", nargs="?", help="osimage name (see `luna osimage list`)"
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run (default: the image shell)",
    )
    parser.add_argument(
        # Hidden from --help (SUPPRESS): this exists only to back shell completion
        # (completions/lchroot.bash calls it on each TAB) and scripting, not as an
        # operator-facing flag. It still works when typed.
        "--list-images",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--path",
        metavar="DIR",
        help="enter a rootfs by directory path, bypassing luna ENTIRELY (no token, no HA "
        "master check); for images not registered with luna, or path-centric callers "
        "(osimage pack / image-create). The image name is the directory basename.",
    )
    parser.add_argument(
        "--ro", action="store_true", help="mount the image read-only (inspection)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the bwrap command and plan; execute nothing",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="show who holds the image lock and the in-image process tree, then exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="if the image is held by a live session, break that lock (stop the holder's "
        "process tree) and proceed instead of asking (on a terminal) or refusing "
        "(non-interactively). A stale lock is always reclaimed automatically; without "
        "--force a live lock prompts on a terminal and is refused without one.",
    )
    parser.add_argument(
        "--no-emulate",
        action="store_true",
        help="do not set up qemu-user emulation for a foreign-arch image (fail instead). "
        "To register a handler without entering an image, use the `qemu-static` command.",
    )
    parser.add_argument(
        "--hostname",
        metavar="NAME",
        help="set the sandbox hostname (default: the image name). Automation callers "
        "entering an image on behalf of the controller (e.g. the Ansible lchroot "
        "connection plugin) pass the controller's hostname so in-image steps see the "
        "same hostname a raw chroot would; UTS isolation is unchanged.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show what it is doing (INFO-level logging)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show detailed logging for troubleshooting (very verbose)",
    )
    parser.add_argument("--log-file", help="also write logs to this file")
    return parser


def _hoist_image_options(argv: list[str], parser: argparse.ArgumentParser) -> list[str]:
    """Let lchroot's own options hug the osimage — before OR directly after it.

    ``command`` is an ``argparse.REMAINDER`` so that a command's own flags pass through
    untouched (``lchroot img dnf -y install`` gives ``-y`` to dnf). The cost is that
    REMAINDER also swallows lchroot's *own* flags written after the image, so
    ``lchroot img --ro`` would run a program ``--ro`` instead of mounting read-only.

    This reorders only the recognised lchroot options that sit *between the osimage and
    the command word* to the front, so ``lchroot img --ro`` == ``lchroot --ro img``. Once
    the command word is reached, everything after it (including ``-v`` and friends) is
    left for the command — collisions are impossible. An explicit ``--`` disables the
    reorder entirely (the operator has drawn the boundary themselves). The option tables
    are read off ``parser`` so they never drift from the real flag set.
    """
    if "--" in argv:
        return argv

    # argparse exposes no public reader for its actions; `_actions` is stable across
    # versions and only read here (never mutated).
    takes_value: dict[str, bool] = {
        opt: action.nargs != 0
        for action in parser._actions
        for opt in action.option_strings
    }

    def is_lchroot_opt(tok: str) -> bool:
        return (
            tok.startswith("-") and tok != "-" and tok.split("=", 1)[0] in takes_value
        )

    def consume(tokens: list[str], start: int, sink: list[str]) -> int:
        """Move leading recognised options (and any values) into ``sink``.

        Stops at the first non-option token and returns its index.
        """
        i = start
        while i < len(tokens) and is_lchroot_opt(tokens[i]):
            tok = tokens[i]
            sink.append(tok)
            if "=" not in tok and takes_value.get(tok) and i + 1 < len(tokens):
                i += 1
                sink.append(tokens[i])
            i += 1
        return i

    head: list[str] = []
    i = consume(argv, 0, head)  # options before the image
    if i >= len(argv):
        return argv  # no positional at all (e.g. just --help / --list-images)
    image = argv[i]
    i = consume(argv, i + 1, head)  # options between the image and the command word
    return [*head, image, *argv[i:]]


def _configure_logging(args: argparse.Namespace) -> None:
    level = (
        logging.DEBUG
        if args.debug
        else (logging.INFO if args.verbose else logging.WARNING)
    )
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    if args.log_file:
        logging.getLogger().addHandler(
            logging.FileHandler(args.log_file, encoding="utf-8")
        )


def _render_tree(holder: LockHolder, tree: list[ProcInfo]) -> str:
    """Render a lock holder and its in-image process tree for --status / a live lock."""
    state = "alive" if holder.alive else "DEAD (stale lock)"
    lines = [f"held by PID {holder.pid} on {holder.tty} ({state})"]
    for proc in tree:
        indent = "  " * (proc.depth + 1)
        lines.append(f"{indent}{proc.pid}  {proc.cmdline or '(unknown)'}")
    return "\n".join(lines)


def _status(image: ResolvedImage) -> int:
    """Handle --status for ``image``: print the lock holder + in-image process tree.

    Read-only (PY-CORE-004): it never signals a process and never clears the lock. A free
    image prints a one-line "not in use"; a held one prints the holder (PID, tty, liveness)
    and the holder's in-image process tree.
    """
    lock = ImageLock(image.path, name=image.name)
    holder = lock.read_holder()
    if holder is None:
        print(f"{image.name}: not in use (no lock)")
        return 0
    tree = holder_tree(holder.pid) if holder.alive and holder.pid is not None else []
    print(_render_tree(holder, tree))
    return 0


def _resolve_live_lock(
    args: argparse.Namespace, image: ResolvedImage, lock: ImageLock, holder: LockHolder
) -> int:
    """Clear a *live* lock that blocks entry, or refuse. Return 0 to proceed, 9 to abort.

    The policy lives here at the boundary (it is a CLI/UX concern, not the lock library's),
    per ADR 0014:

    * ``--force`` → break the lock (stop the holder's whole process tree) and proceed, on a
      terminal or not;
    * **terminal, no --force** → show the holder and ask ``[y/N]``; No aborts (exit 9);
    * **no terminal, no --force** → refuse (``LockedError`` → exit 9) so automation never
      silently kills a running session.

    In every case the holder is described first — PID, tty, the in-image process tree, and
    any package manager mid-run (stopping ``dnf``/``rpm`` can corrupt the image's rpmdb) —
    so the operator sees exactly what is being stopped (or what is blocking them). A stale
    lock never reaches here: ``acquire()`` reclaims a dead holder's lock automatically.
    """
    tree = holder_tree(holder.pid) if holder.pid is not None else []
    pkg = running_package_manager(holder.pid) if holder.pid is not None else None
    rpmdb = f", running {pkg} (stopping it may corrupt the rpmdb)" if pkg else ""

    if not args.force:
        if not sys.stdin.isatty():
            # Non-interactive: never kill a live session behind the operator's back. Name
            # the holder and the two ways forward — free it on a terminal, or pass --force.
            raise LockedError(
                f"image {image.name!r} is held by a live session ({holder.raw}{rpmdb}); "
                f"refusing to break it non-interactively. Re-run on a terminal, inspect "
                f"with `lchroot --status {image.name}`, or pass --force to break the lock "
                f"and run anyway."
            )
        # Interactive: show what holds the image, then ask before stopping it.
        print(_render_tree(holder, tree))
        if not _confirm(
            f"clear the lock on {image.name} by stopping PID {holder.pid} "
            f"on {holder.tty}{rpmdb}, then enter?"
        ):
            log.warning("not cleared; %s is still locked — not entering", image.name)
            return 9
        log.warning("clearing the lock on %s as requested", image.name)
    else:
        # --force: say loudly what we are about to stop, then stop it (no prompt, no refuse).
        print(_render_tree(holder, tree))
        log.warning(
            "--force: breaking the lock on %s held by PID %s on %s%s",
            image.name,
            holder.pid,
            holder.tty,
            rpmdb,
        )

    report = lock.terminate_holder()
    log.warning(
        "stopped %d process(es) and cleared the lock on %s%s",
        len(report.signalled),
        image.name,
        " (required SIGKILL)" if report.escalated else "",
    )
    return 0


def _confirm(question: str) -> bool:
    """Ask a ``[y/N]`` question; default No.

    Returns False *without prompting* when stdin is not a tty, so non-interactive runs
    (cron/CI) never block on input or accidentally confirm a destructive action — without a
    terminal a live lock is refused outright unless ``--force`` is given.
    """
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _list_images(client: LunaClient | None) -> int:
    """Print osimage names (one per line) for completion/scripting; luna-backed.

    Not valid in ``--path`` mode (luna-free, so ``client`` is ``None``).
    """
    if client is None:
        log.error("--list-images needs luna and is not valid with --path")
        return 7
    for name in client.list_images():
        print(name)
    return 0


def _resolve_target(
    args: argparse.Namespace, client: LunaClient | None
) -> tuple[ResolvedImage, list[str]]:
    """Resolve the image + effective command — luna-free (``--path``) or by luna name.

    Assumes the caller already rejected the "neither --path nor osimage" case.

    Returns:
        ``(image, command)`` where ``command`` is empty for the image's default shell.

    Raises:
        ImageResolveError: if the path/name does not resolve to a safe rootfs.
        LchrootError: defensively, if a luna name is requested without a client.
    """
    if args.path:
        # There is no osimage-name positional in --path mode, so what argparse captured
        # as `osimage` is really the first word of the command — fold it back in.
        command = ([args.osimage] if args.osimage else []) + list(args.command)
        return resolve_local_path(args.path), command
    if client is None:  # defensive: main() always builds a client unless --path
        raise LchrootError("internal error: no luna client for osimage resolution")
    return client.resolve_image(args.osimage), list(args.command)


def run(args: argparse.Namespace, client: LunaClient | None, executor: Executor) -> int:  # noqa: PLR0911 - flat guard-clause dispatch (list-images / no-target / status / dry-run / no-command / locked / enter); one early return per gate is clearer than nesting
    """Resolve the image, build the sandbox, and enter it. Returns an exit code.

    Args:
        args: parsed CLI arguments.
        client: the Luna API client, or ``None`` in ``--path`` mode (which is luna-free:
            no token, HA, or sync). Injected for testability.
        executor: the command executor (injected for testability).

    Returns:
        The process exit code.
    """
    if args.list_images:
        return _list_images(client)

    # Resolve the target image either luna-free (by directory) or by luna osimage name.
    if not args.path and not args.osimage:
        log.error("an osimage name (or --path DIR) is required; see --list-images")
        return 7
    image, command = _resolve_target(args, client)

    if args.status:
        # Read-only lock inspection: print the holder + tree, never enter, never mutate.
        return _status(image)

    # HA gate (ADR 0011): images are customised only on the master controller so the
    # pair stays consistent — there is NO override. The gate applies ONLY when HA is
    # enabled (standalone installs are unaffected) and ONLY to a mutating (rw) entry;
    # --ro inspection cannot mutate and is allowed. --path mode is luna-free (client is
    # None) so it never gates.
    ha = client.ha_state() if client is not None else None
    if ha is not None and ha.enabled and not ha.master and not args.ro:
        raise SecondaryControllerError(
            f"refusing to enter {image.name!r} read-write on a non-master HA "
            "controller: images are customised on the master so the pair stays "
            "consistent. Run this on the master controller (or use --ro here to "
            "inspect read-only)."
        )

    argv = build_bwrap_argv(
        image, read_only=args.ro, command=command, hostname=args.hostname
    )

    if args.dry_run:
        print(render_plan(image, argv, read_only=args.ro))
        return 0

    # Without a terminal there is no interactive shell to drop into, so a bare entry would
    # launch a shell that reads EOF and exits having done nothing — looking like success
    # when nothing ran. Require an explicit command instead (ADR 0014). --dry-run above is
    # exempt: it runs nothing by design and says so.
    if not sys.stdin.isatty() and not command:
        log.error(
            "refusing to enter %s with no command on a non-interactive stdin: an "
            "interactive shell would read EOF and do nothing. Pass a command to run, "
            "e.g. `lchroot %s dnf -y install vim`.",
            image.name,
            image.name,
        )
        return 7

    # Foreign-arch images need qemu-user + a binfmt handler before any of their binaries
    # can run inside the sandbox. Set it up on demand (no-op for a native image, or if a
    # handler is already registered). Done here, not on --dry-run, so a plan never mutates
    # host binfmt state (PY-CORE-004).
    foreign = setup_emulation_for_entry(image.path, no_emulate=args.no_emulate)
    if foreign is not None:
        log.info("entering %s image %s under qemu-user emulation", foreign, image.name)

    # rw is the default (use --ro to inspect). A *live* lock blocks entry: --force breaks
    # it, a terminal prompts, and a non-interactive run is refused (ADR 0014). Decided at
    # the boundary, before acquiring. A stale lock (dead holder) needs none of this —
    # acquire() reclaims it automatically.
    lock = ImageLock(image.path, name=image.name)
    holder = lock.read_holder()
    if holder is not None and holder.alive:
        decision = _resolve_live_lock(args, image, lock, holder)
        if decision != 0:
            return decision

    with lock:
        lock.acquire()
        try:
            returncode = executor.run_interactive(argv)
        except OSError as exc:
            # bwrap missing / not executable surfaces as OSError from the launch; turn
            # it into a typed, clean error (-> exit 1) instead of a raw traceback.
            raise SandboxError(
                f"cannot launch the sandbox ({argv[0]!r}): {exc}; "
                "is bubblewrap (bwrap) installed?"
            ) from exc

    # No HA image sync on exit (ADR 0011, superseding 0004): the bash oracle's sync was a
    # no-op, and TrinityX keeps the controllers' images consistent by other means. lchroot
    # only enters the sandbox; it does not replicate. The master-only rw gate above is what
    # keeps mutation on the authoritative controller.
    return returncode


def _dispatch(args: argparse.Namespace) -> int:
    """Build dependencies and run the requested operation; no exit-code mapping.

    Kept separate from :func:`main` so ``main`` is a thin try/except that maps our typed
    errors to process exit codes (and so neither function grows too many return paths).
    """
    client: LunaClient | None = None
    if not args.path:
        # --path is luna-free by design: don't load luna.ini or build the client.
        config = load_api_config()
        if not config.verify_certificate:
            # Self-signed controller cert: silence urllib3's per-request warning once, at
            # the entrypoint, where the process-global side effect belongs (not in the
            # transport constructor — finding D11).
            silence_insecure_tls_warnings()
        transport: Transport = RequestsTransport(
            verify=config.verify_certificate, session=requests.Session()
        )
        client = LunaClient(config, transport)
    return run(args, client, Executor(dry_run=args.dry_run))


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911 - flat exception->exit-code dispatch; one return per error class is clearer than nesting
    """Parse arguments, run the operation, and map errors to exit codes.

    Returns:
        0 success; 7 config/image error; 8 emulation error; 9 locked; 10 non-master HA
        controller; 130 interrupt; 1 other.
    """
    # Slim runtimes (cron / systemd / initramfs) ship a thinner PATH than an interactive
    # shell; set it explicitly here at the entrypoint so behaviour is identical everywhere,
    # and importing the module has no side effect (PY-PORT-004, PY-FUNC-007).
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    try:
        parser = build_parser()
        raw = sys.argv[1:] if argv is None else argv
        args = parser.parse_args(_hoist_image_options(raw, parser))
        _configure_logging(args)
        return _dispatch(args)
    except LockedError as exc:
        log.error("%s", exc)
        return 9
    except SecondaryControllerError as exc:
        log.error("%s", exc)
        return 10
    except (ConfigError, ImageResolveError, LunaError) as exc:
        log.error("%s", exc)
        return 7
    except EmulationError as exc:
        log.error("%s", exc)
        return 8
    except LchrootError as exc:
        log.error("%s", exc)
        return 1
    except (KeyboardInterrupt, EOFError):
        # Ctrl-C / Ctrl-D: exit cleanly with no traceback (PY-ERR-007). Any held
        # lock is released by ImageLock.__exit__ as the exception unwinds.
        log.warning("interrupted")
        return 130
    except OSError as exc:
        # Top-level boundary backstop (PY-ERR-001 exemption): any unforeseen I/O
        # failure not already wrapped in a typed error (e.g. an unwritable lock file)
        # exits 1 with a clean message rather than a raw traceback. Network failures
        # are already translated to LunaError (-> 7) in the transport seam.
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
