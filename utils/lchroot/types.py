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

"""Shared, dependency-free data records for the lchroot package.

Kept in its own module so the *producers* of a resolved image (``luna`` name resolution,
``image`` path resolution) and its *consumer* (``sandbox`` argv builder) all import the
record from a neutral place. Previously ``ResolvedImage`` lived in ``sandbox`` — the argv
builder — so the resolvers imported their own output type from the module that consumes it,
pointing the dependency arrow the wrong way and pulling the whole bwrap builder into
``--path`` mode transitively (review finding D9).
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResolvedImage:
    """An osimage resolved to a validated rootfs path and its target kernel."""

    name: str
    path: Path
    kernelversion: str | None
