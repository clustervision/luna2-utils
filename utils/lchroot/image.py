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

"""Resolve a rootfs directly from a filesystem path, with **no** Luna involvement.

This backs lchroot's ``--path`` mode: enter or inspect a rootfs by directory, bypassing
the Luna API entirely (no token, no HA state, no sync, no name resolution). It exists for
two cases (ADR 0008, a sub-decision of ADR 0007):

* an image that is not (yet) registered with luna — e.g. one mid-build; and
* path-centric callers such as ``luna osimage pack`` or an image-create playbook that know
  a rootfs path but not (or not reliably) a luna name.

The same rootfs safety checks Luna resolution applies are enforced here, so ``--path`` is
no less safe than name resolution — it only skips the *network*, not the validation.
"""

import re
from pathlib import Path

from .errors import ImageResolveError
from .types import ResolvedImage


def _kernel_sort_key(name: str) -> list[int]:
    """Return a numeric-aware sort key for a kernel-module directory name.

    A kernel version such as ``5.14.0-687.17.1.el9_8.x86_64`` must sort *above*
    ``5.9.0-...``; a plain lexical sort gets this wrong (``"5.9" > "5.14"`` as strings).
    We compare the sequences of decimal runs, so ``[5, 14, 0, ...] > [5, 9, 0, ...]``.
    """
    return [int(run) for run in re.findall(r"\d+", name)]


def detect_kernelversion(rootfs: Path) -> str | None:
    """Return the kernel version to spoof, by inspecting ``<rootfs>/lib/modules``.

    Luna reports ``kernelversion`` from its database; with a bare path we have no such
    record, so we read it from the image itself. Returns the highest module directory name
    by numeric version order, or ``None`` when there is no kernel (then ``uname`` inside
    reports the host kernel, matching the no-kernelversion luna case).

    Args:
        rootfs: the validated image root.

    Returns:
        The kernel version string, or ``None`` if no kernel modules are present.
    """
    modules = rootfs / "lib" / "modules"
    if not modules.is_dir():
        return None
    versions = [p.name for p in modules.iterdir() if p.is_dir()]
    return max(versions, key=_kernel_sort_key) if versions else None


def resolve_local_path(raw: str) -> ResolvedImage:
    """Validate a rootfs directory and build a :class:`ResolvedImage` (no Luna calls).

    Applies the same guards as :meth:`LunaClient.resolve_image`: the path is first
    **fully resolved** (symlinks + ``..`` collapsed) so the checks below cannot be fooled
    by a traversal (``/trinity/images/x/../../..``) or a symlink into ``/`` — either would
    otherwise pass a textual ``!= "/"`` check and rw-bind the host root into the sandbox.
    The resolved path must not be ``/``, must be a directory, and must look like a rootfs
    (have a ``usr/``). The image *name* is taken from the resolved directory basename (used
    for the sandbox hostname and prompt); the kernel version is read from the image.

    Args:
        raw: the rootfs directory path.

    Returns:
        The resolved image.

    Raises:
        ImageResolveError: if the path does not exist, is unsafe (resolves to ``/``), or is
            not a rootfs.
    """
    raw_path = Path(raw)
    if not raw_path.is_absolute():
        raise ImageResolveError(f"refusing unsafe image path {raw!r} (not absolute)")
    try:
        path = raw_path.resolve(strict=True)
    except OSError as exc:
        raise ImageResolveError(f"image path {raw!r} does not exist") from exc
    if path == Path("/"):
        raise ImageResolveError(f"refusing unsafe image path {raw!r} (resolves to /)")
    if not path.is_dir() or not (path / "usr").is_dir():
        raise ImageResolveError(f"image path {path} is not a rootfs (no usr/)")
    return ResolvedImage(
        name=path.name, path=path, kernelversion=detect_kernelversion(path)
    )
