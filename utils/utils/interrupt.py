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


import sys

INTERRUPT_MESSAGE = 'Keyboard Interrupted.'
# 128 + SIGINT: what a shell reports for a command ended by Ctrl-C
INTERRUPT_EXIT_CODE = 130


def exit_on_interrupt(func, *args, **kwargs):
    """
    Run a tool's main function and return what it returns. Ctrl-C ends the run
    with a one-line message on stderr and exit code 130 instead of a traceback.
    """
    try:
        return func(*args, **kwargs)
    except KeyboardInterrupt:
        # leading newline: the terminal has echoed ^C with the cursor still on that line
        sys.stderr.write(f'\n{INTERRUPT_MESSAGE}\n')
        sys.exit(INTERRUPT_EXIT_CODE)
