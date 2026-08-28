# lchroot — regression test plan

Tracks the cases that must pass before `lchroot` can replace (or sit beside)
`lchroot`. The bash `lchroot-legacy` is the **oracle**: where behaviour overlaps, lchroot
must match it; where lchroot deliberately diverges (e.g. no host mounts left
behind), the divergence is listed and tested explicitly.

Test image: **`compute-image-bubble`** → `/trinity/images/compute-image-bubble`.
Run unit/golden everywhere; run integration only on a controller with the gate set.

Legend: ☐ todo · ◐ partial · ☑ passing · ✗ failing

## Verification status — 2026-06-04 (lock/hang/order pass)
- **Unit suite now 177 tests, 97.2% cov** (`pytest -m "not integration"`). This pass added,
  on top of the ADR 0014 `--force` rework:
  - **Locking edge cases (§C8–C10):** malformed/partial/empty/binary lock files are
    reclaimed not fatal (`test_parse_holder_tolerates_malformed_contents`,
    `test_acquire_reclaims_a_malformed_lock_file`); custom osimage name in the locked
    message (`test_custom_name_appears_in_locked_error`); `/proc`-walk resilience to a pid
    vanishing mid-scan / an absent root (`test_proc_helpers_return_empty_when_pid_is_gone`,
    `test_holder_tree_for_absent_root_is_a_safe_singleton`, `test_proc_cmdline_joins_nul…`);
    signal order children-before-parents (`test_terminate_holder_signals_children_before_parents`).
  - **Hang / NFS / slow-storage resilience (§J):** the wedged-holder (D-state) bound —
    `terminate_holder` polls only to its grace deadline, escalates to SIGKILL, then clears
    the lock and reports the survivor instead of spinning forever
    (`test_terminate_holder_is_bounded_when_holder_will_not_die`, fake-clock); the
    die-before-signal race (`test_terminate_holder_race_holder_dies_before_signal`);
    release-when-lock-already-gone (`test_release_is_a_noop_when_lock_already_gone`).
  - **Ordering (§K):** operation order emulate→acquire→enter, and that a plan / HA-refusal /
    no-command-refusal mutate nothing first (`test_entry_emulates_then_locks_then_enters`,
    `test_dry_run_sets_up_no_emulation_and_no_lock`, `test_ha_refusal_precedes_emulation_and_lock`,
    `test_non_tty_no_command_refused_before_emulation`, `test_live_lock_is_broken_before_acquire`);
    expanded option-hoisting permutations (`test_hoist_handles_multiple_flags_both_sides_and_equals`).
- Gates green: ruff / ruff format / mypy --strict / pytest. Integration (§E/§I) unchanged —
  still gated on `LCHROOT_ITEST=1` on a controller.

## Verification status — 2026-05-29 (Phase 1)
- **Automated (pytest, 50 tests, 95.6% branch):** argv builder B1–B7b (`test_sandbox`),
  image resolution A2–A6 + locking C1–C6 logic (`test_luna`/`test_lock`), config,
  executor, transport decode, and main() exit-code mapping A1/locked→9/config→7
  (`test_entry`). Run: `./.venv/bin/pytest`.
- **Automated integration (`tests/test_integration.py`, `-m integration`, 7 passed
  live on master 2026-05-29):** E2 PID isolation, E4 remount denied, E6 ro write
  denied, systemctl-can't-reach-host, host-pid invisible+unkillable, TMPDIR no-leak,
  D1/D2 no leftover mounts. Gated on `LCHROOT_ITEST=1` + controller identity.
  Run: `LCHROOT_ITEST=1 ./.venv/bin/pytest -m integration --no-cov`.
- **Adversarial harness (`docs/lchroot/adversarial/`):** LLM-executed `POKE_HOLES.md` (the
  hostile-scenario "framework"); `RESULTS-2026-05-29.md` records a full run incl. a
  **real `dnf install` with systemd scriptlets fully contained** (S5), the TMPDIR
  leak found-and-fixed (S8), and S1–S7/D1/D2 PASS. S10 (resource limits) = known GAP.
- **Verified live manually too:** G1 dry-run; E1 fakeuname kernel; rw write persists +
  CAP_CHOWN kept; C1 lock created+released.
- **Still ☐:** F-series HA paths beyond unit level, H-series side-by-side oracle diff,
  mutation testing on `sandbox`/`executor`, CI wiring.
- **Out of scope (KISS/YAGNI, PY-CORE-009):** S10 resource/cgroup limits — not a gap,
  a deliberate non-goal.

---

## A. Argument & resolution contract (unit + golden)

