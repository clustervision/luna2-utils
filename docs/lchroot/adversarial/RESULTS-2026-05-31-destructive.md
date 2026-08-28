# Destructive integration run — 2026-05-31 (overnight, autonomous)

- **Host:** the controller, Rocky 9.7, bwrap 0.6.3. `/` started **87% full / 6.2 G free**,
  ended **93% / 3.4 G free** (the DOCA download/partial-install ate ~3 G).
- **Image:** `compute-image` (NOT `compute` — luna has compute-image/login/opensuse-image/
  ubuntu-image; `compute` 404s).
- **Command:** `LBWRAP_ITEST=1 LBWRAP_DESTRUCTIVE=1 LBWRAP_IMAGE=compute-image
  ./.venv/bin/pytest -m integration -k destructive --no-cov -v`
- **Result:** `3 failed, 2 passed, 103 deselected in 1863.71s (0:31:03)`.

## Per-test outcome

| Test | Verdict | Cause |
|------|---------|-------|
| `kernel_module_load_is_denied` | **PASS** | modprobe denied inside sandbox, as designed. |
| `docker_cannot_run_a_container_inside_sandbox` | **PASS** | the containment win — no container ran inside. |
| `doca_ofed_installs` | **FAIL — environmental** | `TimeoutExpired after 1800s`, launcher SIGKILLed (rc -9). doca-ofed is a multi-GB driver stack; on a 93%-full disk over the network it did not finish in 30 min. dnf behaves as under chroot; we impose no disk limit (L15/L19). **Not an lchroot fault.** |
| `docker_userspace_installs` | **FAIL — cascade from the DOCA timeout** | `RPM: error: can't create transaction lock on /var/lib/rpm/.rpm.lock (Resource temporarily unavailable)`. The DOCA test's launcher was SIGKILLed at 1800s but **its inner `bwrap`+`dnf install doca-ofed` kept running and held the image's rpmdb lock**, so the next test's dnf could not get the lock. This is the exact double-dnf-on-one-rpmdb hazard D15/ADR-0002 is about — here **caused by our own test harness**, not by lchroot. |
| `detached_process_dies_on_exit` | **FAIL — test-design bug AND a real behavioural finding** | `TimeoutExpired after 60s`; **leaked a `bwrap`+`sleep 54321` tree onto the host for 31 min** (cleaned up — see below). See the finding. |

## ★ The important finding: my earlier "it gets killed on exit" claim is NOT confirmed — likely wrong

The test ran `setsid sleep 54321 >/dev/null 2>&1 </dev/null & echo started; sleep 1`
inside the sandbox and expected the `lchroot` call to return, then the detached `sleep`
to be gone. Instead the `lchroot`/`bwrap` invocation **never returned** (hit the 60s
timeout), and afterwards this tree was alive on the host:

```
PID 3979220 ppid 1   bwrap --bind /trinity/images/compute-image / ... --unshare-pid ... -- /bin/sh -c setsid sleep 54321 ... & echo started; sleep 1
PID 3979222 ppid 3979220   sleep 54321
```

i.e. the inner `/bin/sh` exited after its `sleep 1`, but **`bwrap` kept running, waiting
on the back­grounded `setsid sleep`** — it did NOT collapse the PID namespace and SIGKILL
the sleep. The process never escaped to the host as an independent daemon (it stayed
parented under `bwrap`, inside the namespace), but it also **did not die when the shell
exited.**

