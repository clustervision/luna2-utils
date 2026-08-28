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

"""Typed exception hierarchy. One package base; subclass per domain error.

Library code raises these; only ``__main__`` maps them to process exit codes
(PY-CORE-006, PY-ERR-003).
"""


class LchrootError(Exception):
    """Base for every error this package raises; catch this to catch all of ours."""


class ConfigError(LchrootError):
    """luna.ini is missing, unreadable, malformed, or missing a required key."""


class LunaError(LchrootError):
    """The Luna API returned an error or an unusable response."""


class ImageResolveError(LchrootError):
    """The osimage could not be resolved to a valid, safe rootfs path."""


class LockedError(LchrootError):
    """The image is in use by a live session.

    Inspect the holder with ``--status``; break it with ``--force``. A stale lock from a
    dead holder is reclaimed automatically and does not raise this.
    """


class SandboxError(LchrootError):
    """Entering the bubblewrap sandbox failed (e.g. ``bwrap`` is missing/unexecutable)."""


class SecondaryControllerError(LchrootError):
    """Refused: this is a non-master HA controller; images are customised on the master.

    Raised for a read-write entry when HA is enabled and this host is not the master.
    There is no override (ADR 0011): mutating an image on the secondary would diverge the
    pair. ``--ro`` inspection is allowed (it cannot mutate), and standalone (non-HA)
    installs never hit this.
    """


class EmulationError(LchrootError):
    """Foreign-arch emulation could not be set up (missing qemu binary, binfmt, etc.)."""