| ID | Case | Expected | Status |
|----|------|----------|--------|
| A1 | no osimage arg | help to stdout, exit **7** (matches lchroot) | ☑ |
| A2 | unknown osimage | error, exit **7** (path resolves null) | ☑ |
| A3 | valid osimage | resolves path + kernelversion from luna API | ☐ |
| A4 | osimage with no kernelversion | non-fatal warning; proceeds without FAKE_KERN | ☐ |
| A5 | image path is `/` or empty | refuse loudly (safety) — *lchroot is stricter than lchroot-legacy* | ☑ |
| A6 | two images same path | refuse (ambiguous destructive target) — *new* | ☑ |

## B. Sandbox argv builder (golden + property — highest value)

Also covered by **Hypothesis property tests** (`tests/test_sandbox.py::test_prop_*`,
PY-TEST-006/D22): for arbitrary name/path/kernel/mode/command they assert the hardening
prefix is always present (B1), the bind+cap-drop match the mode (B2/B7/B7b), FAKE_KERN
iff kernelversion (B3/B5), and the command is the verbatim suffix after `--` (B6). Proven
to fail on a dropped flag via a mutation check.

| ID | Case | Expected argv contains | Status |
|----|------|------------------------|--------|
| B1 | default (rw) | `--bind <path> /`, `--dev /dev`, `--proc /proc`, `--ro-bind /sys /sys`, `--unshare-pid`, `--unshare-uts`, `--die-with-parent`, `--hostname compute-image-bubble`, `--tmpfs /run` | ☑ (`test_die_with_parent_present_in_both_modes`) |
| B2 | `--ro` | `--ro-bind <path> /` (not `--bind`) | ☐ |
| B3 | fakeuname | `--setenv LD_PRELOAD libluna-fakeuname.so`, `--setenv FAKE_KERN <ver>` | ☐ |
| B4 | PS1 | `--setenv PS1` begins `lchroot(rw)` / `lchroot(ro)` | ☑ |
| B5 | no kernelversion (A4) | no `FAKE_KERN` setenv emitted | ☐ |
| B6 | passthrough cmd | `lchroot <img> rpm -qa` → argv ends with `rpm -qa`; no cmd → image default shell | ☐ |
| B7 | caps (rw) | argv contains `--cap-drop CAP_SYS_ADMIN` (NOT relying on defaults — verified bwrap keeps all caps as root) | ☐ |
| B7b | caps (ro) | `--ro` argv contains `--cap-drop ALL` | ☐ |
| B8 | golden file diff | full argv matches committed `tests/testdata/argv_*.txt` | ☐ |

## C. Locking & re-entrancy

