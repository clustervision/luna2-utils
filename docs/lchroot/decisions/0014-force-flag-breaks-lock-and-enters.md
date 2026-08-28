# 14. `--force` replaces `--unlock`: break a live lock *and proceed*, never just free it

## Status
Accepted — 2026-06-04 (supersedes the lock-clearing surface of ADR 0012: the `--unlock`
verb is removed and the "free a live lock without entering" capability with it. `--status`
from 0002/0012 stands unchanged. The low-level `ImageLock.acquire(force=...)` *steal*
primitive and `terminate_holder` from 0002 are unchanged; only the CLI surface changes.)

## Context
ADR 0012 (one day earlier) collapsed the old `--kill`/`--force` pair into a single
`--unlock` verb that **freed a live lock and exited without entering**, prompting on a tty
and refusing without one. It explicitly rejected an in-line take-over ("Make the plain
entry path prompt-then-take-over on a tty — rejected").

Reviewing the operator experience again, the operator changed their mind:

- **The common case is "free it *and use it*", not "free it and walk away".** An operator
  at a terminal who discovers a stuck lock almost always wants to then enter the image and
  finish the job. Making them run two commands (`--unlock`, then `lchroot <image>`) splits
  one intent across two invocations.
- **`--force` is the word people already reach for.** It is the conventional name for
  "do it anyway, past the safety check", and it pairs naturally with the locked-image
  error. `--unlock` was a freshly-coined verb nobody had muscle memory for.
- **Freeing without entering had exactly one motivating caller (`osimage pack`), and it
  does not actually need it.** Pack must *not* break a locked image itself (that risks the
  rpmdb); it surfaces lchroot's exit 9 and a human decides. That flow is satisfied by the
  plain entry path failing fast — it never needed a standalone "free" verb.

The operator's instruction: *bring back `--force`; make it a flag on the normal entry path
that breaks a live lock and then does the normal thing (enter on a tty, run the command
otherwise); drop `--unlock` and the free-without-enter capability; and tell the operator
clearly what is being stopped — or why we are refusing.*

## Decision
`--force` is now a **modifier on the normal entry path** (`lchroot [--force] <image>
[command]`), **not** a standalone verb. It changes behaviour **only when a *live* lock is
in the way**; for no lock or a stale (dead-holder) lock it is a no-op — a stale lock is
always auto-reclaimed by `acquire()` (ADR 0002). The "action" after the lock is resolved is
the usual one: a shell on a terminal, the given command otherwise.

The full matrix (the only thing `--force` changes is the **live-lock** row):

| lock state | plain (no `--force`) | `--force` |
|---|---|---|
| **no lock** | enter (tty) / run command (non-tty) | same (no-op) |
| **stale / dead** | reclaim → enter / run command | reclaim → enter / run command |
| **live, tty** | show holder, ask `[y/N]`; **y** → stop holder + enter; **n** → abort (exit 9) | show holder, stop it (no prompt) → enter |
| **live, non-tty** | **refuse** (`LockedError` → exit 9); name the holder + remedy | stop holder (always) → run command |

Two supporting rules:

- **No terminal ⇒ a command is required.** Without a tty there is no interactive shell to
  drop into; a bare entry would launch a shell that reads EOF and exits having done nothing
  — *looking* like success while nothing ran. So a non-interactive entry with no command is
  an explicit error (exit 7) that says why. `--dry-run` is exempt (it runs nothing by
  design and says so).
- **Always describe the holder.** Whether breaking a lock (`--force`), asking about it
  (tty prompt), or refusing it (non-tty), lchroot first prints the holder — PID, tty, the
  in-image process tree — and warns if a package manager is mid-run (stopping `dnf`/`rpm`
  can corrupt the rpmdb). The operator never kills, or is blocked by, something unnamed.

Breaking a live lock goes through `terminate_holder` (SIGTERM the tree → SIGKILL
survivors → drop the lock), **never** the `acquire(force=True)` *steal* primitive: a live
holder is always stopped first, never left running while its lock is yanked. The
prompt/refuse policy lives at the boundary (`__main__._resolve_live_lock`), not in the lock
library (PY-CORE-006).

There is no standalone "free the lock without entering" any more. To clear a lock you
either enter (answering the prompt on a tty) or `--force`; to merely *look*, use
`--status` (read-only, unchanged).

## Consequences
- One mental model: **"locked and in my way → `--force` past it"**, with "ask vs refuse"
  (when you don't pass `--force`) decided by whether a human is at the terminal. `--force`
  is the familiar word and behaves the conventional way.
- **Capability removed:** "free a live lock but do *not* enter" (ADR 0012's `--unlock`).
  Judged unnecessary: its only caller (`osimage pack`) must not break a locked image
  anyway and is served by the plain path's fail-fast exit 9. If a pure "free, don't enter"
  is ever needed it should return as an explicit `--status`-family option, not by widening
  `--force`.
- **`osimage pack` stays structurally safe.** Pack runs lchroot non-interactively *without*
  `--force`, so a live lock yields exit 9 + a named-holder message and a human intervenes;
  nothing automated ever breaks a live lock. ("pack must not `--force`" is now literally
  true — pack simply never passes the flag.)
- **The plain entry path is now interactive on a tty** (it prompts before breaking a live
  lock) — a reversal of 0012's "plain path stays non-interactive, fails fast". The
  fail-fast exit 9 is retained for the *non-interactive* live-lock case, which is the one
  that protects automation.
- **New non-interactive contract:** non-tty + no command → exit 7 (was: a silently-empty
  shell). Surfaces a real foot-gun instead of pretending success.
- The shipped bash completion (`completions/lchroot.bash`) swaps `--unlock` → `--force`.

## Considered alternatives
- **Keep `--unlock` *and* add `--force`** (free-without-enter *and* break-and-enter) —
  rejected: two verbs for one concept ("a live lock is in my way") is the very confusion
  0012 set out to remove; the free-without-enter half has no real caller.
- **Make `--force` break the lock but still exit without entering** (i.e. just rename
  `--unlock`) — rejected: the whole point of the change is that the operator wants to
  *enter/run* after freeing it; exiting first defeats it.
- **Let a non-tty run with no command launch a shell anyway** — rejected: it reads EOF and
  does nothing while returning 0, which reads as "it worked" — the opposite of safe.
- **Keep the non-tty live-lock refusal *even with* `--force`** — rejected: `--force` is
  precisely the operator taking explicit responsibility; honouring it unattended is the
  point (e.g. a deliberate scripted recovery). Plain (no `--force`) non-tty still refuses,
  which is what protects automation that did *not* ask for it.