**This contradicts the confident answer I gave earlier** ("a process you start inside —
even with setsid/nohup/daemonize — is unconditionally SIGKILLed the instant the session
exits"). The observed bwrap 0.6.3 behaviour is instead: **bwrap waits for the
backgrounded process, so the session does not cleanly "exit" while it runs.** The
containment property (can't signal the host, stays in the namespace) still held — but the
*teardown* story is wrong as stated, and the "Cool Facts" bullet + my two chat answers
must be corrected.

**DO NOT trust the earlier teardown claim until re-verified.** Needed: a clean, bounded
experiment — record the in-ns process via a host-visible side channel, exit the session
normally (foreground, no pytest timeout), and observe whether bwrap returns and whether
the process is reaped. Hypotheses to distinguish:
1. bwrap 0.6.3 runs a reaper/init as ns-pid-1 and waits for *all* ns processes (so a
   backgrounded child keeps the sandbox alive — session never ends → nothing to "kill on
   exit"); vs
2. kernel pid-1-exit semantics do apply but only once bwrap's main child chain ends, and
   `setsid` moved the sleep out of that chain.
Either way: **a detached process can keep an lchroot session alive indefinitely**, which is
itself a useful operational fact (and a footgun for scripted/non-interactive use).

## Harness bugs to fix before re-running (next session)

1. **No-hang guard (critical).** Every in-image command in an integration test must be
   bounded so a backgrounded/`setsid` process can't stall the runner. Wrap the inner
   script in `timeout 5` AND don't rely on `&` keeping bwrap alive.
2. **Guaranteed teardown.** A test that times out must not leak `bwrap`. The harness needs
   a fixture/`finally` that `pkill -f 'bwrap --bind <image>'` (or kills the launcher's
   whole process group) on every exit path. Today a `subprocess.run(timeout=)` SIGKILLs
   only the python launcher; **bwrap reparents to init and keeps running** (this is the
   same reason the rpmdb lock leaked into the docker test).
3. **Serialise / isolate rpmdb access.** Because a leaked dnf holds the image rpmdb lock,
   one timed-out install test poisons the next. Fix #2 prevents this; additionally the
   destructive tests should probably each run against a fresh image clone.

## Cleanup done (verified)

Leaked tree SIGKILLed: `kill -9 3979222 3979220`. Verified after: both PIDs gone
(`kill -0` → no), `grep -c compute-image /proc/mounts` = **0**, no `lchroot.lock`. The
`containerd`(1190)/`dockerd`(1487) on the host are the **host's own** socket-activated
daemons (low PIDs, pre-existing), not from these tests.

## Image left dirty (reset before any clean re-run)

`compute-image` was mutated and is **not pristine**: `/etc/resolv.conf` seeded
(`nameserver 8.8.8.8`); `doca.repo` installed under `/etc/yum.repos.d/`; doca-ofed
partially installed (interrupted); docker repo/binary did **not** land (the rpm-lock
failure). Reset/reclone the image before retrying.

## What this run still proves
- The two assertions that matter for the security pitch **passed**: no kernel-module load
  inside; no container runs inside (CAP_SYS_ADMIN dropped + no systemd pid 1).
- The install failures are **environmental (disk) + harness bugs**, not lchroot defects —
  consistent with L15/L19 (we deliberately impose no disk/resource limits).
- A genuinely new behavioural fact surfaced about bwrap teardown that **corrects an
  earlier overstatement** and needs a clean re-test.

## UPDATE 2026-05-31 (same day) — finding resolved + harness fixed

Re-verified cleanly on bwrap 0.6.3 and fixed:

- **Root cause confirmed (hypothesis 1).** bwrap installs a **reaper as PID 1** by
  default (`--as-pid-1` disables it); the shell is its child, not PID 1. The reaper waits
  for all namespace processes, so `--unshare-pid` *alone* does not tear down a detached
  child on exit — the session hangs and (if the launcher is killed) leaks. Flag matrix:
  both `--die-with-parent` and `--as-pid-1` fix it.
- **Product fix:** added `--die-with-parent` to `build_bwrap_argv` (both modes) — D18 /
  ADR 0006. Chosen over `--as-pid-1` (keeps the zombie reaper, no PID-1 signal change).
  `test_destructive_detached_process_dies_on_exit`, rewritten to assert the corrected
  behaviour, now **PASSES live** (~3s, no hang, detached `sleep` gone, no leftover
  mount/lock). The 7 non-destructive integration tests still pass → containment unchanged.
- **Harness fix (D19):** `_run_in_image` now has an inner coreutils-`timeout` guard + an
  outer process-group SIGKILL on launcher timeout, and an autouse `_guarantee_teardown`
  fixture `pkill`s any leftover `bwrap` + clears a stale lock after every test. The
  rpmdb-lock cascade that broke `docker_userspace_installs` can no longer happen.
- **Still TODO (environmental, not a defect):** re-run the disk/network-heavy
  `doca_ofed_installs` + `docker_userspace_installs` once `/` has free space and DNS is
  reachable. Their original failures were the 93%-full disk + the (now-fixed) harness
  cascade, not lchroot.
