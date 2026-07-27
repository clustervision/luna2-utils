# 4. HA image sync on master exit is not gated on the session's exit code

## Status
Accepted — 2026-05-31

## Context
On a master controller, a read-write `lchroot` session triggers a best-effort
`/ha/syncimage/<image>` on exit (D7, mirroring bash `lchroot-legacy`), so the peer
controller's copy of the image stays current. The question this ADR settles: should
that sync fire only when the in-sandbox session exited cleanly (`rc == 0`)?

The intuition "only sync successful changes" is wrong here. The sync's job is to keep
the two controllers' images **byte-identical**, not to ship "good" changes. A session
that exits non-zero (a failed `dnf`, a `Ctrl-C`, a command that returned an error) has
very often *still mutated the image on disk* — packages half-installed, files written.
If we skip the sync on a non-zero exit, the master and the peer **silently diverge**:
the master carries the partial change, the peer does not, and the next failover boots
nodes from an image nobody inspected. Divergence is a worse failure than re-syncing a
possibly-partial change, because it is invisible.

## Decision
The master rw-exit sync fires whenever `not --ro and not --no-sync and HA-enabled and
master`, **independent of the child process's return code**. Do not add an `rc == 0`
(or any exit-code) gate.

Operators who explicitly do not want a sync have `--no-sync`; that is the supported
opt-out, and it is a *deliberate* choice rather than an accident of how a command
happened to exit. The sync itself remains best-effort (`sync_image` never raises), so a
sync failure never changes the session's exit code either.

This is pinned by `tests/test_main.py::test_sync_fires_even_when_session_exits_nonzero`
(non-zero `run_interactive` still calls `syncimage`) and
`test_ha_disabled_master_does_not_sync` (no peer → no sync). A one-line comment at the
sync call in `__main__.run()` states the rationale so the gate is not "helpfully" added
back later.

## Consequences
- Controllers cannot silently diverge because a customization session ended non-zero.
- A failed/aborted session may sync a partial image; that is acceptable and visible
  (the operator saw the failure), and is recoverable by re-running, whereas divergence
  is not.
- `--no-sync` is the one true way to suppress the sync; there is no implicit suppression.

## Considered alternatives
- **Gate on `rc == 0`** — rejected: lets the peer fall behind on exactly the messy,
  partially-applied cases where staying identical matters most.
- **Prompt on non-zero exit ("sync anyway? [y/N]")** — rejected: violates the no-prompt
  default-path stance (L18) and breaks non-interactive use; `--no-sync` already covers
  the intent without a prompt.
