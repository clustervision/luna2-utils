#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

# This code is part of the TrinityX software suite
# Copyright (C) 2023  ClusterVision Solutions b.v.
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



"""Console-script shim that runs the legacy bash ``lchroot`` (``lchroot-legacy``)."""

import subprocess
import sys
from pathlib import Path


def lchroot_legacy() -> int:
    """Run the legacy bash lchroot (kept as the ``lchroot-legacy`` console script).

    The canonical ``lchroot`` command now points at the bubblewrap-based Python
    implementation in ``utils.lchroot``; this keeps the original bash chroot available as a
    fallback (and the behavioural oracle) under ``lchroot-legacy``.

    Runs the script with **bash** (its shebang is ``#!/bin/bash`` and it uses bashisms — a
    bare ``sh`` is dash on Debian/Ubuntu and would break it), forwarding the caller's
    arguments as a real argv list — never a shell string — so there is no injection surface.
    """
    script = Path(__file__).resolve().parent / "lchroot-legacy"
    proc = subprocess.Popen(["bash", str(script), *sys.argv[1:]])
    while True:
        try:
            rc = proc.wait()
            # killed by a signal: report it the shell way (128+N), not as a negative number
            return 128 - rc if rc < 0 else rc
        except KeyboardInterrupt:
            # Ctrl-C reaches bash too; keep waiting so its EXIT trap can unmount the
            # image — subprocess.call would SIGKILL bash here and skip that clean-up
            continue
