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

"""lchroot's foreign-arch adapter: delegate emulation setup to ``qemu_static``.

lchroot does **not** own the qemu interpreters or binfmt registration -- that is
``utils.qemu_static``'s job (ADR 0009). This thin adapter only decides, at entry time,
whether the image is foreign and then either delegates the setup or, under ``--no-emulate``,
refuses. It translates ``qemu_static.QemuError`` into lchroot's :class:`EmulationError` so
``__main__`` maps it to exit code 8.
"""

import logging
from pathlib import Path

from .. import qemu_static
from .errors import EmulationError

log = logging.getLogger(__name__)


def setup_emulation_for_entry(rootfs: Path, *, no_emulate: bool) -> str | None:
    """Set up emulation if ``rootfs`` is foreign; return its arch, or None if native.

    Args:
        rootfs: the validated image root about to be entered.
        no_emulate: if True, refuse to set up emulation (raise) rather than delegating.

    Returns:
        The foreign architecture that was set up, or ``None`` if the image is native to the
        host (or its architecture could not be determined).

    Raises:
        EmulationError: if the image is foreign and emulation is refused (``--no-emulate``)
            or cannot be arranged by qemu_static.
    """
    host = qemu_static.host_arch()
    image = qemu_static.detect_image_arch(rootfs)
    if image is None or image == host:
        return None
    if no_emulate:
        raise EmulationError(
            f"image is {image} on a {host} host but --no-emulate was given"
        )
    try:
        return qemu_static.ensure_for_arch(image)
    except qemu_static.QemuError as exc:
        raise EmulationError(str(exc)) from exc
