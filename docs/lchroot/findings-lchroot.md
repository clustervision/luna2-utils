# Behavioural map of `lchroot` (the oracle)

Per the migration discipline in `skill-python.md` (§ Bash → Python migration),
the existing bash `lchroot` is treated as a **behavioural oracle**: we preserve
its observable behaviour, not its structure. This document is the map to build
from. Do not start implementation expecting to beat this — match it first.

Source inspected:
- `/usr/sbin/lchroot` → symlink → `/trinity/local/python/lib/python3.10/site-packages/utils/lchroot` (bash, 130 lines)
- `contrib/libluna-fakeuname/libluna-fakeuname.c`
- `luna.ini` at `/trinity/local/luna/utils/config/luna.ini`
- house style: sibling `lnode.py` (luna2 python util)

## Environment (Rocky 9.7 controller, 2026-05-29)

| Thing | Value |
|---|---|
| Host OS | Rocky Linux 9.7 (el9), kernel 5.14.0-611.x |
| bubblewrap | `/usr/bin/bwrap`, **0.6.3** (has `--dev/--proc/--unshare-*`; **no `--overlay`**, that needs ≥0.5.0 — present, but verify before relying) |
| System python | 3.9.25 |
| TrinityX python | `/trinity/local/python/bin/python3` → 3.10 (this is what lchroot's utils run under; target this) |
| Images | `/trinity/images/{compute-image,compute-image-bubble,login,opensuse-image,ubuntu-image}` |
| Play image | **`compute-image-bubble`** → `/trinity/images/compute-image-bubble` (redhat / el9) |
| Luna CLI | `/usr/bin/luna`, API up at `the controller:7050` (https, self-signed) |
| fakeuname lib | `/usr/lib64/libluna-fakeuname.so` — **also present inside each image** at `/usr/lib64/`, so it resolves inside the sandbox root |

## What `lchroot <osimage> [cmd...]` does, step by step

1. **Parse `luna.ini`** by hand (bash INI reader). Reads `API_*` keys
   (USERNAME, PASSWORD, ENDPOINT, PROTOCOL, VERIFY_CERTIFICATE).
   - `VERIFY_CERTIFICATE=false/no` → adds `--insecure` to curl calls.
2. **Require osimage arg** (`$1`); else print help, `exit 7`.
3. **Auth**: POST `{username,password}` to `${PROTO}://${ENDPOINT}/token`, scrape token.
4. **HA check**: GET `/ha/state`; parse `ha.enabled`, `ha.master` with `jq`.
   - If HA enabled and **not** master → print loud warning ("changes will be lost after sync").
5. **Resolve image**: GET `/config/osimage/<name>`; extract via `jq`:
   - `kernelversion` → `FAKE_KERN`
   - `path` → `CHROOT_PATH`
   - Missing kernelversion → non-fatal warning (LD_PRELOAD lines may print).
   - Missing/`null` path → `exit 7`.
6. **Lock**: if `$CHROOT_PATH/tmp/lchroot.lock` exists → print who holds it, `exit 9`.
   Else write `PID <pid> on <tty>` into it.
7. **`trap clean EXIT`** + `mount_d`:
   - `mount -t devtmpfs devtmpfs <path>/dev` (on failure → skip its umount)
   - `mount -t sysfs sysfs <path>/sys`
   - `mount -t proc proc <path>/proc`
   - The `NEED_UMOUNT_*` flags track which mounts succeeded so cleanup only
     unmounts what it mounted. **Re-entrancy detail worth preserving.**
8. **Enter**: `shift` off the osimage arg, then:
   ```
   FAKE_KERN=$FAKE_KERN LD_PRELOAD=libluna-fakeuname.so \
     PS1="chroot [\u@$OSIMAGE \W]$ " chroot $CHROOT_PATH "$@"
   ```
   - With no extra args, `chroot` runs the image's default shell interactively.
   - `LD_PRELOAD=libluna-fakeuname.so` + `FAKE_KERN` → `uname -r` inside reports the
     **image's** target kernel, not the host's. Critical so that `%post` scriptlets,
     `dkms`, weak-modules, kernel-version-pinned installs behave as if booted on the node.
9. **Cleanup (`clean`, on EXIT)**: umount dev/proc/sys (force `-f`, only those mounted),
   remove the lock file.
10. **HA sync**: if HA enabled **and** master → GET `/ha/syncimage/<name>` to push the
    mutated image to other controllers. (Fire-and-forget; output discarded.)

## Inputs / outputs / side effects (the contract)

- **Args**: `osimage` (required), then optional command + args passed through.
- **Env read**: none from caller; everything from `luna.ini` + API.
- **Env set into the chroot**: `FAKE_KERN`, `LD_PRELOAD`, `PS1`.
- **Files written on host**: `<path>/tmp/lchroot.lock` (created/removed).
- **Mounts on host mount table**: `<path>/{dev,sys,proc}` (created/removed). ← the
  thing bwrap eliminates: these are *host-visible* and leak/persist if cleanup is
  skipped (kill -9, crash).
- **Network**: luna API token + ha state + osimage config + (master) syncimage.
- **Destructive**: the whole point — operator mutates the image tree in place.

### Exit codes (canonical contract — audited 2026-06-02)

The bash oracle consumed only `7` (bad args / no path) and `9` (locked); **those two are
preserved exactly**. The Python tool extends the map (`1`, `8`, `10`, `130`) for failure
classes the oracle never had a clean code for. The single source of truth is
`__main__.main()` (the only place codes are set); every `raise` was traced to confirm this
table.

| Code | Meaning | Raised by | Origin |
|---|---|---|---|
| `0` | success | normal exit, or the session command's own `0` | oracle |
| `1` | any other / unexpected tool failure | `LchrootError` base, `ExecError` (subprocess), `SandboxError` (bwrap missing/unexecutable), the internal-error guard | extension |
| `7` | bad input: config / image-resolve / luna | `ConfigError`, `ImageResolveError`, `LunaError`; plus the "no osimage (and no `--path`)", "`--list-images` without luna", and "non-interactive entry with no command" (ADR 0014 — a bare non-tty entry would launch a shell that reads EOF and does nothing; `--dry-run` is exempt) guards | **oracle** |
| `8` | foreign-arch emulation setup failed | `EmulationError` (no qemu-user binary, binfmt could not be arranged) | extension |
| `9` | image locked / in use | `LockedError` (a live lock refused non-interactively, or a declined tty prompt to break it) — see `--force` (ADR 0014) | **oracle** |
| `10` | refused on a non-master HA controller | `SecondaryControllerError`: a rw entry when HA is enabled and this host is not the master (no override; `--ro` is allowed; non-HA installs never hit it). ADR 0011 | extension |
| `130` | interrupted (Ctrl-C / Ctrl-D) | `KeyboardInterrupt` / `EOFError` at the boundary (128 + SIGINT) | extension |
| *(passthrough)* | the session command's exit code | `run_interactive` returns the child's `returncode` verbatim | oracle behaviour |

Notes:
- **`SandboxError` is wired** (was dead): a missing/unexecutable `bwrap` raises it (clean
  message "is bubblewrap installed?" → `1`) instead of leaking a raw `FileNotFoundError`
  traceback. It has no dedicated code — like `ExecError` it falls through `except
  LchrootError` to `1`.
- **Locked message names the holder** and points at the remedy. The non-interactive
  refusal reads `image 'cib' is held by a live session (PID … on …); refusing to break it
  non-interactively. Re-run on a terminal, inspect with `lchroot --status cib`, or pass
  --force to break the lock and run anyway` (ADR 0014). This is the message `luna osimage
  pack` surfaces — pack runs lchroot non-interactively **without** `--force`, so it must
  **not** break/steal a locked image itself (forcing can corrupt the rpmdb); it fails on
  lchroot's own exit `9` + this message rather than inventing its own.
- **No raw tracebacks (escape-path audit).** Two non-obvious paths used to escape
  `main()` as an uncaught traceback; both are now closed so *every* failure maps to a
  code:
  - **Network failures.** `requests` exceptions (connection refused, DNS, TLS, timeout)
    are `OSError` subclasses; the best-effort luna calls caught them, but `token` /
    `ha_state` / `resolve_image` did not. The transport now translates every
    `RequestException` → `LunaError` at its seam (`_reach`), so an unreachable controller
    exits `7` ("cannot reach Luna API at …") instead of a traceback.
  - **Backstop.** `main()` has a final `except OSError` → `1`, so any unforeseen I/O
    failure not already wrapped (e.g. an unwritable lock file, or the missing-`bwrap`
    `OSError` if the targeted `SandboxError` wrap is ever bypassed) still exits cleanly
    with a logged message.
- **Message clarity:** every user-facing message was reviewed — config (names the file +
  cause), luna (names the URL/status), image-resolve (names the osimage + reason), locked
  (names the holder + the `--status`/`--force` remedy), emulation (names host/image arch).
- **Non-master HA gate (exit `10`, ADR 0011).** When HA is enabled and this host is not the
  master, a read-write entry is refused (`SecondaryControllerError`) so images are only
  mutated on the master — no override. `--ro` is allowed (it cannot mutate) and non-HA
  installs never hit it. lchroot performs no HA image sync (the oracle's sync was a no-op;
  ADR 0011 supersedes 0004).
- Tests pin every mapping: `tests/test_entry.py` (7/8/9/10/1/130 boundary mapping +
  OSError-backstop), `tests/test_main.py` (locked-message content, `SandboxError`-on-launch,
  the non-master gate), `tests/test_transport.py` (network failure → `LunaError`).

## lchroot-legacy (bash) → lchroot (bwrap) mapping (design intent)

| lchroot-legacy (bash) | lchroot (bwrap) |
|---|---|
| `chroot $PATH` | `bwrap --bind $PATH /` (rw) / `--ro-bind` for `--ro` mode; internal `pivot_root` |
| `mount devtmpfs /dev` | `--dev /dev` (namespaced minimal devtmpfs) |
| `mount proc /proc` | `--proc /proc` (fresh, meaningful with `--unshare-pid`) |
| `mount sysfs /sys` | `--ro-bind /sys /sys` (sysfs can't be freshly mounted in a userns; bind host RO) |
| `trap clean EXIT` + 3× umount | nothing — namespace teardown is automatic & crash-safe |
| lock in `$PATH/tmp/lchroot.lock` | keep, **but manage on the host side** in the wrapper (host dir), not inside the sandbox; rename to `lchroot.lock` |
| `LD_PRELOAD=...` + `FAKE_KERN=` | `--setenv LD_PRELOAD libluna-fakeuname.so --setenv FAKE_KERN <ver>` (lib is inside the image root, resolves) |
| custom `PS1` | `--setenv PS1 "lchroot [\u@<img> \W]\$ "` |
| (none) | `--unshare-pid --unshare-uts --hostname <img>` → scriptlet `kill`/`systemctl` can't reach controller |
| (none — chroot keeps all caps) | **explicitly** `--cap-drop CAP_SYS_ADMIN` (rw) / `--cap-drop ALL` (ro) → `mount -o remount,rw` fails; protections hold vs root. ⚠ bwrap does **NOT** drop caps by default when run as root — verified, see JOURNAL.md L6 |
| (none) | `--tmpfs /run` so transient unit/socket dirs don't litter the image |
| HA syncimage on master exit | **dropped** — the oracle's sync was a no-op; lchroot does no image sync. Instead it **gates**: rw entry is refused on a non-master HA controller (exit 10, no override; `--ro` allowed). ADR 0011 (supersedes 0004) |

## Latent lchroot-legacy bugs lchroot fixes (don't port them — go.md gotcha #9)

- **`CUR_TTY=$(tty)` under `set -e` kills lchroot with no controlling terminal.**
  Confirmed 2026-05-30: a side-by-side run (`lchroot compute-image-bubble /bin/sh -c
  'echo hi'`) in a non-interactive context exits **1** after printing only
  `IMAGE PATH:` — the command never runs. Root cause: line 116 `CUR_TTY=$(tty)`;
  `tty` returns exit 1 when stdin isn't a terminal, and `set -e` aborts the script
  before the chroot. So **lchroot only works interactively** — it breaks under
  cron/CI/pipes/automation. lchroot handles this correctly (`lock._current_tty()`
  catches `OSError` → "unknown") and runs fine non-interactively. This is a concrete
  win, not just parity.

## Side-by-side parity result (2026-05-30)
Same image + command, lchroot `--ro` vs bash lchroot-legacy:
- lchroot: `uname=5.14.0-611.30.1` (image kernel via fakeuname; host is `…-611.24.1`),
  `user=root`, `host=compute-image-bubble`, `FAKE_KERN`/`LD_PRELOAD` set,
  `TMPDIR=/tmp`, **exit 0**, 0 host mounts left.
- lchroot: **exit 1**, command never ran (the tty bug above), so a literal output
  diff isn't possible on this host. lchroot matches the *intended* oracle behaviour
  (this map) and exceeds the current live lchroot.

## Open questions to resolve before/while implementing

1. **DNS inside the sandbox.** `chroot` used the image's `/etc/resolv.conf` as-is.
   Package installs that need name resolution may want `--ro-bind /etc/resolv.conf
   /etc/resolv.conf`. Add a flag? (Default off to match lchroot; document.)
2. **`/sys` writability.** Some `%post` scripts poke `/sys`. RO bind matches the
   "protect the host" goal; confirm nothing in the normal image-build flow needs RW.
3. **`--dev` completeness.** bwrap's `--dev` gives null/zero/full/random/tty/etc.
   If an install needs a specific node, add a scoped `--dev-bind` (documented,
   sparingly). Capture which nodes real image builds touch as fixtures.
4. **Lock semantics.** lchroot's lock is whole-image. Keep that; do not weaken to
   per-pid. Stale-lock handling — **RESOLVED:** lchroot auto-reclaims a dead holder's
   lock on the next entry (legacy needed a manual `rm`); a *live* holder blocks entry and
   is broken with `--force` (stops the holder, then enters/runs) — prompted on a tty,
   refused non-interactively without `--force`. ADR 0014 (`--force` replaces the `--unlock`
   verb from ADR 0012, which had replaced the older `--kill`/`--force` pair).
5. **Resource limits.** Optionally wrap in `systemd-run --scope -p MemoryMax=
   -p TasksMax=` for the fork-bomb/RAM case. Make it opt-in (`--limits`), not default.
6. **`bwrap` privilege model on this host. — RESOLVED, verified 2026-05-29.**
   `bwrap --unshare-pid --ro-bind / / --proc /proc echo ok` works as root (no
   unprivileged-userns dependency). BUT a default root invocation **retains all
   capabilities** (`CapEff: 000001ffffffffff`), so the read-only guarantee is fake
   unless caps are dropped explicitly. Use `--cap-drop CAP_SYS_ADMIN` (rw mode) /
   `--cap-drop ALL` (ro mode). See JOURNAL.md L6 — this is the project's #1 lesson.
