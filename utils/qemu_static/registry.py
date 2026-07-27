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

"""Select and register the qemu-user interpreter for a foreign architecture.

This is the single owner of foreign-arch *capability* in luna2-utils: it bundles the
static ``qemu-<arch>-static`` interpreters and registers ``binfmt_misc`` handlers so the
host kernel can run foreign-arch ELF binaries. It is deliberately independent of lchroot:

* lchroot imports it to set up emulation when it enters a foreign rootfs, and
* callers that run foreign binaries *outside* lchroot use the ``qemu-static`` CLI -- most
  importantly the foreign-bootstrap ``dnf --installroot --forcearch`` step, whose package
  scriptlets execute under the system binfmt handler, not under lchroot.

Mechanics (see ADR 0009): binfmt_misc holds one handler per target architecture, each
matching the target's ELF ``e_machine`` and pointing at an interpreter that must be
executable on the *host* CPU. The ``F`` (fix-binary) flag makes the kernel open the
interpreter at registration time, so the path only needs validity at that instant -- which
is why the interpreter can live inside this package rather than in ``/usr/bin`` and still
work inside a bubblewrap mount namespace. Registration is privileged (root); this tool
runs as root.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)


class QemuError(Exception):
    """Foreign-arch emulation could not be set up (missing interpreter, binfmt, etc.)."""


# --- ELF header parsing -----------------------------------------------------------------
_ELF_MAGIC = b"\x7fELF"
_ELF_HEADER_MIN = 20  # bytes needed to reach e_machine (offset 18-19)
_EI_DATA = 5  # byte index of the endianness marker
_ELFDATA2LSB = 1  # EI_DATA value for little-endian
_E_MACHINE = slice(18, 20)  # 2-byte e_machine field


@dataclass(frozen=True, slots=True)
class Arch:
    """A supported architecture and how to emulate it via qemu-user + binfmt_misc."""

    name: str  # canonical name
    e_machine: int  # ELF e_machine value identifying this arch
    uname: tuple[str, ...]  # os.uname().machine spellings that map here
    qemu: str  # bundled interpreter filename (qemu-<arch>-static)
    binfmt: str  # /proc/sys/fs/binfmt_misc handler name
    magic: str  # binfmt magic, \xNN-escaped (parsed by the kernel)
    mask: str  # binfmt mask, \xNN-escaped


# binfmt magic/mask are the standard qemu-binfmt-conf values; the kernel parses the
# ``\xNN`` escapes. The interpreter is supplied at registration, not hard-coded here.
_ARCHES: dict[str, Arch] = {
    "x86_64": Arch(
        name="x86_64",
        e_machine=0x3E,
        uname=("x86_64", "amd64"),
        qemu="qemu-x86_64-static",
        binfmt="qemu-x86_64",
        magic=r"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x3e\x00",
        mask=r"\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff",
    ),
    "aarch64": Arch(
        name="aarch64",
        e_machine=0xB7,
        uname=("aarch64", "arm64"),
        qemu="qemu-aarch64-static",
        binfmt="qemu-aarch64",
        magic=r"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00",
        mask=r"\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff",
    ),
    "riscv64": Arch(
        name="riscv64",
        e_machine=0xF3,
        uname=("riscv64",),
        qemu="qemu-riscv64-static",
        binfmt="qemu-riscv64",
        magic=r"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xf3\x00",
        mask=r"\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff",
    ),
}
_BY_E_MACHINE = {arch.e_machine: arch.name for arch in _ARCHES.values()}

# Real, non-symlinked ELF binaries virtually every image carries; the first that resolves
# *inside* the rootfs and parses as ELF determines the image architecture.
_SNIFF_CANDIDATES = (
    "usr/lib/systemd/systemd",
    "usr/bin/bash",
    "usr/bin/uname",
    "bin/bash",
)

_BINFMT_DIR = Path("/proc/sys/fs/binfmt_misc")
_REGISTER = _BINFMT_DIR / "register"


def supported_arches() -> tuple[str, ...]:
    """Return the architecture names this tool knows how to emulate."""
    return tuple(_ARCHES)


def _arch_from_elf(header: bytes) -> str | None:
    """Return the canonical arch name for an ELF ``header``, or ``None`` if not ours."""
    if len(header) < _ELF_HEADER_MIN or header[:4] != _ELF_MAGIC:
        return None
    endian: Literal["little", "big"] = (
        "little" if header[_EI_DATA] == _ELFDATA2LSB else "big"
    )
    return _BY_E_MACHINE.get(int.from_bytes(header[_E_MACHINE], endian))


def host_arch() -> str:
    """Return the controller's canonical architecture name (from ``os.uname()``)."""
    machine = os.uname().machine
    for arch in _ARCHES.values():
        if machine in arch.uname:
            return arch.name
    return machine


