# 5. resolve_image refuses when two osimages share one rootfs path

## Status
Accepted — 2026-05-31

## Context
`lchroot <name>` resolves an osimage name to a rootfs path via the luna API and then
**mutates that path** (rw is the default). The resolver already refuses obviously
unsafe paths (`/`, non-absolute, not-a-rootfs). One ambiguity was listed in the
regression matrix (row A6, "two images same path → refuse") but never implemented.

Luna should never map two osimage names to the same rootfs path. The operator
confirmed: if it ever does, **that is a luna bug**, not a supported configuration. But
if it happens, `lchroot <name>` would silently enter and modify an image that another
name also points at — an ambiguous destructive target. We would rather catch luna's
bug loudly than write through it.

## Decision
After validating the path, `LunaClient.resolve_image` cross-checks the osimage
**collection** (`GET /config/osimage`) and refuses with `ImageResolveError` if more
than one osimage resolves (via `Path.resolve()`, so symlinks/`.`/trailing-slash
normalize) to the same path as the requested one.

The check is **best-effort** (PY-ERR-006): if the collection fetch fails
(transport/Luna error), it is logged at debug and skipped — a daemon hiccup must not
turn an otherwise-valid resolve into a failure for every entry. The guard only ever
*adds* a refusal for a genuinely ambiguous, confirmed-duplicate case; it never blocks
on uncertainty.

Pinned by `tests/test_luna.py`:
`test_resolve_refuses_ambiguous_shared_path` (two names, one path → refuse),
`test_resolve_unaffected_when_collection_fetch_fails` (best-effort), and the existing
single-image happy-path tests (now register a single-entry collection → unaffected).

## Consequences
- A luna misconfiguration that aliases two names to one image is caught before any
  mutation, with a message that names the clashing images.
- `resolve_image` now makes a second API call (the collection) on the resolve path.
  It is best-effort, so it adds latency but no new hard failure mode; the existing
  `list_images` already hits the same endpoint, so the shape is familiar.
- New observable behaviour → this ADR + tests-first, per the repo's change rule.

## Considered alternatives
- **Do nothing (trust luna)** — rejected: the resolver is the last line before a
  destructive write; "trust the upstream" is exactly what the path-safety checks
  already decline to do.
- **Make the collection fetch mandatory (fail closed if it errors)** — rejected: that
  would let a transient daemon issue block all entries, trading a rare luna bug for a
  common outage amplifier. Fail-open is correct for a *secondary* safety cross-check.
- **Compare raw path strings instead of `Path.resolve()`** — rejected: `/a/b` vs
  `/a/b/` vs a symlinked path would both false-miss and false-hit.
