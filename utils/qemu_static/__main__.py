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

"""qemu-static CLI: register the qemu-user binfmt handler for a foreign architecture.

For callers that run foreign-arch binaries *outside* lchroot (e.g. the foreign-bootstrap
``dnf --installroot --forcearch`` step). lchroot itself imports the library directly.
"""

import argparse
import logging
from pathlib import Path

from .registry import QemuError, ensure_for_arch, ensure_for_image, supported_arches

log = logging.getLogger(__name__)

_EXAMPLES = """\
qemu-static registers a binfmt_misc handler so this host can transparently run
foreign-architecture binaries through a bundled static QEMU. It is idempotent
(safe to re-run). lchroot sets this up for you automatically; use this command
only to run foreign binaries OUTSIDE lchroot — e.g. dnf --installroot --forcearch.

examples:
  qemu-static aarch64                          enable ARM64 emulation on this host
  qemu-static --image /trinity/images/arm-img  detect the arch from a rootfs dir
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="qemu-static",
        description="Enable a foreign CPU architecture on this host "
        "(qemu-user binfmt handler).",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "arch",
        nargs="?",
        help=f"target architecture to enable ({', '.join(supported_arches())})",
    )
    parser.add_argument(
        "--image",
        metavar="DIR",
        help="detect the architecture from a rootfs directory instead of naming it",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show what it is doing (INFO-level logging)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Register the handler for the requested arch (or a rootfs's arch).

    Returns:
        0 on success, 1 on a QemuError, 130 on Ctrl-C. (argparse exits 2 itself on a
        usage error — a missing/duplicate ARCH/--image.)
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    if bool(args.arch) == bool(args.image):
        parser.error("give exactly one of ARCH or --image DIR")
    try:
        arch = (
            ensure_for_image(Path(args.image))
            if args.image
            else ensure_for_arch(args.arch)
        )
    except QemuError as exc:
        log.error("%s", exc)
        return 1
    except (KeyboardInterrupt, EOFError):
        log.warning("interrupted")
        return 130
    target = args.arch or args.image
    log.info("emulation ready for %s", arch or f"{target} (native; nothing to do)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
