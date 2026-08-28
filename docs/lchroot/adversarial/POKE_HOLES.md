# POKE_HOLES.md — adversarial containment harness for lchroot

> An **LLM-executed test script.** Unlike the pytest suite (deterministic logic:
> argv, exit codes, locking), this harness exists to attack a *live* sandbox the way
> a hostile or buggy package would, and prove the sandbox contains it. It targets the
> exact failure modes that made us abandon `chroot`/`lchroot` for bubblewrap:
> package `%post` scriptlets that `kill`/`systemctl`/`mount` their way onto the
> **controller**.
>
> How to use it: an agent reads this file, runs each scenario's **Attack** against a
> live image, runs the **Host-safety check** on the host, and records PASS/FAIL with
> evidence into a dated `RESULTS-<date>.md` next to this file. New holes found become
> observations in `../../src/agent/MEMORY.md` and, on a second sighting (or first, if
> security), rules via `../../src/agent/RULE_EVOLUTION.md`. The TMPDIR fix
> (JOURNAL.md L13) was found exactly this way.

## Threat model (what lchroot got wrong)
1. **PID-signal vector** — `chroot` shares the host PID namespace, so a scriptlet's
   `kill`/`pkill` can signal controller processes. lchroot: `--unshare-pid`.
2. **systemd/service vector** — a scriptlet `systemctl stop/restart`s controller
   services. lchroot: no host systemd/D-Bus socket + private PID ns → systemctl can't
   reach the host bus.
3. **mount vector** — root remounts/escapes via `mount`. lchroot: `--cap-drop`
   removes CAP_SYS_ADMIN so mount/remount is denied even to root.
4. **read-only-escape vector** — root rewrites a path that should be read-only.
   lchroot: `--ro-bind` + dropped caps.
5. **host-fs reach** — escape the root via `/proc/1/root`, `..`, etc. lchroot:
   `pivot_root` (not chroot) + PID ns.
6. **leftover state** — host mounts/locks survive a crash. lchroot: namespace
   auto-teardown; host-side lock released on exit.

## Executor instructions (READ BEFORE RUNNING)
- **Image:** use a designated *play* image (`compute-image-bubble`), never a
  production one. Invoke with `--no-sync` so nothing propagates to peer controllers.
- **Run the tool** (system interpreter, correct requests/urllib3):
  `PYTHONPATH=<repo>/src /trinity/local/python/bin/python3 -m lchroot [flags] <image> /bin/sh -c '<attack>'`
- **SAFETY RULES (non-negotiable):**
  - **Never** issue a destructive command against a *real* host service
    (`systemctl stop sshd`, `kill <real-daemon-pid>`). If containment ever regressed,
    that command would actually fire. Prove *unreachability* instead, or attack a
    throwaway **sentinel** you started yourself.
  - Capture a host **baseline** before and re-check after every scenario: chosen
    witness services (`nginx`, `mariadb`) stay `active`, host `pid1` stays `systemd`,
    image mount count in `/proc/mounts` stays `0`.
  - `dnf` scenarios mutate the play image — that's expected and allowed; note what was
    installed/upgraded.
- **Verdict rule:** a scenario PASSES only if (a) the attack was contained AND (b) the
  host-safety check shows the controller unchanged. Record the literal evidence.

---

## Scenarios

### S1 — remount escape (mount vector)
- **Attack:** `mount -o remount,rw /`
- **Expected:** `mount: /: permission denied` (CAP_SYS_ADMIN dropped). In `--ro` mode
  a subsequent `touch /etc/x` also fails (read-only fs).
