# lchroot — project essentials (START HERE)

> Lean entry doc: what the project is, where everything lives, how to run it, the
> invariants you must not break, and the next actions. **Full detail — every decision
> (D1–D28), lesson (L1–L23), the ARM/foreign-arch design, adversarial results, and the
> original session handoffs — is in [`JOURNAL.archive.md`](JOURNAL.archive.md).** When a section here
> says "see JOURNAL.archive.md", that's where the rationale lives.

---

## ✅ TrinityX 16.1 fix + validation pass — DONE (2026-07-08)

Brought the `lchroot-bwrap` branch clean onto **TrinityX 16.1** (controller `trx16`, bwrap 0.6.3,
Python 3.10.12) and fixed everything the review surfaced. Baseline was already green on 16.1, and
the Luna API is a **full match** against the TX16 daemon — so this was defect/packaging/CI fixing,
not a behavioural port. 7 commits on top of the pristine pack; gates green throughout
(ruff + mypy + pytest **192 tests / 95.7% cov** + bandit 0). Full reviewer-facing writeup:
workspace `handover-lchroot.book.md`.

- **Security/correctness:** D1 `--path` traversal/symlink escape to host root closed (now
  `resolve(strict=True)`, ADR 0015); D2 `%`-in-password configparser crash; D3 `rm -rf /` test
  payload; D10 numeric kernel-version sort.
- **Structure/KISS:** D9 `ResolvedImage`→`types.py`; D11/D12 transport (warning-suppression to
  entrypoint, drop dead 204); D13 emulation adapter rename; D14 `lchroot-legacy` via bash; D15
  entrypoint/hygiene.
- **Tests/gates:** D16 qemu-static CLI tests + parametrized exit-code table + coverage +ruff N/FBT;
  K1 integration suite targets this checkout (`-m utils.lchroot`); K3 bandit in the gate (`# nosec`
  on the sanctioned subprocess site); K2 doc `LBWRAP_*`→`LCHROOT_*` + path drift.
- **Packaging/CI:** D4 `find_packages()` + new `utils/utils/__init__.py` (so `utils.utils` ships);
  D5 drop dead `lslurm`; D6 `python_requires>=3.10`; D7 GPLv3; D8 CI gates on main/development +
  pinned tools + `need:[test]` + `python -m build`.
- **One real 16.1 delta:** TX16 systemd says *"running in chroot, ignoring command"* (not 15.3's
  *"failed to connect to bus"*) — same containment; `test_systemctl_cannot_reach_host_bus` broadened.
- **Live-validated on 16.1:** enters real `compute-image` (fakeuname → image kernel); rw `dnf
  install` persists to a clone, **not** the host; remount/`/etc`-write denied in `--ro`; PID
  isolation (pid1=bwrap); `--path` escape refused; **7/7 read-only containment tests pass**.
- **Deferred (see handbook §5):** vulnerable shared pins `requests`/`urllib3` (K4, repo-wide bump,
  pip-audit kept advisory); pre-existing broken `bootutil` entry; two minor test/setup nits.

## ✅ ARM build uses lchroot EVERYWHERE — DONE (2026-06-03)

> Goal was: make the `compute-arm` image build use **lchroot everywhere** (not raw chroot)
> and prove it by building clean, end-to-end, until it **completes + packs**. **Achieved.**

**Outcome.** `compute-arm` built end-to-end with lchroot at every image-entry point and
**packed OK**: `ok=355, failed=0`; 2.2 GB `compute-arm-<ts>.tar.bz2` + kernel + initramfs +
torrent in `<luna files dir>`. lchroot is now used by all three entry points (was: only one):
- **Phase B in-image setup chain** → new **lchroot Ansible connection plugin** (was raw
  `chroot` connection — the TMPDIR-leak source).
- **initramfs** (`trinity/dracut-sysroot`) → `lchroot --path` (already).
- **pack** (`luna/osimage-pack`, the installed daemon) → `lchroot --path` (ADR 0010; the
  `pack-via-lchroot` daemon was deployed + restarted, then exercised live by the pack).

**What it took — one new plugin, one real TrinityX bug, and 4 faithful chroot-equivalence
fixes.** Driving a real build through lchroot exposed every place the setup chain leaned on
raw chroot's *shared* namespaces (which lchroot deliberately isolates). Each fix restores the
chroot-equivalent behaviour **without** weakening the sandbox. Full categorised write-up:
[`arm-build-notes.md`](arm-build-notes.md). Summary:

| # | Symptom under lchroot | Root cause (lchroot isolation) | Fix | Layer |
|---|---|---|---|---|
| connection | Phase B used raw chroot (no TMPDIR pin/lock/sandbox) | — | **`lchroot` connection plugin** (derives from `community.general.chroot`; exec via `lchroot --path … -- …`) wired in `site/ansible.cfg` + `site/dynamic_hosts` (`ansible_connection=lchroot`) | trinityx |
| hostname | `trinity/init` "Verify controller hostname" failed | `--unshare-uts` → in-image hostname = image name, not controller | new lchroot **`--hostname NAME`**; plugin passes `socket.gethostname()` | **lchroot** + plugin |
| in_image / read_facts | wrong in-image/controller task split; luna creds never loaded | role idiom `ansible_connection in 'chroot'` is a backwards substring test (`'lchroot' in 'chroot'`=False) — **a real latent bug** | flip to `'chroot' in ansible_connection` (matches chroot+lchroot, excludes ssh) in `trinity/init`, `ansible/read_facts`, `trinity/trix-tree` | trinityx (bug) |
| service backend | "Could not find the requested service haveged" | `service` module auto-detects init from `/proc/1` + `/run/systemd/system`; `--unshare-pid`+tmpfs `/run` defeat it → falls to sysvinit | `module_defaults: {service: {use: systemd}}` on in-image plays | trinityx |
| service_mgr fact | prometheus `assert ansible_service_mgr=='systemd'` failed | gathered fact = `bwrap` (`/proc/1`) | play var `ansible_service_mgr: systemd` | trinityx |
| systemd module | "Service is in unknown state" (state: started offline) | `--unshare-pid` defeats Ansible's `is_chroot()` (`/proc/1/root==/`) → tries live D-Bus | **lchroot sets `SYSTEMD_OFFLINE=1`** (documented offline switch; honoured by systemctl + Ansible) | **lchroot** |

