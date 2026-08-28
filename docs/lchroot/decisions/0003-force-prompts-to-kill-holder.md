# 3. `--force` prompts to kill the holder (supersedes the `--force` semantics of ADR 0002)

## Status
Accepted — 2026-05-30 (supersedes the `--force` decision in ADR 0002; `--status`/`--kill`
from 0002 stand unchanged)

## Context
ADR 0002 kept `--force` as "reclaim the lock *without* stopping the holder". On review
the operator judged that still too dangerous: a reflexive `--force` (existing muscle
memory) silently steals the lock and **leaves the old session running**, which is exactly
the double-`dnf`-on-one-rpmdb incident that motivated 0002 in the first place. The
operator's instruction: *"`--force` should at most ask to do a kill, but not kill by
default without asking — a y/N question."*

This reverses part of the project's earlier no-prompts stance (D13/L18 removed a y/N
confirmation from the *normal* entry path). The reconciliation: L18 is "don't add prompts
the oracle lacked **that the user didn't ask for**". Here the user explicitly asked, and
the prompt guards a *destructive override* path, not the common entry path — so it is
warranted where the earlier one was scope creep.

## Decision
`--force` now means **"take over an in-use image, asking first"**:

- If a *live* holder exists, `--force` prints who holds it (and a warning if a
  package manager is running in the tree) and asks `kill it and take over? [y/N]`,
  **defaulting to No**.
  - **y** → terminate the holder's process tree (via `terminate_holder`, ADR 0002),
    then enter the image.
  - **N / Enter / EOF** → abort with exit 9 ("not taking over; still in use"); the
    holder is left completely untouched.
- **No tty** (cron/CI/pipe): `--force` does **not** prompt and does **not** kill — it
  treats the absence of a human as "No" and aborts (exit 9). Non-interactive callers
  that really want to take over must use the explicit `--kill` (which has no prompt —
  invoking it *is* the confirmation, per 0002).
- The old "silently steal the lock and leave the holder running" behaviour is **removed**.
  There is no longer any way to enter alongside a running holder by accident.

So the three verbs are now cleanly distinct: `--status` (look), `--kill` (stop, no
prompt, scriptable), `--force` (stop-then-enter, with an interactive confirmation).

The low-level `ImageLock.acquire(force=...)` primitive is unchanged and still tested; the
CLI entry path no longer calls it with `force=True` (it kills first, then acquires
normally). The prompt lives at the boundary (`__main__._confirm`), never in library code
(PY-CORE-006).

## Consequences
- `--force` can no longer cause a silent double-session; the worst it does is ask.
- A safety property falls out for free: `--force` is inert and harmless in non-interactive
  contexts (it can't find a human to confirm, so it refuses), which protects cron/CI.
- One more interactive code path to keep tty-aware; covered by `_confirm` unit tests
  (tty/no-tty/EOF/answers) and `run()` flow tests (yes→kill+enter, no→abort+spare).
- Verified live on the controller: non-interactive `--force` on a held image aborted
  (exit 9, holder spared); a pty-driven `--force` answering `y` killed the holder, took
  over, ran the command, and cleared the lock.

## Considered alternatives
- **Keep 0002's silent steal as a fallback when the user answers No** — rejected: that
  preserves the very footgun being removed; "No" must mean "do nothing".
- **Prompt by default even without `--force` (on plain `lchroot <held-image>`)** —
  rejected: the plain path should stay non-interactive and fail fast with exit 9 +
  guidance (`--status`/`--kill`), matching lchroot-legacy's "locked → error" contract. The
  prompt is opt-in via `--force`.
- **A separate `--force --yes` to skip the prompt for automation** — rejected as
  redundant: `--kill` already is the no-prompt path; adding `--yes` would be a second way
  to do the same thing.
