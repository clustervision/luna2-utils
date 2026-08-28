# 6. Enter the sandbox with `--die-with-parent` so teardown is real

## Status
Accepted — 2026-05-31

## Context
`lchroot`'s pitch (JOURNAL.md "Cool Facts") promised that **nothing you start inside the
image survives leaving it** — "zero leaked daemons, ever" — on the strength of
`--unshare-pid`. A destructive integration run (RESULTS-2026-05-31-destructive.md)
showed that claim was **false as stated** for bwrap 0.6.3 with our flags, and a clean
bounded re-verification (2026-05-31, JOURNAL.md L21) pinned down exactly why.

By default bwrap installs a **reaper as PID 1** inside the namespace (its `--as-pid-1`
flag *disables* this). That reaper does not exit until *every* process in the namespace
has exited — including a `setsid`/`nohup`/`&`-backgrounded child. Two concrete
failure modes follow, both reproduced live:

1. **`exit` hangs.** Background a process (`setsid sleep 99999 &`) and exit the shell:
   the main command is gone but the reaper keeps waiting on the detached child, so the
   outer `bwrap` never returns. Under captured I/O (the integration harness, or any
   non-interactive caller) the held-open stdout pipe blocks the reader until its
   timeout. The detached process is **not** killed on exit.
2. **A killed launcher leaks a live tree.** When the Python launcher is SIGKILLed
   (e.g. a test timeout), `bwrap` reparents to init (PID 1 on the host) and keeps
   running with its children. In the overnight run this left an orphaned
   `bwrap`+`dnf install` holding the image's **rpmdb lock**
   (`/var/lib/rpm/.rpm.lock`), which then poisoned the next test — the exact
   double-`dnf`-on-one-rpmdb hazard ADR 0002 exists to prevent, here caused by our own
   tooling.

The containment properties (cannot signal the host, cannot escape the namespace, caps
dropped) held throughout — this is purely a **teardown** defect.

## Decision
`build_bwrap_argv` always emits **`--die-with-parent`** (both rw and ro modes).

Per `man bwrap`, it SIGKILLs all sandbox processes — parent to child, including
COMMAND — when bwrap or bwrap's parent dies (`PR_SET_PDEATHSIG`). Verified live on
bwrap 0.6.3, it fixes both failure modes with no downside:

- **Normal exit:** when the main command (the shell) exits, the outer bwrap exits, and
  the flag tears down any lingering reaper + detached children. `exit` returns promptly
  and the detached process is killed — the pitch is now true.
- **Killed launcher:** when the Python parent dies, the whole sandbox is SIGKILLed
  instead of reparenting to init. No orphaned `bwrap`, no leaked rpmdb lock.

The reaper (PID 1) is **kept** — zombie reaping during a long `%post` still works.

### Why not `--as-pid-1`
`--as-pid-1` also fixes both modes (the shell becomes PID 1, so its exit collapses the
namespace), but it *removes the reaper*. That hands PID-1 signal semantics and zombie
reaping to an interactive `bash`, a behavioural change with no upside here. We get the
same teardown guarantee from `--die-with-parent` while keeping the reaper — strictly
less risky.

## Consequences
- "Nothing you start inside survives leaving the image" is now an accurate, verified
  claim rather than an aspiration.
- **Documented operational note (not a regression):** a process you want to outlive the
  session cannot be launched this way — that is the intended, advertised behaviour
  (`lchroot` is for image *customisation*, not for spawning host daemons; this is the
  footgun chroot/lchroot-legacy leaves open and we deliberately close).
- The destructive harness can no longer be poisoned by a leaked `bwrap` from a prior
  test (it complements, not replaces, the harness teardown fix — see test_integration).
- This is a sandbox-flag change, hence an ADR. It is a *strengthening* (it adds teardown
  and removes nothing), so it does not relax the "do not weaken the sandbox" rule; no
  capability, namespace, or mount behaviour changed.

## Enforcement
- `tests/test_sandbox.py::test_die_with_parent_present_in_both_modes` (golden, both
  modes) — removing the flag fails the suite.
- `tests/test_sandbox.py::test_rw_argv_has_all_hardening` also asserts it.
- `tests/test_integration.py::test_destructive_detached_process_dies_on_exit`
  exercises it live (bounded, with guaranteed teardown).

## Considered alternatives
- **`--as-pid-1`** — rejected: removes the zombie reaper, changes PID-1 signal semantics
  for the interactive shell. Same teardown win, more risk.
- **Leave the flags as-is and only correct the docs** — rejected: the `exit`-hangs
  behaviour is a real usability defect and the leaked-tree behaviour is a real
  operational hazard (it leaked an rpmdb lock). Documenting a footgun we can cheaply
  close is worse than closing it.
- **Fix it only in the test harness** — rejected: the harness still needs its own
  teardown (a process can die for reasons unrelated to the parent), but the product
  itself shipping a non-teardown sandbox was the root cause.