def detect_image_arch(rootfs: Path) -> str | None:
    """Determine a rootfs's architecture by sniffing a representative binary's ELF header.

    Only files resolving to a path *inside* ``rootfs`` are read, so an absolute symlink
    (e.g. ``/bin/sh`` -> ``/usr/bin/bash``) cannot make us read a host binary and report
    the host's architecture by mistake.

    Args:
        rootfs: the image root.

    Returns:
        The architecture name, or ``None`` if no candidate binary could be identified.
    """
    root = rootfs.resolve()
    for relative in _SNIFF_CANDIDATES:
        real = Path(os.path.realpath(root / relative))
        if real != root and root not in real.parents:
            continue  # symlink escaped the rootfs; never read a host binary
        try:
            # read only the ELF header, not the whole (multi-MB) binary
            with real.open("rb") as handle:
                header = handle.read(_ELF_HEADER_MIN)
        except OSError:
            continue
        arch = _arch_from_elf(header)
        if arch is not None:
            return arch
    return None


def _bundled_qemu(arch: Arch) -> Path:
    """Path to the qemu interpreter shipped alongside this package for ``arch``."""
    return Path(__file__).resolve().parent / arch.qemu


def _is_registered(binfmt: str) -> bool:
    """True if an *enabled* binfmt_misc handler named ``binfmt`` is present."""
    try:
        return "enabled" in (_BINFMT_DIR / binfmt).read_text(encoding="ascii")
    except OSError:
        return False


def _register(arch: Arch, interpreter: Path) -> None:
    """Register a binfmt_misc handler for ``arch`` pointing at ``interpreter`` (F flag).

    Raises:
        QemuError: if binfmt_misc is unavailable or the registration write fails.
    """
    if not _BINFMT_DIR.is_dir():
        raise QemuError(
            "binfmt_misc is not available (mount it: "
            "mount -t binfmt_misc none /proc/sys/fs/binfmt_misc)"
        )
    line = f":{arch.binfmt}:M::{arch.magic}:{arch.mask}:{interpreter}:F\n"
    try:
        _REGISTER.write_text(line, encoding="ascii")
    except OSError as exc:
        raise QemuError(
            f"failed to register binfmt handler {arch.binfmt}: {exc}"
        ) from exc


def ensure_for_arch(target: str) -> str | None:
    """Ensure a binfmt handler for ``target`` arch on this host; return it, or None.

    Returns ``None`` when ``target`` is the host's own architecture (nothing to emulate).
    Idempotent: an already-enabled handler (whatever its interpreter) is left as-is;
    otherwise this package's bundled interpreter is registered.

    Raises:
        QemuError: if ``target`` is unsupported, the bundled qemu interpreter is missing,
            or registration fails.
    """
    host = host_arch()
    if target == host:
        return None
    arch = _ARCHES.get(target)
    if arch is None:
        raise QemuError(f"unsupported foreign architecture {target!r} (host is {host})")
    if _is_registered(arch.binfmt):
        log.debug("binfmt handler %s already registered", arch.binfmt)
        return target
    interpreter = _bundled_qemu(arch)
    if not interpreter.is_file():
        raise QemuError(
            f"cannot emulate {target} on {host}: no bundled {arch.qemu} "
            f"(expected at {interpreter})"
        )
    _register(arch, interpreter)
    log.info("registered binfmt handler %s -> %s", arch.binfmt, interpreter)
    return target


def ensure_for_image(rootfs: Path) -> str | None:
    """Detect ``rootfs``'s architecture and ensure emulation if it is foreign.

    Returns the foreign arch that was set up, or ``None`` if the image is native to the
    host (or its arch could not be determined).

    Raises:
        QemuError: if the image is foreign and emulation cannot be arranged.
    """
    image = detect_image_arch(rootfs)
    if image is None:
        return None
    return ensure_for_arch(image)
