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

"""lchroot: a bubblewrap-based, security-hardened replacement for TrinityX lchroot.

Enters an OS image (resolved via the Luna API) inside a bubblewrap sandbox so an
operator can customise it without endangering the controller. Engineering standard:
``docs/lchroot/AGENTS.md`` (which instantiates the shared engineering-standards Python
kit). The package version is owned by packaging (``VERSION.txt`` / ``setup.py``).
"""
