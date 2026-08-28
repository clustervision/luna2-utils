# 8. `--path` mode: luna-free entry by rootfs directory

## Status
**Accepted (implemented 2026-06-01).** Sub-decision of ADR 0007 (foreign-arch). Adds
`src/lchroot/image.py` + a `--path` CLI flag; no existing behaviour changed.

## Context
lchroot resolves an image **by luna name** (ADR 0005): it loads `luna.ini`, fetches a token,
calls `GET /config/osimage/<name>`, checks HA state, and syncs on exit. Two real needs don't
fit that:

1. **Path-centric callers.** `luna osimage pack` and the TrinityX `image-create` playbook
   know a rootfs *path*, and creation happens *before* luna registration. ADR 0007 (goal 2/3)
   wants both to route their unsafe `chroot … dracut` through lchroot — but they can't always
   supply a luna name. The ARM proof drove this home: the `dracut-sysroot` ansible role had to
   hand-roll `mount + chroot + dracut` precisely because it couldn't call `lchroot <name>` for
   an image that may not be registered yet.
2. **Images not (yet) in luna**, e.g. one mid-build, or a throwaway rootfs.

## Decision
Add `lchroot --path DIR [command…]` that enters/inspects a rootfs **by directory, bypassing
luna ENTIRELY** — no `luna.ini`, no token, no HA check, no sync, no name resolution. It is the
luna-free sibling of name resolution, not a luna call with a different argument:

- `main()` builds **no** `LunaClient` when `--path` is set (so it works with luna down / absent).
  `run(args, client, …)` takes `client: LunaClient | None`; HA warning + sync are guarded on
  `client is not None`.
- `src/lchroot/image.py::resolve_local_path(raw)` applies the **same** rootfs guards as
  `LunaClient.resolve_image` (absolute, not `/`, is a dir, has `usr/`) — `--path` skips the
  *network*, never the *validation*. The image **name** is the directory basename (sandbox
  hostname/prompt); the **kernelversion** is read from `<rootfs>/lib/modules` (luna has no
  record to consult).
- The sandbox argv (`sandbox.py`) is **unchanged** — same hardening, cap-drop, `TMPDIR` pin,
  `--die-with-parent`. `--path` only changes *how the image is resolved*, not how it is entered.
- Argparse quirk handled: in `--path` mode there is no name positional, so the token argparse
  captured as `osimage` is really the first command word and is folded back into the command.
- Locking, `--status`, `--kill`, `--dry-run`, `--ro` all work in `--path` mode (luna-free).

## Consequences
- `dracut-sysroot` (and, later, `luna osimage pack`) can call
  `lchroot --path <image> dracut …` and get the cap-drop, PID/UTS namespaces, the **edit-lock**
  (no pack-vs-edit race — ADR 0007 goal 2), the `TMPDIR=/tmp` pin, and clean teardown — instead
  of a raw `chroot`. Foreign-arch works for free via the global binfmt `F` handler (no
  `emulation.py` needed for entry; that remains future work for *auto-detect/require*).
- `--list-images` stays luna-backed and errors if combined with `--path`.
- New unit module fully covered (`tests/test_image.py`); `run()` `--path` paths covered in
  `tests/test_main.py` with `client=None` (the None client is the proof of zero luna calls).

## Considered & rejected
- **Reuse name resolution with a synthetic name** — still loads luna.ini / token; fails the
  "luna may be absent/down" requirement. Rejected.
- **A separate `lchroot-path` entry point** — duplicates the entry/lock/sandbox plumbing.
  Rejected; one flag on one entry point.
- **Auto-deriving the name by reverse-looking-up luna** — reintroduces the luna dependency the
  flag exists to avoid. Rejected; basename is sufficient for hostname/prompt.
