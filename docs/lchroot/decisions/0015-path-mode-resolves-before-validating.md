# 0015 — `--path` mode fully resolves the path before validating it

- **Status:** Accepted — 2026-07-08
- **Supersedes:** none (hardens the check introduced with ADR 0008 `--path` mode)

## Context
`resolve_local_path` (the `--path` entry, luna-free) validated the *literal* operator-supplied
path: `Path(raw) == Path("/")`, `is_absolute()`, `is_dir()`, and a `usr/` probe. `PurePath`
comparison is textual, so a traversal (`--path /trinity/images/x/../../../../..`) or a symlink
pointing at `/` passed the "refusing unsafe image path" guard: the string was not `"/"`, the
directory existed, and `<path>/usr` resolved (through the `..`) to the host `/usr`. lchroot then
`--bind`s that path read-write into the sandbox — i.e. it would rw-bind the **host root**. The
module docstring claimed `--path` is "no less safe than name resolution"; it was not. (Review
finding D1. The sibling `qemu_static.registry.detect_image_arch` already resolves before its
symlink-escape check — the discipline existed, just not here.)

## Decision
Resolve the path to its real location **before** any safety check:
1. reject a non-absolute path up front (clear message);
2. `Path(raw).resolve(strict=True)` — collapses `..` and follows symlinks, and raises if the
   target does not exist (→ clean `ImageResolveError`, exit 7, instead of a later confusing
   "not a rootfs");
3. reject the resolved path if it is `/`;
4. keep the existing directory + `usr/` rootfs probe on the resolved path;
5. take the image *name* from the **resolved** basename, and bind the **resolved** path.

## Consequences
- The traversal/symlink escape is closed: any input that resolves to `/` (or does not exist) is
  refused before a bind happens. Regression tests: `test_resolve_refuses_traversal_escaping_to_root`,
  `test_resolve_refuses_symlink_pointing_at_root` (both fail on the pre-fix code).
- **Behavioural change (intended):** a symlink to a real rootfs is now entered under its *target*
  name/path, not the link name (`test_resolve_follows_symlink_to_real_rootfs`). This matches how
  bwrap would resolve the bind anyway and removes the ambiguity; callers that relied on the link
  basename as the hostname should pass `--hostname`.
- A non-existent `--path` now fails with "does not exist" (exit 7) rather than "not a rootfs".
- No change to luna-name resolution or to the sandbox hardening flags.

## Considered alternatives
- *Keep the literal check, add a separate `..`/symlink scan* — rejected: re-implements what
  `Path.resolve()` does correctly in one call (KISS); easy to get subtly wrong.
- *Resolve non-strict (don't require existence)* — rejected: a non-existent rootfs can never be a
  valid target, and strict mode gives the operator an accurate, early error.