> The two **lchroot** fixes (`--hostname`, `SYSTEMD_OFFLINE=1`) are env-level and benefit any
> caller; gates green (pytest 147). The rest are Ansible-layer (a generic sandbox tool must
> not learn Ansible internals). Cool Fact added to `JOURNAL.archive.md` ("offline-image semantics
> match chroot without its weakness"). The Ansible service-mgr shim is **consolidated**: one
> canonical documented block in `imports/trinity-redhat-image-setup.yml`; the slurm play
> repeats only the one `module_defaults` line (Ansible doesn't inherit it across plays).

**How to functionally test the packed ARM image (KVM boot).** Recipe + driver in this dir:
[`arm-boot-recipe.md`](arm-boot-recipe.md) + [`armboot-driver.sh`](armboot-driver.sh)
(KVM-aware). It does a
**direct-kernel boot** off a local virtio disk using a **no-luna** initramfs (regenerated via
`lchroot --path … dracut --omit "luna nfs …"` so it boots standalone instead of trying to
PXE/network-provision). On an **aarch64 host**: copy the rootfs tarball + kernel +
`initramfs-standalone.img` + driver, `dnf install qemu-kvm qemu-img`, run
`armboot-driver.sh <dir>` → builds the disk, boots headless with KVM, watches the serial
console for `login:`/multi-user (root pw `armtest123`). On x86 it's the same but pure TCG.
⚠️ **This Rocky environment has NO usable `qemu-system-aarch64`** (not in the curated
clustervision/Rocky mirror; the node can't reach external mirrors; the only build, openSUSE's
`qemu-arm`, needs `libutil.so.1(GLIBC_2.17)` which Rocky 9's glibc 2.34 dropped → won't run).
So the full VM boot must run on an **aarch64 box** (best) or an x86 box with a real
qemu-system-aarch64. Artifacts are staged on a destroyable compute node under `~/armboot/`.

**Validated without a full boot (safe, via lchroot):** kernel is aarch64 EFI; the initramfs
carries `virtio_blk`+`ext4`; in-image `systemd 252` executes under qemu-user; and the build's
offline `systemctl enable`s produced real symlinks (`slurmd, munge, prometheus-node-exporter,
haveged, sshd` all **enabled**) — strong evidence the image boots to userspace.

**✅ CLEAN FROM-SCRATCH REBUILD — DONE (2026-06-03, chat 4).** The unambiguous proof is in:
`luna osimage remove compute-arm` (also dropped the rootfs) + `rm -rf /trinity/images/compute-arm`
→ empty slate → `cd /root/git/trinityx-combined/site && TMPDIR=/tmp setsid nohup ansible-playbook
compute-arm-rocky.yml` (fully detached, ~75 min under qemu). Built **GREEN from nothing on the
consolidated playbook** (`65916517`): `compute-arm ok=356 failed=0`, `controller1 ok=102 failed=0`,
`localhost ok=2 failed=0`; `unreachable=0` everywhere. `foreign-bootstrap` created the rootfs from
scratch (fresh image dir + repos + `/dev` + `dnf --installroot --forcearch` core set), initramfs
built via `lchroot --path`, `luna/osimage-pack` packed. Fresh artifacts (ts `1780491445`/`1780490608`):
2.28 GB `compute-arm-1780491445.tar.bz2` + 216 MB initramfs + 13 MB aarch64 vmlinuz + torrent in the
luna files dir; osimage auto-re-registered; rootfs 4.5 GB, **lock released cleanly** (die-with-parent).
Boot-readiness re-confirmed via `lchroot --ro --path`: aarch64 systemd image with `slurmd, munge,
prometheus-node-exporter, haveged, sshd` all **enabled** (real symlinks from the offline enables).
No code changes needed — the committed fixes carried a from-scratch build with zero failures.

**✅ VM BOOT TEST — DONE (2026-06-03, chat 5).** The packed from-scratch aarch64 image **boots to a
multi-user `login:` prompt** under a real `qemu-system-aarch64`. The old "blocked — no compatible
`qemu-system-aarch64`" caveat is RESOLVED: Rocky's `qemu-kvm` is host-arch-only and nothing in the
repos/box/gitlab ships an aarch64 system emulator (the committed Fedora qemu is *user-mode* only), so
we **built `qemu-system-aarch64` 10.1.5 from qemu.org source on Rocky** (links Rocky glibc 2.34 → runs
natively, no container/glibc fight) and ran the boot on **node001** (15 GB free vs the controller's
~2 GB). Direct-kernel boot of the fresh `compute-arm-1780491445` rootfs → `Rocky Linux 9.8 … aarch64 …
localhost login:`. Full method in [`arm-boot-recipe.md`](arm-boot-recipe.md) §"What actually worked".

**▶ NEXT — Tomorrow (2026-06-05), priority order.** End of a big 2026-06-04 across three repos:
1. **luna2-utils `lchroot-bwrap` — review & commit/MR.** The ADR-0014 `--force` change (replaces
   `--unlock`), the regression expansion (**177 tests**, lock/hang/order), and the docs
   (findings/regression/JOURNAL + ADRs 0012–0014) were **committed end-of-day as a BACKUP to
   `alex/luna2-utils`** (gates green) — NOT reviewed/MR'd. Review the diff, re-split commits if
   wanted, then open the luna2-utils MR (folds in backlog #9). Gate: `ruff/format/mypy --strict/pytest`.
2. **trinityx-docs — open the two MRs.** `alex-lchroot-rewrite` + `alex-arm-image-docs` (pushed to
   `alex/trinityx-docs`, `--strict`-clean) → MRs into `clustervision/trinityx-docs`, colleague-reviewed.
   **Merge the two close together** — they cross-link via site-root URLs, so a lone merge leaves one
   transient 404. Sanity-read the "braggy" claims + the `~/trinityX/site` path assumption first.
3. **Discuss-with-dev** (Alex to raise; both written up below): luna2-utils CI gaps (item 7b);
   docs CI/CD (copy-paste message ready — per-MR preview + strict gate + protected-branch confirm;
   maintainer TBD, image hints van Meijel not Sumit).
4. **Backlog (unchanged, post-merge):** daemon-pack push + ADR (#5), productionize (#10),
   qemu-static RPM split (#17), team deck (#14).
- *Engineering-standards kit (DOC-META-001 loading protocol) is DONE + merged to
  `alex/engineering-standards` main — no follow-up.*

### Session log — 2026-06-04 (chat 9) — TrinityX docs drafts + engineering-standards kit loading protocol
- **Docs drafts written & pushed to `alex/trinityx-docs`** (NOT MR'd; `mkdocs --strict` clean,
  0 warnings). `alex-lchroot-rewrite`: new `admin/lchroot.md` (full + braggy, ADR-0014 `--force`
  lock model), `utils.md` stub keeping the `#lchroot` anchor, decision-D cross-link pass
  (`image.md`/`luna.md`/`osimages.md`). `alex-arm-image-docs`: new `howtos/build-arm-image.md` +
  an `anyimage.md` "different architecture" subsection. Full handoff in the "TrinityX docs rewrite"
  section; followed the mkdocs kit (branch→serve→strict→push).
- **CI/CD reality captured as discuss-with-dev** (both below): luna2-utils CI (7b — strict gate
  only on MR + the `lchroot-bwrap` branch, non-strict prod, unpinned tools); trinityx-docs CI (no
  real gate — non-strict, `only: build`; no preview beyond a shared `mkdocs serve` host). Wrote a
  copy-paste message for the docs maintainer (per-MR Pages preview + MR strict gate + protected-
  branch confirm + Material-theme upgrade).
- **engineering-standards kit hardened — DONE + MERGED** (`alex/engineering-standards` main,
  `0a570e4`). Prompted by a real miss (answered docs-workflow Qs from a half-loaded kit). Canonical
  **"Load the standard before you apply it"** in `PRINCIPLES.md` + per-kit entrypoint pointers
  (python/go/gitlab-trinityx) + mkdocs **DOC-META-001**: match load depth to the task, read whatever
  you open in full, a half-read item is worse than an unread one. Memory carries the "mkdocs ⇒
  full-load" trigger.

### Session log — 2026-06-04 (chat 8) — regression-test pass: lock edge cases, hang/NFS bounds, ordering
- **Extended the regression suite** (review + add, per Alex). Unit suite **154 → 177 tests,
  97.2% cov**; all gates green. New coverage, by theme:
  - **Lock edge cases (regression §C8–C10):** malformed/partial/empty/binary lock files are
    *reclaimed*, never fatal (a half-written lock can't wedge the next run); custom osimage
    `name=` is quoted in the locked message; `/proc`-walk degrades (pid vanishing mid-scan →
    ppid=None/cmdline="" ; absent root → safe singleton; NUL-argv joined); `terminate_holder`
    SIGTERMs **children-before-parents** (asserted order).
  - **Hang / NFS / slow-storage (new regression §J):** the headline one — a holder that
    **won't die** (D-state / wedged NFS I/O) does NOT spin lchroot forever: `terminate_holder`
    polls only to `grace_s`, escalates SIGTERM→SIGKILL, then clears the lock and reports the
    survivor (proved on a *fake clock*, bounded sleeps). Plus die-before-signal race,
    release-when-already-gone, and the `Executor` timeout seam (`TimeoutExpired`→`ExecError`).
    Documented J8 as a **known limitation**: an image on a *dead* NFS mount can still block in
    the kernel (no per-syscall fs timeout — rejected as over-engineering); mitigations listed.
  - **Ordering (new regression §K):** operation order emulate→acquire→enter; a plan /
    HA-refusal / no-command-refusal mutate **nothing** first (no binfmt, no lock); live-lock
    resolved before `acquire()`; expanded option-hoisting permutations (both sides, multiple
    flags, `--hostname=h1` equals-style, value-token vs command, unknown-flag-left-for-command).
  - Tests added in `test_lock.py` (+15), `test_main.py` (+7 ordering/permutation/re-entrancy), `test_executor.py` (+1).
  - Docs: `regression.md` got a 2026-06-04 status block, expanded §C, and new **§J (hang/NFS)**
    + **§K (ordering)** with each row mapped to its test.
- **Not committed yet** — branch `lchroot-bwrap`, pending review.

### Session log — 2026-06-04 (chat 7) — `--force` replaces `--unlock` (ADR 0014): break-and-enter, not free-and-exit
- **Operator changed their mind on yesterday's `--unlock` (#18 / ADR 0012).** The common
  case is "free the lock **and use** the image", not "free it and walk away". So the
  lock-override is now a **flag on the normal entry path**, `--force`, that breaks a *live*
  lock and then does the usual thing — **enter** on a tty, **run the command** otherwise.
  `--unlock` and the standalone "free-without-enter" capability are **removed** (no real
  caller: `osimage pack` must not break a locked image anyway and is served by the plain
  path's fail-fast exit 9). The word `--force` is what operators already reach for.
- **Behaviour matrix** — `--force` only changes the *live-lock* row (no-lock/stale are
  no-ops; stale always auto-reclaims): plain → tty prompts `[y/N]` (n = exit 9, holder
  spared), non-tty refuses (exit 9, names holder + remedy); `--force` → breaks it (stop the
  holder's tree via `terminate_holder`, never the `acquire(force=True)` steal) then
  enters/runs, on a tty or not. **New rule:** non-tty + **no command** → exit 7 (a bare
  non-tty shell reads EOF and does nothing, faking success); `--dry-run` exempt. **Always**
  describes the holder (PID/tty/tree + rpmdb warning) before breaking/asking/refusing.
- **Reverses two specifics of 0012** (recorded in **ADR 0014**, append-only supersede): the
  `--force` word is back, *and* the plain entry path now prompts on a tty (0012 had kept it
  non-interactive). The non-tty live-lock refusal — the gate that protects automation — is
  retained.
- **Touched:** `__main__.py` (`--force` arg replaces `--unlock`; `_lock_admin` split into
  read-only `_status` + boundary `_resolve_live_lock`; live-lock decided before `acquire`;
  non-tty-no-command guard), `lock.py` + `errors.py` (LockedError + docstrings → `--force`),
  `completions/lchroot.bash` (`--unlock` → `--force`). Docs: **ADR 0014**, findings exit-code
  table + open-Q4, regression §C2/C3/C4/C4b. Tests rewritten (prompt-yes/no, force tty/non-tty,
  pkg-mgr rpmdb warning, stale-reclaim, non-tty-no-command, non-tty refuse names `--force`).
  Gates green: ruff/format/mypy --strict, **pytest 154, 95.71% cov**.
- **Not committed yet** — on branch `lchroot-bwrap`, pending Alex's review.

### Session log — 2026-06-03 (chat 6) — CLI/UX pass (#18): `--unlock` replaces `--kill`/`--force`; dropped #19
- **Dropped #19** (image selection by node name) — probed the live luna API (a node record
  carries its effective `osimage` directly, so resolution was only one call), but the
  CLI-ergonomics + node/osimage ambiguity weren't worth it. Removed from scope.
- **Lock-admin redesign (#18).** Collapsed the confusing `--kill`/`--force` pair into one
  **`--unlock`**: clear the lock + tear down the holder's session, then exit without
  entering. Behaviour A — a *stale* lock clears silently, a *live* holder is confirmed on a
  tty (rpmdb warning if a package manager is running) and **refused non-interactively**
  (exit 9), so automation never silently kills a running session. No `--force`, no in-line
  takeover (free explicitly, then enter). `--status` unchanged.
- **Why it's enough.** `acquire()` already auto-reclaims a stale lock, so the flags only
  ever face a *live* holder; the tty-confirm/non-tty-refuse gate is the only safety needed,
  which let `--force` go entirely. The naming now pairs with the "image is locked" message.
- **Touched:** `__main__.py` (`--unlock` arg, `_lock_admin` rewrite, removed the `--force`
  takeover block), `lock.py` + `errors.py` (LockedError → point at `--unlock`; docstrings),
  `completions/lchroot.bash` (**fixed stale flag list** + value-option skip), tests
  (`--kill`/`--force` tests → `--unlock` confirm/decline/non-tty/stale cases). Docs: **ADR
  0012** (supersedes the CLI surface of 0002/0003), findings exit-code table, regression
  §C3/C4, POKE_HOLES S9. Gates green: ruff/format/mypy --strict, **pytest 148, 95.75% cov**.
- **Not committed yet** — on branch `lchroot-bwrap`, pending Alex's review of the rest of
  the #18 pass.

### Session log — 2026-06-03 (chat 5) — full-system VM boot of the from-scratch image PROVEN
- **Goal.** Functionally prove the packed from-scratch `compute-arm` image actually boots (the "KVM
  boot test" prior chats parked as blocked).
- **Diagnosis (the impasse, now closed).** `qemu-system-aarch64` is genuinely unavailable on this
  box: Rocky/RHEL `qemu-kvm` is **host-arch-only** (ships only `/usr/libexec/qemu-kvm`, x86_64 —
  verified by `repoquery` *and* by installing it); no Rocky/EPEL package and nothing on the box or in
  gitlab provides an aarch64 system emulator. The "qemu from Fedora, in luna2-utils" is the **user-mode**
  static (`qemu-aarch64-static`, source RPMs at `/root/rpmbuild/SOURCES/qemu-user-static-*-fc42.rpm`) —
  it runs aarch64 *binaries*, it does **not** boot a VM.
- **Two-level verification achieved.**
  1. **Userspace under qemu-user** (the committed Fedora static + binfmt, via `lchroot --ro --path`):
     the image's real aarch64 binaries execute on the x86 controller — `uname -m`=aarch64, `bash` is an
     ARM ELF, **systemd 252**, Rocky 9.8, **1025 rpms**.
  2. **Full-system VM boot** under `qemu-system-aarch64` **10.1.5 built from qemu.org source on Rocky**
     (so it links glibc 2.34 and runs natively — the fix the openSUSE/Fedora *binaries* couldn't be).
     Ran on **node001** (22 GB disk / 15 GB free; the controller `/` was at 94–96 %). Direct-kernel
     boot (`-machine virt -cpu max`, TCG) of the fresh `compute-arm-1780491445` rootfs reached
     `Rocky Linux 9.8 (Blue Onyx) / Kernel 5.14.0-687.12.1.el9_8.aarch64 on aarch64 / localhost login:`.
     `munge` fails at boot (no luna-provisioned `/etc/munge/munge.key` — expected for a standalone boot,
     not an image defect); everything else reached multi-user.
- **How to log in to the live VM** (left running with a telnet serial on node001): `telnet node001 5555`
  → `root` / `armtest123`. (Method + the two false starts — missing slirp, missing `efi-virtio.rom` —
  written up in `arm-boot-recipe.md`.)
- **No code changes.** Pure validation + an externally-built test tool. qemu dist kept at
  `/root/qdist` (controller) and `node001:/root/armboot/qemu`. Controller `qemu-kvm` + build deps
  were dnf-installed; build tree removed (freed 1.3 GB; `/` back to 84 %).
- **Process lesson (logged L24).** I over-engineered the qemu sourcing (a Fedora **docker container**)
  before checking the simplest options; Alex redirected twice ("just install KVM via dnf", "get it from
  qemu.org"). Building from source on the host was the right, simplest answer. See L24/L25/L26.

### Session log — 2026-06-03 (chat 4) — clean from-scratch ARM build green + packed
- **The proof run.** Removed `compute-arm` from luna (which also dropped the rootfs) + `rm -rf`
  the dir → genuinely empty slate, then ran `compute-arm-rocky.yml` fully detached
  (`TMPDIR=/tmp setsid nohup …`, log `/root/compute-arm-build10.log`, ~75 min). Built **GREEN
  from scratch on the consolidated playbook** (`65916517`): `compute-arm ok=356 / controller1
  ok=102 / localhost ok=2`, **all `failed=0 unreachable=0`**. `foreign-bootstrap` built the rootfs
  from nothing; initramfs via `lchroot --path`; `osimage-pack` packed (fresh ts `1780491445`:
  2.28 GB tarball + 216 MB initramfs + 13 MB aarch64 vmlinuz + torrent; osimage auto-re-registered;
  rootfs 4.5 GB; lock released cleanly). Services `slurmd/munge/prometheus-node-exporter/haveged/sshd`
  all **enabled** (verified via `lchroot --ro --path`).
- **No new code.** Pure validation — the chat-3 committed fixes (`--hostname`, `SYSTEMD_OFFLINE=1`,
  connection plugin, idiom flip, service-mgr shim) carried a from-scratch build with zero failures.
  This retires the "green run was only a resume" caveat from the DONE block above.
- **Disk note:** `/` at 91% (4.5 GB free) after the build — the superseded build9 artifacts
  (`compute-arm-1780472153-*`, `compute-arm-1780473014.tar.bz2`, ~2.5 GB) are still on disk and can
  be reclaimed now that the from-scratch tarball is the registered one.
- **Still blocked:** full KVM boot needs aarch64 hardware (no Rocky-compatible
  `qemu-system-aarch64` here) — unchanged from chat 3.

### Session log — 2026-06-03 (chat 3) — ARM build green + packed via lchroot everywhere
- **luna2-utils (`alex/luna2-utils` `lchroot-bwrap`):** added **`--hostname NAME`** (`93deee5`)
  and **`SYSTEMD_OFFLINE=1`** in the sandbox (`2194963`) — the two env-level chroot-equivalence
  fixes; + Cool Fact doc (`68f9b79`). Gates green throughout (ruff/format/mypy --strict, pytest
  **147**, ~95% cov).
- **trinityx-combined (`alex/trinityx-combined` `arm-image-build`):** `lchroot` connection
  plugin + wiring (`cdb289a4`); plugin passes controller `--hostname` (`cacc6b04`); flipped the
  backwards `ansible_connection in 'chroot'` idiom in `trinity/init`/`read_facts`/`trix-tree` +
  `service`→systemd `module_defaults` (`e3147ac9`); `ansible_service_mgr` fact override
  (`3eeafbec`); **consolidated** the service-mgr shim (`65916517`).
- **daemon:** the installed `luna2-daemon` already carried `pack-via-lchroot` (verified
  byte-identical to `dc89e4cc`); backed up + restarted; pack exercised live (kernel+ramdisk
  assembled via `lchroot --path`).
- **Build:** iterated builds 4→9 fix-forward; **build9 completed `failed=0`, packed** (2.2 GB
  tarball + kernel + initramfs + torrent). Was a *resume* on the pre-consolidation playbook →
  clean from-scratch rebuild deferred to a fresh chat (see DONE block above).
- **KVM boot test:** recipe + KVM-aware driver written (`arm-boot-recipe.md`,
  `armboot-driver.sh`); no-luna initramfs built via lchroot. Full VM boot **blocked in this
  env** (no Rocky-compatible `qemu-system-aarch64`; openSUSE build glibc-ABI-incompatible) →
  run on aarch64 hardware. Image validated boot-ready via lchroot (aarch64 systemd 252 runs;
  services enabled). Notes: [`arm-build-notes.md`](arm-build-notes.md).

### Session log — 2026-06-02 (chat 2), all committed to `alex/luna2-utils`
- **#2 exit-code audit + robustness:** wired the dead `SandboxError` (bwrap-missing → clean
  exit 1, not a traceback); closed two traceback escape paths — transport now maps `requests`
  network errors → `LunaError` (exit 7), and `main()` has a final `except OSError` backstop;
  canonical exit-code table in `findings-lchroot.md`. **#1:** `LockedError` names the image +
  points at `--status`/`--kill`. **#3:** GPLv3 header (ClusterVision, no year, `Author: Alex
  Ninaber`) on all 27 `.py` + legacy. **CLI help:** examples epilog + exit-code legend +
  filled blank `-v`/`--debug` help on both `lchroot` and `qemu-static`.
- **Phase C:** #5 ADR 0010 (daemon pack via `lchroot --path`); #6 `lchroot-legacy` shipped +
  wired (fixed arg-forwarding bug in `bash_runner`); #7 CI gap fixed (added `qemu_static` to
  ruff/mypy); #8 bundled aarch64-built `qemu-x86_64-static` (Fedora 9.2.4-2.fc42); #13 docs
  scrub (lbwrap→lchroot incl. ADRs by Alex's call; `/root` paths + IP + colleague identity
  removed — rule 4 done). Gates green throughout (pytest 145).
- **Repos:** `alex/trinityx-foreign-arch-image` → renamed to `alex/trinityx-combined`
  (faithful clone-of-main + branch). All 3 feature repos now consistent. (Alex to delete the
  old repo in the UI.)
- **TODOs added #18–#20:** CLI/UX review; node-name→osimage selection; offer-to-pack on rw
  exit. **ARM build:** clean rebuild proved Phase A from scratch; found the raw-chroot/TMPDIR
  issue above → this overnight task.

---

## What lchroot is
`lchroot` is a **bubblewrap-based, security-hardened replacement for the legacy bash
`lchroot`** (now `lchroot-legacy`). It enters an OS image (e.g.
`/trinity/images/compute-image`) as a sandbox so an operator can customise it — install
packages, edit config — **without endangering the controller**. It ships inside
ClusterVision's **`luna2-utils`** as `utils/lchroot/`. Foreign-arch (ARM-on-x86) entry is
delegated to the sibling **`qemu-static`** tool (`utils/qemu_static/`).

It is a **behavioural drop-in** for the bash oracle: same invocation muscle memory
(`lchroot <image>` → rw shell, no prompt), same exit codes (`7` bad args, `9` locked),
same `fakeuname`. It also fixes real oracle bugs (works non-interactively; no leaked
daemons; true read-only; real containment). Why each matters: JOURNAL.archive.md "Cool Facts" +
the lchroot-vs-lchroot-legacy table.

## Current state (2026-06-01)
**Feature-complete and in use.** Core built (sandbox, executor, errors, config, luna
client, host-side lock, CLI, `--path` luna-free entry, lock admin `--status`/`--kill`,
foreign-arch emulation). Gates green at code HEAD `18f25f1`: **ruff + ruff format + mypy
--strict (src+tests) + pytest 139 passed, 94.8% branch cov**. Containment proven live
(14/14 escape attempts denied; real `dnf`/docker installs contained). An aarch64 Rocky
image was built end-to-end on x86 and **booted under qemu-system-aarch64**.

`utils/lchroot/` modules: `__main__` `sandbox` `executor` `errors` `config` `luna`
`transport` `lock` `image` `emulation`. Plus `utils/qemu_static/` (the binfmt/qemu owner).

## Where everything lives (canonical)
- **Dev tree:** `<luna2-utils>`, branch **`lchroot-bwrap`**. Edit
  `utils/lchroot/` here. **NOT** `<dev-tree>` — that is a frozen reference/archive
  (its `.venv` is still the gate toolchain; see "How to run").
- **This running log:** edit `docs/lchroot/JOURNAL.md` (lean) + `JOURNAL.archive.md` (archive)
  *on the branch*, not the `<dev-tree>` copies.
- **Docs (`docs/lchroot/`):** `JOURNAL.md` (this), `JOURNAL.archive.md` (archive), `AGENTS.md`
  (agent entrypoint), `findings-lchroot.md` (oracle), `regression.md`, `decisions/`
  (ADRs 0002–0009), `adversarial/`.
- **Shared methodology kit:** the **`engineering-standards`** repo
  (`git@gitlab.taurusgroup.one:alex/engineering-standards.git`; local
  `<engineering-standards>`) — generic `python/` kit + `go/` guide. The lchroot
  `AGENTS.md` points at it. `/root/agents` is retired.
- **On the box:** dev launchers `/usr/local/sbin/lchroot` and `/usr/local/sbin/qemu-static`
  (each `cd <luna2-utils>` then `-m utils.<pkg>`). binfmt `qemu-aarch64`
  registered → `utils/qemu_static/qemu-aarch64-static`. NOT a clean install — test mods.

## Repos / branches / push identity
| Repo | Branch | State |
|---|---|---|
| luna2-utils (gitlab clustervision) | `lchroot-bwrap` | pushed to `alex/luna2-utils` HEAD `68f9b79` (docs) — code adds `--hostname` (`93deee5`) + `SYSTEMD_OFFLINE=1` (`2194963`) for the ARM build; pytest **147** green. The package, tests, docs, `qemu-static`, CI test stage. **MR to `development` deferred.** |
| trinityx-combined (gitlab clustervision) | `arm-image-build` | `alex/trinityx-combined` HEAD `65916517` — ARM playbook + `foreign-bootstrap`/`dracut-sysroot` roles **+ `lchroot` Ansible connection plugin + the chroot→lchroot compat fixes** (see DONE block). Builds `compute-arm` green to pack. |
| luna2-daemon | `pack-via-lchroot` | `dc89e4cc` — pack routes dracut via `lchroot --path`; proven live. Lives in github checkout (`origin` = github clustervision); **no longer local-only** (backed up to `alex/`, see below). GitLab clustervision push still deferred. |
| engineering-standards (gitlab **alex** personal) | `main` | generic methodology kit; pushed. |

**Personal backup mirror (gitlab `alex` namespace, all PRIVATE) — created 2026-06-01.** All
work here is consolidated under a private personal GitLab namespace as a backup, independent
of clustervision (clustervision repos untouched). Pushes go out under a dedicated personal
GitLab identity (see "Push identity"). Note: push-to-create is DISABLED on this GitLab — the
`alex/` projects must be pre-created in the UI before pushing; a shallow checkout (luna2-utils
was one) needs `git fetch --unshallow` first.
| Source | Branch | → `alex/` repo |
|---|---|---|
| luna2-utils (lchroot **+ qemu-static** + `docs/lchroot/` agent docs) | `lchroot-bwrap` | `alex/luna2-utils` |
| luna2-daemon | `pack-via-lchroot` | `alex/luna2-daemon` |
| trinityx-combined (ARM image build) | `arm-image-build` | `alex/trinityx-combined` (faithful clone-of-main + branch) |
| engineering-standards | `main` | `alex/engineering-standards` (already its canonical `origin`) |

**Update 2026-06-02 — reshaped to clone-of-main + branch (Alex's call).** The mirror is now a
faithful **clone of each clustervision main repo PLUS our feature branch**, not just the lone
feature branch. Pushed the `development` mainline to all three: for luna2-utils + trinityx,
`git push alex origin/development:refs/heads/development`; for the daemon (its `origin` is
**github**, and the checkout's `core.sshCommand` forces the personal GitLab identity so a
github fetch is denied) pushed `gitlab/development` (the clustervision *gitlab* remote) instead. So each `alex/`
repo now has BOTH `development` + the feature branch. **CAVEAT:** the daemon's `pack-via-lchroot`
was branched off an OLD development now 58 commits behind `gitlab/development`, so on
`alex/luna2-daemon` the feature branch diverges widely from `development` (fine as a backup,
messy as a diff). luna2-utils + trinityx branch cleanly off current `development`.

**Push identity (critical):** the box's DEFAULT ssh key belongs to a different user, so a
naive push can go out under the wrong GitLab identity. Each relevant checkout pins the correct
personal identity via its own `core.sshCommand` (an explicit `IdentityFile` +
`IdentitiesOnly=yes`); a partial flag set still falls back to the box default, so **verify the
pushing identity before every push** (`git ls-remote` / a dry push). **Always feature-branch →
MR; never push `development`/`main`; confirm remote ownership first.** GitHub authenticates
under a separate personal account. **The box has NO backups → commit + push often.**

## How to run
- **Gates:** `cd <luna2-utils> && <gate-venv>/bin/ruff check utils/lchroot utils/qemu_static tests && <gate-venv>/bin/ruff format --check … && <gate-venv>/bin/mypy utils/lchroot utils/qemu_static tests && <gate-venv>/bin/pytest -m "not integration"`
- **Integration (controller-only):** `LCHROOT_ITEST=1 ./.venv/bin/pytest -m integration --no-cov` (read-only containment checks). Destructive: add `LCHROOT_DESTRUCTIVE=1 -k destructive`; auto-clones `login` → throwaway, removes it after. Needs free disk + DNS.
- **Real tool:** `lchroot …` / `qemu-static <arch>` (dev launchers), or `cd <luna2-utils> && PYTHONPATH=. /trinity/local/python/bin/python3 -m utils.lchroot …`. Use **system python** for real runs (the `.venv` has a urllib3/requests skew — gates only; see JOURNAL.archive.md L10).

## Invariants you must NOT break (security-relevant → need an ADR)
- The sandbox hardening is **load-bearing**: keep `--cap-drop` (CAP_SYS_ADMIN rw / **ALL**
  ro), `--unshare-pid`, `--unshare-uts`, **`--die-with-parent`** (real teardown — L21/ADR
  0006), and the `--setenv TMPDIR /tmp` pin (L13/L23). bwrap as root keeps ALL caps by
  default — dropping them is what makes read-only/containment real (L6). Never mount host
  systemd/D-Bus into the sandbox.
- The whole bwrap argv is built by one pure function (`sandbox.py`) — golden + Hypothesis
  property tested. Subprocess only via the one `Executor`; never `shell=True`.
- ADRs in `decisions/` are **append-only** (supersede, never edit). Never log `luna.ini`
  credentials.

## Architecture decisions (index — full text in `decisions/`, rationale in JOURNAL.archive.md D-series)
- **0002/0003** lock admin `--status`/`--kill`; `--force` asks before killing a live holder.
- **0004** HA sync is NOT gated on session exit code (peers must stay byte-identical).
- **0005** refuse an ambiguous shared image path (two osimages → one rootfs).
- **0006** `--die-with-parent` for real teardown-on-exit.
- **0007** foreign-arch entry via static qemu-user + binfmt `F` (two-worlds model).
- **0008** `--path DIR` — enter a rootfs by directory, bypassing luna entirely.
- **0009** emulation owned by `qemu-static`; lchroot delegates; binfmt self-registered
  (idempotent, `F`), interpreter bundled in-package (not `/usr/bin`).
- **0010** daemon `osimage pack` builds the initramfs via `lchroot --path` (not
  mount+chroot); pack must NOT `--force` a locked image (surfaces lchroot's exit-9
  message); lchroot is now a hard runtime dep of pack.
- **0011** master-only rw gate (exit 10) + no HA image sync (supersedes 0004).
- **0012** `--unlock` replaces the `--kill`/`--force` pair (supersedes the CLI surface of
  0002/0003): one verb frees a lock — stale cleared silently, live confirmed on a tty and
  refused non-interactively; no in-line takeover. `--status` unchanged. *(Lock-clearing
  surface superseded by 0014; `--status` part still stands.)*
- **0014** `--force` replaces `--unlock` (supersedes 0012's lock-clearing surface): a flag
  on the **normal entry path** that breaks a *live* lock and **then proceeds** (enter on a
  tty, run the command otherwise). Plain (no `--force`): tty prompts, non-tty refuses (exit
  9). Stale always auto-reclaims; `--force` is a no-op without a live lock. Drops the
  free-without-enter capability. New: non-tty + no command → exit 7. `--status` unchanged.
- **0013** lchroot options may hug the osimage on either side (`lchroot img --ro` ==
  `lchroot --ro img`) via a parser-driven argv reorder that stops at the command word (no
  `-v` collision); `--` forces the boundary. `--list-images` hidden from `--help`.

## Next actions (priority order)

> **Updated 2026-06-02** with a fresh batch of TODOs from Alex (grouped below). The lchroot
> code is feature-complete; most of these are contract hardening, packaging/merge, clean-room
> verification, docs, and the team deck. **This chat's work:** (a) reshaped the `alex/` backup
> to clone-of-main + branch (see the backup-mirror section above); (b) built the team-briefing
> deck (see "presentation & documentation" below).

### lchroot — code & contract

> **Phase A done (2026-06-02, gates green: ruff/format/mypy --strict + pytest 142 passed,
> 95.09% cov).** Items 1–3 below are complete on the branch (not yet committed):
> - **#2 (codes)** audited every `raise`→code path; canonical table now in
>   `findings-lchroot.md`. Wired the previously-dead `SandboxError`: a missing/unexecutable
>   `bwrap` now raises it (clean "is bubblewrap installed?" → exit 1) instead of leaking a
>   raw `FileNotFoundError` traceback. Fixed the stale `__main__` module docstring (now lists
>   0/7/8/9/130/1). Added boundary tests (8→exit 8, SandboxError→1) + a launch test.
> - **#1 (pack-lock)** lchroot side: the `LockedError` message now names the image and points
>   at the remedy — `image 'cib' is locked (PID…); inspect with `lchroot --status cib` or take
>   it over with `lchroot --kill cib``. This is the lchroot-sourced message `osimage pack`
>   surfaces; the daemon must NOT `--force` (folds into the deferred daemon-pack ADR, #5).
> - **#3 (headers)** GPLv3 block on all 27 `.py` + `lchroot-legacy`. Decisions: copyright
>   **ClusterVision only, no year** (year drops the staleness problem; legally fine) + an
>   **`# Author: Alex Ninaber`** attribution line (glory, without a false copyright claim).
>   No metadata dunders (noise/stale, dup setup.py); shebang only on the two `__main__.py`
>   (coding cookie removed — ruff UP009). Block copied verbatim from luna2-utils.

1. **`luna osimage pack`: do NOT pass `--force` to a locked image.** Forcing pack on a
   locked image can corrupt the rpmdb (and other state). Pack runs lchroot non-interactively
   and **without** `--force`, so a live lock makes lchroot **fail with its own clear message**
   — "image `<image>` is held by a live session (PID…); … inspect with `lchroot --status
   <image>`, or pass --force to break the lock and run anyway" (ADR 0014) — and **that message
   comes FROM lchroot** (the daemon surfaces lchroot's error/return code, it doesn't invent
   its own, and never adds `--force` itself). Folds into the deferred daemon-pack ADR (#5).
2. **Review ALL lchroot exit/error codes — clear, complete, to the point.** Current map
   (`__main__.main`): `0` ok · `1` other (`LchrootError`) · `7` config / image-resolve / luna
   (`ConfigError`,`ImageResolveError`,`LunaError`) · `8` emulation (`EmulationError`) · `9`
   locked (`LockedError`) · `130` interrupt. Audit every raise→code path and every user-facing
   message for clarity + completeness (esp. the locked message → point at `--status`/`--force`;
   the pack-lock case in #1). Document the final table in `findings-lchroot.md` and a test.
3. **GPLv3 header on EVERY source file.** Add the standard **TrinityX GPLv3 license header +
   copyright** to every `.py` (and `utils/lchroot-legacy`). **Add `Alex Ninaber` to the
   copyright** alongside ClusterVision ("for glory"). Copy a canonical existing TrinityX/luna2
   header verbatim — match their exact format, don't invent one.

### packaging / merge

> **Phase C progress (2026-06-02).** #4 ✅ gates green throughout. #5 ✅ ADR 0010 written
> (daemon pack via `lchroot --path`; daemon already does NOT `--force` + surfaces lchroot's
> exit-9 message — code was already correct); `pack-via-lchroot` backed up on
> `alex/luna2-daemon`, clustervision push deferred to #9. #6 ✅ `lchroot-legacy` shipped
> (MANIFEST) + wired (`bash_runner:lchroot_legacy`) — FIXED a bug where it dropped CLI args
> (now forwards `sys.argv` as a list, no `shell=True`). #7 ✅ CI documented + FIXED a gap
> (ruff/mypy omitted `utils/qemu_static` — now covered). #8 ✅ bundled the aarch64-built
> `qemu-x86_64-static` (Fedora `qemu-9.2.4-2.fc42`, matches the existing interpreter's
> provenance). **Remaining: #13 docs scrub, then #9 open MRs.**

4. **Run the gates** after the header + error-code changes (ruff/format/mypy --strict/pytest);
   never merge red.
5. **Daemon pack fix → GitLab + its ADR** (proven live): push `pack-via-lchroot`, write the
   ADR, note the hard dep (lchroot must be installed before the daemon can call it). Include
   the pack-lock behaviour from #1.
6. **Is `lchroot-legacy` in the MR?** Confirm the renamed bash `lchroot-legacy` is actually
   included/shipped in the luna2-utils MR (and wired as a console script) when we open it.
7. **What does the GitLab CI/CD pipeline test?** Document + verify `.gitlab-ci.yml`'s `test`
   stage: exactly which gates run (ruff / format / mypy / pytest unit), on which branches/MRs,
   and that integration/destructive tests are correctly excluded (controller-only).
7b. **⚠ DISCUSS WITH DEV (CI/CD workflow) — Alex is "meh" on how this currently works; agree
    the model before/at the MR.** CI maintainer is **Sumit Sharma** (per `.gitlab-ci.yml`
    header). Reviewed 2026-06-04 — here is the *what / how / why* and the open questions:
    - **What we ship & where the tests live (settled, just confirm intent):** the unit +
      regression tests live in `tests/` in the repo and run in GitLab CI; they are **not**
      bundled into the deployed artifact (`setup.py packages=['utils','utils.lchroot',
      'utils.qemu_static']` + `MANIFEST.in` exclude `tests/`), so the `production`/
      `development` wheel that gets `scp`'d out carries code, not tests. Tests are never
      "moved" anywhere — they travel with `git push`. *Confirm this dev-gate-vs-runtime-artifact
      split is the intended contract.*
    - **How the gate runs:** `python:3.10` container, `pip install ruff mypy pytest pytest-cov
      hypothesis …`, then the same four commands as the local gate (`ruff check`,
      `ruff format --check`, `mypy`, `pytest -m "not integration"`). 177 unit tests, ~97% cov.
      Integration/destructive (`-m integration`, the bwrap/containment proofs) are **never**
      in CI by design — they need a real controller (root+bwrap+luna). So **CI-green ≠
      integration-verified.**
    - **Open Q1 — gate scope is narrow.** The `test` job's rules are `merge_request_event`
      OR the literal branch `lchroot-bwrap`. After merge, **direct pushes to `main`/
      `development` won't run the unit gate** (only MRs will). *Decide:* broaden to run on the
      default branch too (`- if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'`) — the CI comment
      already says "broaden once merged if desired." **Why it matters:** otherwise a hotfix
      pushed straight to main ships unlinted/untyped/untested.
    - **Open Q2 — unpinned tool versions.** CI `pip install`s ruff/mypy/pytest unpinned each
      run, so a new upstream release can fail CI on code that passed locally. *Decide:* pin
      versions (and ideally match the gate venv) for reproducibility, or accept the drift.
    - **Open Q3 — runner availability** on `clustervision/luna2-utils`. The gate only runs if
      a runner is registered/shared on that project; if none, the MR pipeline sits pending.
      *Verify with Sumit.*
    - **Open Q4 — integration cadence.** The E/I-series + `POKE_HOLES` only run manually on a
      controller. *Decide:* is there appetite for a controller-tagged runner (or a scheduled
      job) to run `-m integration` periodically, or do these stay manual+documented (current)?
    - **Open Q5 — target branch & the restructure.** On `origin/main`, `utils/lchroot` is
      still the **legacy bash blob**; this branch turns it into a package dir + `utils/
      lchroot-legacy` *and* introduces the whole `test` stage (main's CI has none). The MR is a
      sizeable restructure + the project's first tests/CI — *agree the target (`development`
      vs `main`) and give Sumit a heads-up* (ties into #9).
8. **`qemu-x86_64-static` (the Intel/x86 reverse interpreter) for ARM controllers** —
   bundle an **aarch64-built** `qemu-x86_64-static` so ARM RH controllers can enter/build x86
   images. Only the x86-built `qemu-aarch64-static` is bundled today. Per-host/per-target.
9. **Open the MRs** (luna2-utils → development; trinityx-combined → development); flag to the
   maintainer that luna2-utils gains tests/CI/methodology (it had none).
10. **Productionize:** install luna2-utils into the TrinityX python (replace the dev launchers),
    symlink `/usr/sbin/lchroot`; decide qemu distribution + binfmt persistence (JOURNAL.archive.md D26)
    + completion install path.

### clean-room / verification
11. **Clean-start check on a fresh TRX install.** Verify lchroot + qemu-static + binfmt +
    everything works on a **clean TrinityX installation** — nothing may rely on this box's
    hand-placed test mods (live binfmt registration, the `/usr/local/sbin/{lchroot,qemu-static}`
    launchers, the daemon site-packages edit). The real "works out of the box" test.
12. **Run `compute-arm-rocky.yml` end-to-end** (trinityx-combined `arm-image-build`) — bootstrap
    registers via `qemu-static`; needs `compute-arm` not lock-held; watch Phase B node roles for
    vendor-aarch64 gaps.
13. ✅ **DONE (2026-06-02) — Docs cleanup before the luna2-utils MR.** Shipping docs
    (`findings-lchroot.md`, `regression.md`, `AGENTS.md`, `adversarial/*`) fully scrubbed:
    `lbwrap`→`lchroot` (oracle-comparison lines reworded to `lchroot-legacy` vs `lchroot`),
    `/root` dev-tree paths → `<placeholders>`, cluster DNS IP / `controller1` →
    generic. The **archive `JOURNAL.archive.md` keeps `lbwrap` as historical fact** (D1 name, D27
    rename) under its disclaimer; only its paths/IP were genericized. The **`decisions/`
    ADRs were also retro-named `lbwrap`→`lchroot`** (Alex's call) — a one-time TERMINOLOGY
    migration (the product rename), NOT a decision change: every decision/rationale is
    byte-identical except the tool name, and oracle references were disambiguated to
    `lchroot-legacy` (genuine new-tool refs like 0007 "a live lchroot session" kept). This
    is an authorized, recorded exception to the append-only rule, which still stands for
    actual decisions (supersede, never edit). **Rule 4 (personal/identity content) also DONE
    (2026-06-02, Alex's call):** the push-identity + backup-mirror sections here and the
    historical push records in `JOURNAL.archive.md` were depersonalized — colleague identity, SSH
    key filenames, and the literal `core.sshCommand` removed, while keeping the operational
    lessons (pin the correct identity per-checkout; verify before pushing; feature-branch →
    MR; never push `development`/`main`; no backups → push often).

### presentation & documentation
14. **Team deck — improve it.** Lives at **`/root/deck/`** (NOT in the repo): `build_deck.py`
    (python-pptx) + `charts.py` (matplotlib) → `lchroot-team-briefing.pptx` (+ `.pdf`). Render:
    `soffice --headless --convert-to pdf --outdir preview <pptx>` then `pdftoppm -r 95 -png …`
    to review. TODOs from Alex:
    - **Explain bubblewrap** explicitly (what `bwrap` is, why it's the engine under lchroot).
    - **Less dumbed-down — teach more.** Add real specifics so people learn: how the
      root/**capability** shield actually works (root keeps all caps by default → we drop them;
      that's what makes read-only/containment real), and **what a kernel namespace is**
      (PID/UTS isolation explained plainly but truthfully). Keep KISS, add substance.
15. **MkDocs site for TrinityX documentation.** *(Updated 2026-06-02)* The TrinityX docs
    already exist as a **deployed MkDocs site** in GitLab `clustervision/trinityx-docs`
    (→ docs.clustervision.com; `main` = production, branch→preview→MR). A reusable
    **mkdocs contribution kit** was added to `engineering-standards/mkdocs/` (branch
    `add-mkdocs-kit`, MR pending). **Next:** rewrite the TrinityX docs to cover the new
    lchroot + the ARM/foreign-arch image build (planning with Alex; see #16/#17 register).

### New TODOs (2026-06-02, from Alex)

16. ✅ **DONE (2026-06-03) — Removed the HA-pair sync AND added a master-only rw gate.**
    Alex extended the ask: not just drop the no-op sync, but **gate** rw entry so you cannot
    build on the secondary at all. Done on `lchroot-bwrap` (gates green: ruff/format/mypy
    --strict, **pytest 146**, 95% cov): dropped `LunaClient.sync_image`, the `--no-sync`
    flag, and the master-exit sync call; added `SecondaryControllerError` → **exit 10** when
    `ha.enabled and not ha.master and not --ro` (NO override — Alex's call; `--ro` still
    allowed since it can't mutate; non-HA installs never gate; `--path` is luna-free so never
    gates). **ADR 0011** written (supersedes 0004; 0004 left intact, append-only). Exit-code
    table (`findings-lchroot.md`), comparison table, and `regression.md` §F updated; the deck
    "twin server synced"/`--no-sync` scrub is still pending (deck work, item #14).
17. **Split `qemu-static` (+ its CLI) out of luna2-utils into its own RPM.** Decision: move
    `utils/qemu_static/` and its CLI out of the main GitLab `luna2-utils` and package it as
    its **own RPM**. Likely **its own repo in ClusterVision GitLab** (created later) —
    tracking method still TBD. Keep lchroot's delegation to qemu-static intact (ADR 0009);
    update the runtime/packaging dependency accordingly once the split lands. In the
    TrinityX docs, keep qemu-static **low-profile** (no dedicated page — at most a brief
    mention inside the lchroot page that it can run foreign-arch ARM/Intel binaries).
18. **Review the lchroot command line (full CLI/UX pass).** Beyond the `--help`/examples
    polish already done — step back and review the whole CLI surface for ergonomics: flag
    names, defaults, positional contract, consistency, and what an operator actually reaches
    for. Are the flags well-named and minimal? Anything confusing or missing? Treat it as a
    deliberate UX review of the command, not just the help text.
    - **Lock-admin redesign DONE (2026-06-03, gates green: ruff/format/mypy --strict,
      pytest 148, 95.75% cov).** `--kill`/`--force` collapsed into a single **`--unlock`**
      (clear the lock + tear down the holder, don't enter; stale cleared silently, live
      confirmed on a tty and refused non-interactively — behaviour A, no `--force`).
      `--status` unchanged. ADR 0012; LockedError/pack message now points at `--unlock`;
      findings exit-code table + regression §C + POKE_HOLES updated; **fixed the stale
      bash completion** (dropped removed `--no-sync`, added `--status`/`--unlock`/`--path`/
      `--hostname`/`--no-emulate`, value-option skip for `--path`/`--hostname`/`--log-file`).
    - **Positional/`REMAINDER` sharp edge + `--list-images` DONE (2026-06-03, gates green:
      ruff/format/mypy --strict, pytest 152, 95.94% cov).** `_hoist_image_options` lets
      lchroot's own flags hug the osimage on either side (`lchroot img --ro` ==
      `lchroot --ro img`, `lchroot img --unlock` works) via a parser-driven argv reorder
      that **stops at the command word** so a command's `-v`/`--debug` are never eaten;
      `--` forces the boundary; `--path` + bare invocations unaffected. `--list-images`
      hidden from `--help` (`argparse.SUPPRESS`) — it only backs completion/scripting.
      ADR 0013. (Rejected `parse_known_args`: silently steals colliding command flags.)
    - **Still open in #18:** a general flag-name/defaults/consistency sweep over the rest
      of the surface. **Decided to DROP #19** (node→osimage selection) as not worth the
      complexity/ambiguity.
19. **Image selection by NODE name (auto-resolve node → its osimage).** Today you pass the
    *osimage* name. Idea: let the operator start typing a **node** name and have lchroot
    auto-select the osimage linked to that node — operators think in nodes, not images.
    **Open question (Alex): how would that even work?** Sketch: luna already maps node →
    group → osimage, so lchroot could take a `--node <name>` (or detect that the positional
    is a node, not an image) and resolve via the luna API to the linked osimage; tab-completion
    could offer node names too. Edge cases: a node with no/!1 image, group vs node-level image
    overrides, ambiguity between a name that's both a node and an image. Spike it.
20. **Offer to PACK on exit of an interactive rw session.** Nobody remembers the
    `luna osimage pack <image>` command. When an interactive, read-write lchroot session that
    actually mutated the image exits cleanly, **offer to pack** (e.g. prompt "image changed —
    pack it now? [y/N]", tty-only, default No, like the `--force` confirm). Consider gating on
    "did anything change" and on rw (never offer in `--ro`/`--path`-only/non-tty). This closes
    the loop operators forget. (Ties into the daemon pack path / ADR 0010.)

## TrinityX docs rewrite — plan & handoff (2026-06-02)

> **Drafts written 2026-06-04 (gitlab-trinityx-docs, NOT pushed — local commits only,
> `mkdocs --strict` clean, 0 warnings).** Pending Alex's review then MR into
> `clustervision/trinityx-docs` (colleague-reviewed; `main` = production).
> - **`alex-lchroot-rewrite`:** new `admin/lchroot.md` (full + braggy: bubblewrap intro,
>   `--status`/`--force` lock model per ADR 0014, options, `--ro`, non-interactive use, the
>   safety guarantees + the 7-fix legacy comparison table, foreign-arch, exit codes,
>   migration). `admin/utils.md` collapsed to a stub keeping the `#lchroot` anchor; nav
>   updated. **Decision-D pass done:** `admin/image.md` (link→lchroot.md, `lchroot(rw)`
>   prompt, safety note), `admin/luna.md` (pack reports a locked image, don't force),
>   `howtos/osimages.md` (refreshed dated `chroot` prompts). `requirements.md` had no
>   lchroot mention (skipped); `recipes/osgrab-rhel.md` already uses the non-interactive
>   form (kept). qemu-static stays obscure (decision B): only a brief line in lchroot.md.
> - **`alex-arm-image-docs`:** new `howtos/build-arm-image.md` (cross-arch build: prereqs,
>   `compute-arm-rocky.yml` + tags, what runs under the hood, verify via `lchroot --ro
>   --path … uname -m`, native-arch-boot caveat) + a "Building for a different architecture"
>   subsection woven into `install/anyimage.md` after the aarch64 matrix; nav updated.
> - **Cross-branch links** between the two new pages use site-root form
>   (`/admin/lchroot/`, `/howtos/build-arm-image/`) so each branch builds `--strict`-clean
>   alone; both resolve once both MRs land. **Two open MRs**, one per branch.
> - **For the reviewer:** the "braggy" claims trace to JOURNAL.archive "Cool Facts"; the
>   playbook path is assumed to be `~/trinityX/site` (matches `install/install.md`).

> **⚠ DISCUSS WITH DEV (docs CI/CD) — does trinityx-docs need a staging/preview + a real
> build gate?** Reviewed `clustervision/trinityx-docs/.gitlab-ci.yml` 2026-06-04. Maintainer
> unclear — the build job uses image `vanmeijel/mkdocs` (likely van Meijel, *not* Sumit who
> owns the luna2-utils CI); confirm who owns it. The *what / how / why*:
> - **What exists today (two stages, neither is a real gate or a preview):**
>   - `build`: runs `mkdocs build` **without `--strict`**, and `only: - build` — i.e. only on
>     a branch literally named `build`. It does **not** run on MRs or on feature branches, and
>     publishes nothing.
>   - `production`: `only: - main` → `mkdocs build` (**also non-strict**) → `scp site.tgz` to
>     the docs host. **Merge to `main` = publish to docs.clustervision.com (~10 min).**
> - **Gap 1 — no enforced build gate.** `mkdocs build --strict` (dead links, missing nav,
>   bad anchors) is a **manual, author-side** step today; CI never runs it on an MR, and even
>   the production deploy is non-strict — so a broken link/nav **can reach production**. (I ran
>   `--strict` by hand on both branches: 0 warnings. But nothing enforces that for the next
>   contributor.)
> - **Gap 2 — no staging/preview.** No GitLab Pages, review-app, or environment. A reviewer
>   cannot open a rendered view of a branch; the only preview is **local `mkdocs serve`**.
>   So review = read the Markdown diff, or pull + serve locally.
> - **Why it matters:** `main` is production with no human-clicked staging step — the merge
>   *is* the deploy. With neither a CI strict-gate nor a preview, correctness rests entirely on
>   the author remembering to run `--strict` and the reviewer reading raw Markdown.
> - **Proposal (own MR, `.gitlab-ci.yml` infra — do NOT slip into a content MR):**
>   (a) an **MR-triggered `mkdocs build --strict`** job (turn the strict build into an actual
>   gate, matching the kit's DOC-BUILD-001 claim that "the strict build is the review gate");
>   and optionally (b) a **per-branch preview** (GitLab Pages / review app) giving reviewers a
>   real URL. Both can be drafted on request.

**Goal:** rewrite/extend the **deployed** TrinityX docs (GitLab `clustervision/trinityx-docs`,
MkDocs → docs.clustervision.com) to cover (1) the **new lchroot** and (2) **ARM/foreign-arch
image building**. Follow the mkdocs contribution kit in `engineering-standards/mkdocs/`
(entry token `CONTEXT-LOADED-TRINITYX-DOCS`): branch → `mkdocs serve` preview →
`mkdocs build -f trinityX/mkdocs.yml --strict` → MR. Callouts use the HTML note divs
(`green/blue/yellow-note`), **not** `!!!` (admonition ext is disabled). **`main` = production
(auto-publishes ~10 min) → NEVER edit or push `main`.**

**Repos / branches (set up 2026-06-02):**
- Local clone `/root/git/gitlab-trinityx-docs` (origin = clustervision; `alex` remote =
  `alex/trinityx-docs` mirror; push identity pinned to the alex GitLab key via core.sshCommand).
- alex mirror created (clone-of-main + feature branches): `main`, **`alex-lchroot-rewrite`**,
  **`alex-arm-image-docs`** (branches currently empty == main).
- **Multi-branch flow:** lchroot work → `alex-lchroot-rewrite`; ARM work → `alex-arm-image-docs`.
  Push to alex for Alex's review. **Authoritative landing = MR into `clustervision/trinityx-docs`
  `main`, reviewed by a colleague** (it's a clustervision repo; the alex mirror is review/backup only).

**Decisions (Alex, 2026-06-02):**
- **A — lchroot page = full + braggy.** NEW `admin/lchroot.md`, promoted out of `admin/utils.md`
  (leave a 1-line stub there + keep the `utils.md#lchroot` anchor working so `admin/image.md`
  links don't break). Tagline *"chroot with super powers."* Written in the deck's engaging voice
  but technically real: capability dropping (incl. CAP_SYS_ADMIN); PID/UTS **namespaces** + the
  guardrailing they give; `/proc`·`/dev`·`/sys` handling; real read-only; die-with-parent teardown;
  the lock model (`--status` to inspect, `--force` to break a live lock and proceed; ADR 0014);
  `--ro`/`--dry-run`/`--path`; non-interactive use;
  the exit-code table. "Everything that makes it beautiful." **Primary source = `JOURNAL.archive.md`
  "Cool Facts" (L39–164)** — the pitch (every claim backed by an L*/D*/adversarial result) +
  the **lchroot-vs-lchroot-legacy comparison table (L99–107, the 7 fixes)**; reuse that table in
  the page. Include a short **"what happened to the old tool"** note: the bash original still ships
  as **`lchroot-legacy`** (a console script — the behavioural oracle / fallback), while `lchroot`
  is now the bubblewrap tool. Headline claims to land: zero leaked daemons (`--unshare-pid` +
  `--die-with-parent`); image can't touch the controller; real read-only (root keeps all 41 caps →
  we drop them, the load-bearing part); no mount-cleanup to get wrong even on `kill -9`; **14/14**
  live escapes denied; fixes the no-tty bug (usable from cron/CI); foreign-arch extension.
- **B — qemu-static stays obscure.** No dedicated page; at most a brief line in lchroot.md that it
  runs foreign-arch (ARM/Intel) binaries (ties to TODO #17).
- **C — ARM = hybrid (I assessed the existing image docs: adequate, do NOT rewrite).**
  `install/anyimage.md` is good and ALREADY has an aarch64 tested matrix (cross-*distro*). The gap is
  cross-*arch* (build aarch64 ON an x86 controller). So: (i) weave a "Building for a different
  architecture" subsection into `anyimage.md` by the aarch64 matrix; (ii) add a task howto
  `howtos/build-arm-image.md` (prereqs, qemu-static+binfmt, the `foreign-bootstrap` path,
  `compute-arm-rocky.yml`, boots under emulation), cross-linked. `howtos/osimages.md` is dated
  (el7/yum/old `chroot [root@compute /]$` prompt) → minor refresh only.
- **D — cross-link/refresh:** `image.md` (lchroot links + new `lchroot(rw)` prompt + a safety note),
  `luna.md` (pack must surface lchroot's exit-9 on a locked image, must NOT `--force`),
  `recipes/osgrab-rhel.md` (validate non-interactive `lchroot $IMAGE <cmd>` — it still works),
  `install/requirements.md`.
- **D (lchroot-legacy) — don't forget the rename.** Old bash `lchroot` → **`lchroot-legacy`**
  (still shipped as a console script: the oracle/fallback); new `lchroot` = bubblewrap. Any page
  that says "lchroot" meaning the *old* behaviour (`admin/utils.md`, `admin/image.md`'s
  `chroot [root@compute /]$` prompt, `howtos/osimages.md`) is updated to the new tool; the migration
  note + comparison table live in `admin/lchroot.md`. `recipes/osgrab-rhel.md`'s non-interactive
  `lchroot $IMAGE <cmd>` is already the *new* behaviour (works now) — keep it.
- **E — release notes:** `general/releases.md` — new lchroot + ARM build status.

**Page-by-page worklist** (branch in parentheses):
- NEW `admin/lchroot.md` (alex-lchroot-rewrite); EDIT `admin/utils.md` stub→lchroot.md (same branch).
- EDIT `admin/image.md`, `howtos/osimages.md`, `admin/luna.md`; validate `recipes/osgrab-rhel.md`
  (alex-lchroot-rewrite).
- NEW `howtos/build-arm-image.md` + EDIT `install/anyimage.md` cross-arch subsection
  (alex-arm-image-docs).
- EDIT `general/releases.md` (split per topic across both branches).
- nav: register new pages in `trinityX/mkdocs.yml`; add to `trinityX/pdf.yml` only if manual-grade
  (lchroot ref likely YES; ARM howto likely NO — confirm).

**Sources of truth:** `docs/lchroot/JOURNAL.archive.md` **"Cool Facts" (L39–164)** — the pitch + the
lchroot-vs-lchroot-legacy table; the **primary braggy source for A**; `docs/lchroot/findings-lchroot.md`
(oracle + canonical exit-code table), ADRs `0002–0010`, the team deck at `/root/deck/`
(plain-language bubblewrap/caps/namespaces),
trinityx-combined `arm-image-build` branch (`compute-arm-rocky.yml` + `foreign-bootstrap`/
`dracut-sysroot` roles + their variables).

**Open questions (Alex closed the chat before answering — resolve next session):**
- ARM status in the docs: **GA or still "Beta"?** (`releases.md` says Beta → default to hedged/Beta
  wording until told otherwise.)
- `pdf.yml` inclusion for the lchroot page (manual-grade?) — likely yes; ARM howto likely no.
- Base branch = **`main`** (used as the base for both feature branches; confirmed by Alex).

**Cross-cutting:** TODO #16 (remove HA-pair sync) means the new lchroot docs must NOT describe a
`--no-sync`/twin-server-sync feature — keep it out of `admin/lchroot.md` until #16 lands, and scrub
the "twin server synced"/`--no-sync` line from the deck too.

## Explicitly OUT OF SCOPE (KISS — do NOT build)
Resource/cgroup limits / fork-bomb containment. chroot-class tools behave ~99% of the
time; external `systemd-run --scope -p MemoryMax= -p TasksMax=` is the answer if a real
incident ever occurs. A decision, not a gap (JOURNAL.archive.md L15).
