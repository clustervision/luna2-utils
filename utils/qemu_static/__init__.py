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

"""qemu-static: bundle qemu-user interpreters and register binfmt_misc handlers.

The single owner of foreign-architecture *capability* in luna2-utils. lchroot imports this
to set up emulation on foreign-arch entry; the ``qemu-static`` CLI exposes the same for
callers that run foreign binaries outside lchroot (e.g. ``dnf --installroot --forcearch``).
See ADR 0009.
"""

from .registry import (
    Arch,
    QemuError,
    detect_image_arch,
    ensure_for_arch,
    ensure_for_image,
    host_arch,
    supported_arches,
)

__all__ = [
    "Arch",
    "QemuError",
    "detect_image_arch",
    "ensure_for_arch",
    "ensure_for_image",
    "host_arch",
    "supported_arches",
]
