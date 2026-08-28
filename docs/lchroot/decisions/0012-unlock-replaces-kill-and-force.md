# 12. `--unlock` replaces the `--kill`/`--force` pair (one verb to free a lock)

## Status
Accepted — 2026-06-03 (supersedes the CLI surface of ADRs 0002 and 0003: the `--kill`
command and the `--force` takeover are removed. `--status` from 0002 stands unchanged.
The low-level `ImageLock.acquire(force=...)` *steal* primitive and `terminate_holder`
from 0002 are unchanged; only the CLI flags change.)

## Context
By ADR 0003 the lock-administration surface was three verbs: `--status` (look), `--kill`
(stop the holder, no prompt, scriptable), and `--force` (stop-then-*enter*, with a y/N
confirmation). Reviewing the CLI as a whole (TODO #18), the operator found the
`--kill`/`--force` pair genuinely confusing:

- The **names point the wrong way.** `--kill` is the *non-interactive* one (no prompt);
  `--force` is the *interactive* one (prompts). Nothing in the words conveys that — if
  anything "force" sounds more unattended than "kill".
- They are **different verbs, not two intensities.** `--kill` evicts *and exits*;
  `--force` evicts *and then enters*. The names hide that difference entirely.
- The motivating need is simpler than either flag. When `osimage pack` fails on a locked
  image, the operator does not know who holds it and just wants to **free it without
  entering**, then re-run pack. That is exactly what `--kill` did — but under a name that
  also reads as "take over and work in it".

The operator's instruction: *drop `--kill`/`--force` entirely; have one "clear lock"
operation that unwraps the lock and the bubble. Confirm on a tty, refuse otherwise.*
The name **`--unlock`** was chosen so it pairs with the "image is locked" error message
the operator just read — locked → unlock.

## Decision
The lock surface is now **two verbs**: `--status` and `--unlock`.

- **`--status <image>`** (read-only, unchanged from 0002): print the holder (PID, tty,
  liveness) and the in-image process tree. Zero side effects.
- **`--unlock <image>`**: clear the lock and tear down the holding session (the "bubble"),
  then **exit without entering**. It stops the holder's whole process tree (SIGTERM →
  SIGKILL survivors; killing the outer `bwrap` collapses the `--unshare-pid` namespace)
  and removes the lock. Behaviour by holder state:
  - **stale lock (dead holder)** → cleared silently (no prompt, nothing to kill);
  - **live holder on a tty** → print the tree + a package-manager/rpmdb warning, then ask
    `clear the lock … ? [y/N]` (default No). y → stop + clear (exit 0); N → abort, holder
    untouched (exit 9);
  - **live holder, no tty** (cron/CI/pipe) → **refuse** (exit 9) with a message to re-run
    on a terminal. Automation never silently kills a running session.
  `--dry-run` prints what it would stop and changes nothing (PY-CORE-004).

There is **no `--force` and no in-line takeover.** To enter an image that is held, the
operator frees it explicitly (`--unlock`) and then runs `lchroot <image>` normally — two
deliberate steps instead of one flag with hidden semantics. The plain entry path is
unchanged: a *live* lock fails fast with exit 9 and the `--status`/`--unlock` guidance; a
*stale* lock is auto-reclaimed by `acquire()` (ADR 0002 behaviour).

The confirmation lives at the boundary (`__main__._confirm`), never in library code
(PY-CORE-006). `_confirm` already returns False without prompting when stdin is not a tty,
which *is* the non-interactive refusal — so dropping `--force` removes a flag without
losing the safety it provided. `acquire(force=True)` remains as a tested low-level
primitive but the CLI no longer reaches it.

## Consequences
- One mental model: *"it's locked → `--unlock` it"*, with "prompt vs refuse" decided by
  whether there is a human at a terminal — not by which of two similarly-named flags you
  picked. This is the conventional CLI shape.
- The `osimage pack`-on-locked-image flow (ADR 0010) is naturally safe: pack surfaces
  lchroot's exit 9 + message and a human runs `--unlock`; nothing automated ever clears a
  live lock (the non-tty refusal enforces it). ADR 0010's "pack must not force a locked
  image" is now structurally true — there is no force.
- **Capability removed:** "stop a live holder *without* a tty, non-interactively" (old
  `--kill`). Judged not worth a flag: stale locks auto-reclaim, and the only remaining
  case — scripted eviction of a *live* session — is rare and deliberately disallowed so
  automation can't nuke a running build. If it ever proves necessary it should return as
  an explicit `--status`-family option, not as a resurrected top-level `--kill`.
- The shipped bash completion (`completions/lchroot.bash`) was corrected in the same
  change: it had drifted (offered the long-removed `--no-sync`, missing `--status`,
  `--path`, `--hostname`, `--no-emulate`); it now lists the real flag set including
  `--unlock`.

## Considered alternatives
- **Keep `--kill`/`--force`, only fix the help text** — rejected: the confusion is in the
  names and the verb split, not the prose; better docs don't fix "force prompts but kill
  doesn't".
- **One `--unlock` plus a global `--yes`/`-y` to skip the prompt** — rejected as redundant
  with the tty check: `--unlock` already proceeds with confirmation on a tty and refuses
  without one. A `--yes` would only re-introduce the "kill a live holder unattended" path
  this ADR deliberately drops.
- **Make the plain entry path prompt-then-take-over on a tty** — rejected: the plain path
  stays non-interactive and fails fast with exit 9 (matches the `lchroot-legacy` "locked →
  error" contract); freeing a lock is an explicit, separate act.
