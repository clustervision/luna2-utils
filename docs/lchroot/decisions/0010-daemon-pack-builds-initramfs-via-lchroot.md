# 10. `luna osimage pack` builds the initramfs via `lchroot --path`

Date: 2026-06-02

Status: accepted (depends on [0008](0008-path-mode-luna-free-entry.md); the lock
behaviour folds in the pack-lock contract — see "Consequences")

## Context

`luna osimage pack` rebuilds an image's initramfs by running `dracut` *inside* the
image's rootfs. The luna2-daemon historically did this the same way the legacy bash
`lchroot` did: it **bind-mounted `/dev` `/proc` `/sys` onto the host mount table and
`os.chroot()`ed** into the image to invoke dracut, then unmounted on the way out.

That is exactly the class of operation lchroot was built to replace, and it carries the
same hazards inside the daemon process:

1. **Host-visible mounts that leak.** The `/dev` `/proc` `/sys` binds live on the
   controller's real mount table; a crash, exception, or `kill -9` between mount and
   unmount leaves them dangling on the controller.
2. **No edit-lock.** A `pack` and an interactive `lchroot <image>` edit session could run
   against the same rootfs/rpmdb concurrently and corrupt it — nothing coordinated them.
3. **No foreign-arch support.** A raw `os.chroot` cannot run aarch64 binaries on an x86
   controller, so dracut inside a foreign-arch image could not run at all.
4. **`os.chroot` in the long-lived daemon process** is itself a risk (a missed
   `os.fchdir` back out changes the daemon's view of the filesystem).

lchroot already solves all four for the interactive case, and `--path DIR` (ADR 0008)
gives a luna-free, directory-addressed entry point suitable for a path-centric caller
like pack.

## Decision

The daemon's pack operation builds the initramfs by running dracut **inside the image
under `lchroot --path <image_path>`**, not by mounting + `os.chroot`. Concretely
(`daemon/plugins/osimage/operations/image/default.py`), every in-image step —
`dracut --list-modules` and the `dracut --force --kver …` build — runs as
`lchroot --path <image> -- <dracut command>`. The daemon no longer touches host mounts
or chroots itself; the built initrd still lands at `<image>/tmp/<ramdisk_file>` and is
moved into place exactly as before.

Two contract points are load-bearing:

- **Pack must NOT `--force` a locked image.** Forcing entry past a live edit-lock can let
  pack and an interactive session race the same rpmdb and corrupt it. So pack invokes
  lchroot **without `--force`**: if the image is locked, lchroot exits `9` with its own
  message — `image '<name>' is locked (…); inspect with `lchroot --status <name>` or take
  it over with `lchroot --kill <name>`` — and the daemon **surfaces that message
  verbatim** (it returns lchroot's captured stderr to the caller) rather than inventing
  its own or packing blindly. The warning comes *from* lchroot.
- **lchroot is a hard runtime dependency of pack.** Because pack now shells out to the
  `lchroot` console script (`utils.lchroot.__main__:main`), luna2-utils — which ships
  lchroot — **must be installed before the daemon can pack an image**. This is a new,
  explicit ordering dependency between the two packages.

## Consequences

- Pack inherits lchroot's guarantees: a private mount namespace (no host-visible
  `/dev` `/proc` `/sys`, nothing to leak on crash), the per-image edit-lock (a concurrent
  edit and a pack can no longer race the same rootfs), capability drops, `TMPDIR=/tmp`
  pinning, clean teardown on failure, and — via `qemu-static`/binfmt (ADR 0009) —
  **foreign-arch images become packable** on a native controller.
- The daemon's pack code shrank (it deleted its mount/chroot/unmount bookkeeping) and no
  longer performs privileged host-mount operations or `os.chroot` in-process.
- **Deployment ordering is now significant:** luna2-utils (lchroot) must be present before
  the daemon runs a pack. Packaging/install must order this; a missing `lchroot` surfaces
  as a clear pack failure (lchroot/console-script not found), not a silent fallback.
- Proven live: the daemon branch `pack-via-lchroot` routes dracut through
  `lchroot --path` and packs successfully on a real controller.
