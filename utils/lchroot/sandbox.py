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

"""Build the bubblewrap argv that enters an OS image. The security-critical core.

This is a pure function (no I/O) so it is exhaustively golden-tested. The capability
drop is **load-bearing**, not decoration: bwrap as root keeps ALL capabilities by
default, so without an explicit ``--cap-drop`` root inside the sandbox can
``mount -o remount,rw /`` and defeat a read-only bind (verified, skill.md L6).
Removing any hardening flag here is a security-relevant change requiring an ADR.
"""

from collections.abc import Sequence

from .types import ResolvedImage

# The fakeuname preload ships inside every image at /usr/lib64, so it resolves
# within the sandbox root without a host bind (skill.md L2).
FAKEUNAME_LIB = "libluna-fakeuname.so"
DEFAULT_SHELL = "/bin/bash"


def build_bwrap_argv(
    image: ResolvedImage,
    *,
    read_only: bool,
    command: Sequence[str] = (),
    hostname: str | None = None,
) -> list[str]:
    """Construct the full bwrap command line for entering ``image``.

    Args:
        image: the resolved image (name, path, optional kernelversion).
        read_only: if True, mount the image read-only and drop all capabilities
            (inspection mode); if False, mount read-write and drop only
            CAP_SYS_ADMIN so package installs work while the remount escape stays
            closed.
        command: command to run inside the sandbox; empty means the image's shell.
        hostname: override the sandbox's UTS hostname. Defaults to ``image.name``
            (the operator-facing identity). Automation callers that enter an image
            *on behalf of* the controller — e.g. the Ansible ``lchroot`` connection
            plugin — pass the controller's own hostname so that in-image steps see
            the same hostname a raw ``chroot`` would (raw chroot shares the host UTS
            namespace; lchroot does not, because of ``--unshare-uts``). This keeps
            UTS isolation intact — it only changes the *name*, not the namespacing.

    Returns:
        The argv list, ready for the executor (argv[0] is ``"bwrap"``).
    """
    root_bind = "--ro-bind" if read_only else "--bind"
    cap_drop = "ALL" if read_only else "CAP_SYS_ADMIN"
    mode = "ro" if read_only else "rw"
    sandbox_hostname = hostname if hostname is not None else image.name

    argv: list[str] = [
        "bwrap",
        root_bind,
        str(image.path),
        "/",
        # Namespaced virtual filesystems (replace lchroot's manual mounts).
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--ro-bind",
        "/sys",
        "/sys",
        "--tmpfs",
        "/run",
        # Isolation: private PID + UTS so scriptlet kill/systemctl cannot reach the
        # controller, and no host systemd/D-Bus socket is exposed.
        "--unshare-pid",
        "--unshare-uts",
        "--hostname",
        sandbox_hostname,
        # Tear the whole sandbox down when the session's main command exits or the
        # launcher dies (verified, skill.md L21 / ADR 0006). Without this, bwrap's
        # default pid-1 reaper *waits* for any backgrounded/`setsid` child, so a
        # detached process keeps the session alive (an `exit` appears to hang) and a
        # killed launcher leaves an orphaned bwrap+child tree on the host holding
        # locks (e.g. the image rpmdb). `--die-with-parent` makes the teardown
        # guarantee real: nothing you start inside survives leaving the image.
        "--die-with-parent",
        # Capability drop - load-bearing (see module docstring / skill.md L6).
        "--cap-drop",
        cap_drop,
        # fakeuname so uname -r reports the image's target kernel, not the host's.
        "--setenv",
        "LD_PRELOAD",
        FAKEUNAME_LIB,
    ]
    if image.kernelversion:
        argv += ["--setenv", "FAKE_KERN", image.kernelversion]
    argv += [
        # Pin TMPDIR inside the image: a host TMPDIR (e.g. a tool's /tmp/xxx) leaks
        # through the inherited env and breaks dnf/librepo with "No such file or
        # directory" (found by adversarial testing, skill.md L13).
        "--setenv",
        "TMPDIR",
        "/tmp",  # noqa: S108 # nosec B108 - the image's own /tmp inside the sandbox, not a host temp path
        # Tell systemd tooling the image's systemd is OFFLINE. Inside the sandbox the
        # image's init is never running (--unshare-pid), so `systemctl enable/preset`
        # must act on unit files, not a live manager. A raw chroot was auto-detected
        # as offline (Ansible's is_chroot(): /proc/1/root != /); under --unshare-pid
        # /proc/1 is the sandbox's own init so that heuristic fails, and `systemctl`
        # / Ansible's systemd module would try to talk to a (non-existent) D-Bus and
        # fail ("Service is in unknown state", "Failed to connect to bus"). The
        # documented SYSTEMD_OFFLINE=1 makes both degrade to file-only operations.
        "--setenv",
        "SYSTEMD_OFFLINE",
        "1",
        # Present as a chroot to tooling that predates/ignores SYSTEMD_OFFLINE:
        # debian_chroot flips Ansible's is_chroot() to True (under --unshare-pid the
        # /proc/1/root heuristic wrongly reports a live system).
        "--setenv",
        "debian_chroot",
        "lchroot",
        "--setenv",
        "PS1",
        rf"lchroot({mode}) [\u@{image.name} \W]\$ ",
        "--chdir",
        "/",
        "--",
        *(command if command else [DEFAULT_SHELL]),
    ]
    return argv


def render_plan(image: ResolvedImage, argv: Sequence[str], *, read_only: bool) -> str:
    """Render a human-reviewable dry-run plan: the mode, the image, and the argv.

    Args:
        image: the resolved image.
        argv: the bwrap argv that would be executed.
        read_only: whether this is read-only (inspection) entry.

    Returns:
        A multi-line string for printing to stdout.
    """
    mode = "READ-ONLY (inspection)" if read_only else "READ-WRITE (mutates the image)"
    lines = [
        f"lchroot plan: enter {image.name} [{mode}]",
        f"  image path     : {image.path}",
        f"  kernelversion  : {image.kernelversion or '(unknown - no FAKE_KERN)'}",
        "  bwrap command  :",
        f"    {' '.join(argv)}",
    ]
    return "\n".join(lines)