- **Host-safety:** n/a (attack can't fire); image mount count unchanged.

### S2 — PID isolation (signal vector, part 1)
- **Attack:** `ls /proc | grep -cE '^[0-9]+$'` and `cat /proc/1/comm`
- **Expected:** only a handful of PIDs (sandbox-local), `/proc/1/comm` is `bwrap`,
  **not** the hundreds of host PIDs nor host `systemd`.

### S3 — kill a host process (signal vector, part 2)
- **Setup (host):** start a throwaway sentinel: `sleep 600 & echo $!` → `$SENT`.
- **Attack (sandbox):** `ls -d /proc/$SENT` ; `kill -9 $SENT` ; `pkill -9 sleep`
- **Expected:** sentinel not visible; `kill` → "No such process"; `pkill` → rc 1.
- **Host-safety:** `kill -0 $SENT` → sentinel **survived**. Then clean it up.

### S4 — systemctl reaches controller systemd (service vector)
- **Attack:** `systemctl status nginx` ; `systemctl start chronyd`
- **Expected:** "System has not been booted with systemd as init system (PID 1).
  Can't operate." (no host bus reachable).
- **Host-safety:** witness services still `active`; never issue `stop`/`restart`.

### S5 — package install worst-case (the headline scenario)
- **Attack:** `export TMPDIR=/tmp; dnf -y install <pkg-with-a-systemd-unit>` (e.g.
  `chrony`, `httpd`, `cronie`). Then `systemctl start <unit>`.
- **Expected:** transaction **completes**; the `%systemd_post` scriptlet enables the
  unit *in the image* (`systemctl is-enabled` → enabled) but a real `start` is
  contained ("not been booted…").
- **Host-safety:** witness services unchanged; the host's copy of `<pkg>` (if any)
  unchanged; image mount count `0`.

### S6 — escape the root to the host filesystem
- **Attack:** `cat /proc/1/root/etc/hostname` ; `ls /proc/1/root/trinity/local/luna 2>&1` ;
  `head -1 /etc/os-release` (compare to host).
- **Expected:** `/proc/1/root` is the *sandbox* root (the image), not host `/`; no
  controller-only paths (e.g. luna config) are reachable.

### S7 — read secrets / host config
- **Attack:** `cat /trinity/local/luna/utils/config/luna.ini 2>&1` ;
  `ls /root/.ssh 2>&1`
- **Expected:** the image's own (absent/different) paths — **not** the controller's
  `luna.ini` or root keys. (lchroot binds the *image* as `/`, so host config is simply
  not present.)

### S8 — environment leak (found 2026-05-29 → fixed)
- **Attack:** `echo "TMPDIR=$TMPDIR"` with a hostile host `TMPDIR` set
  (e.g. `/tmp/does-not-exist-in-image`); then `dnf -y install tree`.
- **Expected:** `TMPDIR=/tmp` inside (lchroot pins it); dnf's librepo temp files work.
- **History:** before the fix, host `TMPDIR` leaked in and broke dnf with
  `mkstemp ... No such file or directory`. This scenario is the regression guard.

### S9 — leftover state after a hard kill (cleanup vector)
- **Attack:** enter a long-running sandbox, then `kill -9` the host `bwrap` PID
  mid-session (simulate a crash).
- **Expected:** no image mounts remain in host `/proc/mounts`; the host-side lock
  (`<image>/tmp/lchroot.lock`) is gone or auto-reclaimed (dead holder) on the next run.

### S10 — resource exhaustion — OUT OF SCOPE (KISS/YAGNI, PY-CORE-009)
- lchroot deliberately imposes **no** cgroup/resource limits. `dnf`/apt under a plain
  chroot already behave ~99% of the time; a fork-bomb sandbox would add far more error
  surface than it removes. **Do not test or build this.** If a real incident ever
  occurs (a 2nd strike), the documented mitigation is to wrap the invocation in
  `systemd-run --scope -p MemoryMax= -p TasksMax=` externally — no change to lchroot.

---

## Recording results
Create `RESULTS-<YYYY-MM-DD>.md` with one block per scenario: the exact commands run,
the captured output, the host baseline/after, and `VERDICT: PASS | FAIL | GAP`. File
any new hole into `../../src/agent/MEMORY.md`.