| ID | Case | Expected | Status |
|----|------|----------|--------|
| C1 | clean → enter | lock created host-side (`<path>/tmp/lchroot.lock` w/ PID+tty), removed on exit | ☑ unit (`test_acquire_then_release_clean`, `test_ro_entry_acquires_lock_and_releases`) |
| C2 | live lock, no `--force` | tty: prompt `[y/N]` (n → exit 9, holder spared); non-tty: refuse (exit **9**), names holder + remedy (ADR 0014) | ☑ unit (`test_live_lock_tty_prompt_no_aborts_and_spares_holder`, `test_live_lock_non_tty_without_force_is_refused`) |
| C3 | stale lock (dead PID) | reclaimed automatically on entry w/ loud log (no flag), then enters/runs | ☑ unit (`test_stale_lock_auto_reclaimed_without_force`, `test_stale_lock_is_reclaimed_then_enters`) |
| C4 | `--force` a live lock | breaks it (stop holder tree) then proceeds: tty → enter (no prompt); non-tty → run command; pkg-mgr running → rpmdb warning first (ADR 0014) | ☑ unit (`test_force_live_lock_tty_breaks_without_prompt`, `…_non_tty_breaks_and_runs_command`, `test_force_warns_about_running_package_manager`) |
| C4b | non-tty entry, no command | exit **7** (a bare non-tty shell reads EOF, does nothing); `--dry-run` exempt (ADR 0014) | ☑ unit (`test_non_tty_without_command_is_rejected`) |
| C5 | second run after clean exit | succeeds (no leftover lock) — re-entrancy: run 1's lock is released so run 2 is not falsely refused | ☑ unit (`test_two_runs_back_to_back_succeed`) |
| C6 | SIGINT / exception mid-session | lock removed on the way out (context-manager `__exit__`), child gone | ☑ unit (`test_sandbox_launch_failure_raises_sandbox_error` proves release-on-unwind; `test_entry.py` SIGINT→130) |
| C7 | `/proc/<pid>/stat` ppid parse | `_ppid_from_stat` recovers ppid for any `comm` (parens/spaces) | ☑ property (`test_prop_ppid_parsed_across_hostile_comm`) |
| C8 | malformed / partial / empty / binary lock file | treated as no-live-holder → reclaimed, never a hard error (a half-written lock can't wedge the next run); `name=` osimage quoted in the locked message | ☑ unit (`test_parse_holder_tolerates_malformed_contents`, `test_acquire_reclaims_a_malformed_lock_file`, `test_custom_name_appears_in_locked_error`) |
| C9 | `/proc` walk under churn | a pid that vanishes mid-scan (OSError) degrades to ppid=None/cmdline="" not a crash; an absent root → safe singleton; NUL-argv joined | ☑ unit (`test_proc_helpers_return_empty_when_pid_is_gone`, `test_holder_tree_for_absent_root_is_a_safe_singleton`, `test_proc_cmdline_joins_nul_separated_argv`) |
| C10 | teardown signal order | `terminate_holder` SIGTERMs the tree **deepest-child-first, root last** (so killing outer `bwrap` doesn't orphan children mid-collapse) | ☑ unit (`test_terminate_holder_signals_children_before_parents`) |

## D. Cleanup / no-leftover (the key divergence from lchroot)

| ID | Case | Expected | Status |
|----|------|----------|--------|
| D1 | normal exit | `/proc/mounts` on host unchanged vs before | ☐ |
| D2 | **SIGKILL** mid-session | host mount table unchanged (namespace auto-teardown) — *lchroot would leak* | ☐ |
| D3 | 100 sequential runs | no accumulation of binds/mounts on host | ☐ |
| D4 | rw run that wrote a file | file persists in image; nothing else persists | ☐ |

## E. Behaviour inside the sandbox (integration on compute-image-bubble)

| ID | Case | Expected | Status |
|----|------|----------|--------|
| E1 | `uname -r` inside | reports image kernelversion (fakeuname works), not host | ☐ |
| E2 | `/proc` inside | mounted, shows only sandbox PIDs (`--unshare-pid`) | ☐ |
| E3 | `kill`/`systemctl` host PID | cannot reach host (no host PID visible, no systemd socket) | ☐ |
| E4 | `mount -o remount,rw /` inside | fails EPERM (CAP_SYS_ADMIN dropped) even as root | ☐ |
| E5 | `dnf install <pkg>` | files land in image; scriptlet systemctl no-ops/soft-fails; controller services untouched | ☐ |
| E6 | `--ro` then attempt write | write to `/etc` fails (read-only bind) | ☐ |

## F. HA behaviour

| ID | Case | Expected | Status |
|----|------|----------|--------|
| F1 | HA enabled, non-master, **rw** | **refused**: `SecondaryControllerError` → exit 10, no lock taken, no override (ADR 0011) | ☑ |
| F2 | HA enabled, non-master, **--ro** | allowed (read-only cannot mutate) | ☑ |
| F3 | HA enabled, master, rw | enters normally; **no** image sync on exit (sync removed, ADR 0011) | ☑ |
| F4 | HA disabled (standalone) | enters normally even if not master; gate does not apply | ☑ |

## G. Dry-run & safety

| ID | Case | Expected | Status |
|----|------|----------|--------|
| G1 | `--dry-run` | prints full bwrap argv + plan; execs nothing; image untouched; no lock left | ☐ |
| G2 | rw entry without `--yes` | confirm prompt (or refuse non-interactively) — destructive visible | ☐ |
| G3 | secrets | no `PASSWORD`/`SECRET_KEY` in any log/debug/dry-run output | ☐ |

## H. Side-by-side oracle comparison

| ID | Case | Expected | Status |
|----|------|----------|--------|
| H1 | `lchroot-legacy <img> cmd` vs `lchroot <img> cmd` | same stdout/exit for representative read-only cmds | ☐ |
| H2 | env inside both | `FAKE_KERN`, `LD_PRELOAD` identical | ☐ |
| H3 | exit codes | 7/9 match across both | ☐ |

## I. Destructive install & teardown (integration; `tests/test_integration.py`, `@destructive`)

Real package installs (DOCA InfiniBand drivers, docker), the containment proof that
docker still cannot run *inside* the sandbox, and the process-teardown-on-exit check.
These run against a **disposable clone of `login`** that the `regression_image` fixture
creates once and removes after (D20) — no production image is touched. Double-gated:
`LCHROOT_ITEST=1 LCHROOT_DESTRUCTIVE=1` (they mutate the clone + pull large packages). Run:
`LCHROOT_ITEST=1 LCHROOT_DESTRUCTIVE=1 ./.venv/bin/pytest -m integration -k destructive --no-cov -v`
(no `LCHROOT_ITEST_BASE` — the fixture auto-clones). Status column: overnight run 2026-05-31
(`docs/lchroot/adversarial/RESULTS-2026-05-31-destructive.md`) + this session's clone-based re-validation.

| ID | Test fn | Expected | Status (2026-05-31) |
|----|---------|----------|--------|
| I1 | `test_destructive_doca_ofed_installs[el9]` | doca-host bootstrap rpm → `dnf install doca-ofed` completes; `rpm -q doca-ofed` ok | ☑ **PASS (2026-05-31, el9)** — installs cleanly under lchroot in 41 min with the 60-min `LCHROOT_DOCA_TIMEOUT`; the overnight failure was purely our own 30-min budget cutting the dkms/initramfs scriptlets, not a defect |
| I2 | `test_destructive_kernel_module_load_is_denied` | `modprobe mlx5_core` inside fails (no CAP_SYS_MODULE; host kernel ≠ FAKE_KERN) — install ≠ live-load | ☑ |
| I3 | `test_destructive_docker_userspace_installs` | docker-ce repo + `dnf install docker-ce docker-ce-cli containerd.io` completes | ⧖ **not yet re-run** — overnight ✗ was a cascade from I1's leaked dnf (rpmdb lock); harness cascade fixed (D19), needs a disk/DNS-OK re-run |
| I4 | `test_destructive_docker_cannot_run_a_container_inside_sandbox` | even with docker installed, `dockerd`/`docker run` fails inside (CAP_SYS_ADMIN dropped, no systemd pid 1) — **NO container runs** | ☑ |
| I5 | `test_destructive_detached_process_dies_on_exit` | a `setsid` process started inside is gone after the session exits; launcher returns promptly (no hang); no leftover host mount/lock | ☑ **FIXED + verified live (2026-05-31)** — root cause was bwrap's default pid-1 reaper waiting on the child; added `--die-with-parent` (D18/ADR 0006). Test rewritten (bounded, no-hang assert) + harness made un-leakable (D19). Passes in ~3s. |

## J. Hang / slow-storage / NFS resilience

The tool must never wedge the controller on a stuck process or stuck storage. Images can
live on NFS (`/trinity/images` is shared in HA), so a holder can be in uninterruptible
sleep (D-state) and a lock read/write can block. The guarantees below bound what we *can*
bound and document the one inherent limitation.

| ID | Case | Expected | Status |
|----|------|----------|--------|
| J1 | holder won't die (D-state / wedged NFS I/O) under `--force`/break | `terminate_holder` polls only to its `grace_s` deadline, escalates SIGTERM→SIGKILL, then **gives up cleanly**: clears the lock and returns a `KillReport(escalated=True)` with the survivor in `signalled` — it does **not** spin forever | ☑ unit, fake-clock (`test_terminate_holder_is_bounded_when_holder_will_not_die`) |
| J2 | holder dies between read and signal (race) | SIGTERM hits a gone pid (ProcessLookupError swallowed), no SIGKILL, lock still cleared | ☑ unit (`test_terminate_holder_race_holder_dies_before_signal`) |
| J3 | detached / `setsid` child started inside the sandbox | session exit does **not** hang and the launcher returns promptly; nothing survives leaving the image; no leftover host mount/lock — guaranteed by `--die-with-parent` (ADR 0006) | ☑ argv unit B1 + integration I5 (`test_destructive_detached_process_dies_on_exit`) |
| J4 | integration harness vs a hung in-image process | inner coreutils `timeout --signal=KILL` (only the foreground, so a detached child still exercises J3) + outer process-group SIGKILL + autouse `pkill` → one wedged test can't hang or poison the next (rpmdb cascade) — D19 | ☑ integration framework |
| J5 | `/status`/`--force` while the holder is in D-state | `/proc` scan still renders the tree (readers swallow OSError on a stuck pid); a pid vanishing mid-walk is tolerated (→ C9) | ☑ unit (C9 helpers) |
| J6 | host-only `TMPDIR` would wedge `dnf`/librepo inside | `--setenv TMPDIR /tmp` pins a valid in-image temp dir so package tools don't fail/stall on `mkstemp … No such file or directory` (skill.md L13) | ☑ argv unit (B-series) + integration (`test_tmpdir_does_not_leak_from_host`) |
| J7 | non-interactive helper command runs long | `Executor.run(timeout_s=…)` raises `ExecError` on `subprocess.TimeoutExpired` (bounded). The **interactive entry** path deliberately has NO timeout (a shell must not time out) — its hang protection is `--die-with-parent`, not a clock | ☑ design; `run` timeout seam unit-coverable (`test_executor`) |
| J8 | image on a **dead** NFS mount (server down) | **KNOWN LIMITATION:** a lock read/write or the bwrap bind can block in D-state; there is no per-syscall filesystem timeout (would need threads/`alarm`, rejected as over-engineering, PY-CORE-009). Mitigations: HA master-only rw gate (single writer, ADR 0011), bounded teardown (J1), namespace auto-cleanup on death. **Manual/integration check only** — confirm lchroot doesn't *busy-spin* (it blocks in the kernel, killable once I/O returns) | ☐ manual |

## K. Ordering (CLI options + operations + signals)

| ID | Case | Expected | Status |
|----|------|----------|--------|
| K1 | option position (hoist, ADR 0013) | lchroot flags hug the image on either side and in any combination: `--ro`/`--force`/`--status` before **and** after the image, multiple value-less flags, `--hostname=h1` equals-style, a value option consuming its next token (not the command), an **unknown** flag left for the command, and `--` forcing the boundary; a command's own `-v`/flags are never eaten | ☑ unit (`test_options_after_image_are_hoisted`, `test_hoist_handles_multiple_flags_both_sides_and_equals`, `test_command_flags_after_command_word_are_untouched`, `test_double_dash_disables_hoist`, `test_hoist_leaves_path_mode_and_bare_invocations_alone`) |
| K2 | entry operation order | emulation setup (host binfmt) → acquire lock → enter, in that order | ☑ unit (`test_entry_emulates_then_locks_then_enters`) |
| K3 | `--dry-run` mutates nothing | a plan sets up **no** emulation and creates **no** lock (PY-CORE-004) | ☑ unit (`test_dry_run_sets_up_no_emulation_and_no_lock`) |
| K4 | HA refusal is first | the non-master rw gate (exit 10) fires **before** any binfmt/lock mutation | ☑ unit (`test_ha_refusal_precedes_emulation_and_lock`) |
| K5 | no-command refusal is first | non-tty + no command (exit 7) fires **before** any binfmt/lock mutation | ☑ unit (`test_non_tty_no_command_refused_before_emulation`) |
| K6 | live-lock resolved before acquire | the prompt/force/refuse policy runs **before** `acquire()`, so `acquire()` never faces a live holder (with `--force`, the holder's lock is already cleared by the time acquire runs) | ☑ unit (`test_live_lock_is_broken_before_acquire`) |
| K7 | teardown signal order | children-before-parents (→ C10) | ☑ unit |

## Notes

- The harness scenarios (S1–S11) in `docs/lchroot/adversarial/POKE_HOLES.md` are the
  LLM-executed counterpart to the D-series here; RESULTS-*.md logs each run.
- "Oracle" = the bash `lchroot` at `/usr/sbin/lchroot` (see `findings-lchroot.md`).
- **Entry-point exit-code mapping** is unit-pinned in `tests/test_entry.py`:
  `ConfigError/ImageResolveError/LunaError → 7`, `LockedError → 9`, `ExecError/other
  LbwrapError → 1`, `KeyboardInterrupt/EOFError → 130` (PY-CORE-006 / PY-ERR-007).
- **Transport status policy** is unit-pinned in `tests/test_transport.py`: the curated
  success set `{200,201,202,204}` decodes; 3xx/4xx (incl. 302/404) raise `LunaError`.
- **Ambiguous-path refusal (A6 / ADR 0005)** and **HA-sync-not-gated-on-exit-code (E2/F2
  / ADR 0004)** are unit-pinned in `tests/test_luna.py` and `tests/test_main.py`.
- **Section I (destructive) status:** the pipeline is now **parametrized over distros**
  (el9/`login`, `ubuntu-image`, `opensuse-image`), each on its own disposable clone
  (D23). **el9 is fully green this session**: native install, docker install,
  docker-cannot-run, kernel-module-denied, detached-teardown, and DOCA (I1, 41 min @
  60-min budget) all pass against a `login` clone — clone create/use/remove re-validated
  twice. Teardown is `--die-with-parent` (D18/ADR 0006); the harness is un-leakable
  (no-hang + process-group-kill + autouse `pkill`, D19). **ubuntu/opensuse pipelines are
  set up but not yet run live** (docker is their heavy install; DOCA is el9-only). The IDs
  above (I1–I5) predate D23's parametrization; tests now appear as `…[el9|ubuntu|opensuse]`.
  `compute-image` was best-effort reset but is not pristine (D21); it is no longer a
  regression target. Full brief: the "RESOLVED 2026-05-31" block in `JOURNAL.md`.
