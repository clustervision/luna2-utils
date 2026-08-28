# 2. `--status` / `--kill` for lock administration; `--force` redefined as non-stopping

## Status
Accepted — 2026-05-30

## Context
lchroot serialises access to an image with a host-side lock (`<image>/tmp/lchroot.lock`,
holding `PID <pid> on <tty>`). When the lock is held the operator's only override was
`--force`, which **steals the lockfile but leaves the holder running**. In practice
(observed live, 2026-05-30) an operator who hit "image in use" reached for `--force`
and ended up with **two `dnf` processes updating the same image concurrently** — racing
the rpmdb and download cache. The lock existed precisely to prevent that, and `--force`
quietly defeated it. The operator had no way to *see* who held the lock or to *stop*
them cleanly; their only recourse was hunting PIDs by hand.

This is a deliberate divergence from the behavioural oracle: bash `lchroot-legacy` has no lock
inspection or takeover at all. It is justified because it fixes a footgun lchroot itself
introduced with `--force`, and was explicitly requested by the operator (cf. L18 — add
behaviour beyond the oracle only when asked; this was asked).

## Decision
Add two lock-administration subcommands and redocument `--force`:

- **`--status <image>`** (read-only): print the lock holder (PID, tty, liveness) and the
  in-image process tree, walked from `/proc` with pure stdlib. Zero side effects.
- **`--kill <image>`**: stop the holder's entire process tree (SIGTERM, then SIGKILL any
  survivor after a grace period), then drop the lock. Because the sandbox uses
  `--unshare-pid`, killing the outer `bwrap` collapses the PID namespace and everything
  inside it dies with it. `--dry-run` prints what it would stop and changes nothing
  (PY-CORE-004). It logs a **loud warning first** if a package manager (`dnf`/`rpm`/…)
  is running in the tree, since killing one mid-transaction can corrupt the rpmdb.
- **`--force`** is kept but redocumented honestly as "reclaim the lock *without* stopping
  the holder (advanced; leaves it running)". The locked-error message now points at
  `--status`/`--kill` rather than implying `--force` is the way to take over.

Implementation: `/proc` walking and `os.kill` are stdlib (no new dependency, no
subprocess — consistent with `lock.py` already using `os.kill`). New surface lives in
`lock.py` (`read_holder`, `holder_tree`, `terminate_holder`, `KillReport`) with the CLI
wiring in `__main__.py` (`_lock_admin`). Re-entrant: a dead/absent holder is a no-op
that just clears any stale lock.

## Consequences
- The safe takeover path is now explicit: `--status` to see, `--kill` to take over.
  `--force` remains only for the rare "holder is wedged but I must enter alongside it"
  case, and no longer reads as the default override.
- `--kill` is destructive by design; its guard rails are `--dry-run` + the package-manager
  warning, not an interactive prompt (consistent with L18 — no prompts the oracle lacked;
  invoking `--kill` *is* the confirmation).
- Linux-only (`/proc`, `os.kill`) — acceptable: bwrap is Linux-only already.
- Verified live on the controller: `--status` rendered the `python → bwrap → bwrap →
  sleep` tree; `--kill` stopped all 4 processes and cleared the lock; SIGTERM sufficed
  (namespace collapse), no SIGKILL escalation needed.

## Considered alternatives
- **Make `--force` kill the holder first** — rejected: overloads one flag with a
  destructive meaning, so a reflexive `--force` (the existing muscle memory) would now
  silently kill a running `dnf`. Separating "see / kill / steal" into three explicit
  verbs is safer and clearer.
- **Status only, kill by hand** — rejected: leaves the operator doing `pgrep`/`kill`
  manually, which is exactly the error-prone path that produced the double-`dnf` incident.
- **Shell out to `ps --forest`** — rejected: an external dependency on `ps` output format
  for a durable tool; reading `/proc` directly is stdlib and stable (PY premise 1).
