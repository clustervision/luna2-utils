# 7. Foreign-architecture image entry via the host's qemu-user / binfmt_misc

## Status
**Proposed (design agreed 2026-05-31; implementation deferred to the next phase).**
This ADR records the *design and intentions* worked out in discussion. No `emulation.py`
exists yet. Supersedes nothing. (Note: an earlier spec mis-numbered this "0004" — that
slot is taken; foreign-arch is **0007**.)

## Context — what we're actually trying to do
Operators run x86_64 TrinityX controllers but increasingly need **ARM (aarch64)** compute
images. Three connected goals, in priority order:

1. **Edit/customise a foreign-arch osimage** — `lchroot <arm-image>` should transparently
   run the image's ARM userspace so an operator can `dnf install …`, edit configs, etc.
   *This is the phase the spec we read covers.* Userspace emulation for editing — **not a
   VM/boot.**
2. **Fix `luna osimage pack`** so it (a) stops racing concurrent edit sessions and (b) can
   pack foreign-arch images.
3. **Ultimate goal: an ansible playbook that *creates* an ARM image on an x86 host.**

The enabling mechanism for all three is one host capability: **`qemu-user` + `binfmt_misc`**.

## The mental model that drives every decision — "two worlds"
- **Host world = x86_64**: the controller, its kernel, its libraries.
- **Image world = aarch64**: the rootfs being edited/built.
- **`qemu-aarch64` is a citizen of the *host* world** (it is an x86_64 program) that reads
  ARM instructions and translates them to x86, issuing real syscalls to the **host
  kernel**.

Consequences that fall out of this and are load-bearing for the design:
- **Why `qemu-*-static` (not dynamic).** When an ARM binary runs inside the sandbox, the
  mount root *is* the ARM rootfs. A dynamic `qemu-aarch64` would need its *x86* loader
  (`/lib64/ld-linux-x86-64.so.2`) and libs, which aren't in the ARM rootfs (only ARM libs
  are). A **static** qemu carries no luggage and works regardless of which rootfs is
  mounted. (The "bundle the dynamic qemu + its libs + an `LD_LIBRARY_PATH` wrapper" idea
  was explored and **rejected**: the wrapper's `#!` shebang resolves to the *guest* shell
  → recursion; and the ELF `INTERP` path resolves in the guest ns before `LD_LIBRARY_PATH`
  matters. A static binary *is* "qemu + its libs pre-bundled," done by the linker.)
- **Why the `F` (fix-binary) binfmt flag.** With `F` the kernel opens the interpreter fd at
  *registration* time (host ns) and reuses it, so foreign ELFs execute across bubblewrap's
  mount/PID namespaces with **no emulator needed inside the image**. Without `F`, the
  interpreter must be bound into the sandbox read-only (the fallback the spec handles).
- **fakeuname caveat.** Under qemu-user, syscalls reach the **host** kernel; `FAKE_KERN`/
  `LD_PRELOAD` only spoof the `uname` *string*. Anything that branches on a genuine foreign
  *kernel* feature needs a real VM — explicitly out of scope.

## Decision — lchroot's foreign-arch entry (the spec, condensed)
Detection is **host-side** (like the lock), the sandbox argv stays **pure**:
- New `src/lchroot/emulation.py` (pure stdlib, Linux-only): `host_arch()`,
  `detect_image_arch(rootfs)` — **the sole arch source: ELF-sniff `e_machine` of a probe
  binary in the image** (luna reports no arch); `read_handler()` parsing
  `/proc/sys/fs/binfmt_misc/<name>`; `plan_emulation()` returning an `EmulationPlan`
  (`needed`, optional handler, optional interpreter bind).
- **Auto-detect, no enable-flag** (operator's explicit ask + matches L18): emulation
  engages on a detected arch mismatch. Only opt-*out* is `--no-emulate` (assert same-arch).
- Missing/disabled handler ⇒ raise `EmulationError` → **new exit code 8** ("this host
  cannot emulate the image's architecture"), distinct from 7/9. Handler with `F` ⇒ no bind;
  without `F` ⇒ `--ro-bind` the interpreter.
- `build_bwrap_argv` gains optional `emulator_bind`; cap-drop **unchanged**.
- **NEVER auto-register `binfmt_misc`.** That is global, privileged host kernel state and a
  surprise; detect-and-require only. Registration is an explicit operator step (or a future
  `--register-binfmt` behind its own ADR, default off). *(The auto-mode safety classifier
  independently enforced this during setup — good signal.)*

Full step-by-step spec lives in the task block the operator pasted; tests-first, ADR per
behaviour, `mypy` over tests too.

## Host prerequisite — and its current state on this controller
qemu-user-static is **not packaged on EL9** (RHEL ships only `qemu-kvm` = x86 *system*
virt; EPEL9 dropped `qemu-user-static`; `qemu-kvm` does **not** include user-mode
emulators). Upstream QEMU and Fedora's `qemu-user-static` are very much alive (this box has
`qemu-kvm` 10.1.0) — it's purely a downstream packaging gap. Ways to get the static binary,
ranked: **(1)** extract one file from Fedora's `qemu-user-static-<arch>` rpm
(`rpm2cpio | cpio`) — cleanest, no repo/no container; **(2)** GhettoForge `gf.el9` rpm
(true `dnf install`, but a 3rd-party repo on the controller); **avoid** docker
`--privileged` (heavy) and from-source `--static` (EL9 lacks the static `-devel` libs).

**Already done on controller1 (2026-05-31, operator-authorised, live-verified):**
`/usr/bin/qemu-aarch64-static` (QEMU 9.2.4, from the Fedora rpm) installed; `binfmt_misc`
`qemu-aarch64` handler registered with `flags: F`. Proven end-to-end: a static aarch64
busybox ran transparently on the x86 host and `uname -m` → `aarch64`. Caveats: **live-only**
(no `/etc/binfmt.d` file → cleared on reboot), **aarch64-only** (arm/riscv64/… each need the
same two steps), reversible (`echo -1 > …/qemu-aarch64`; `rm` the binary). **No ARM osimage
exists yet** (all four luna images are x86_64), so the first real `lchroot <arm-image>` test
needs a small aarch64 rootfs first.

## Forward architecture — lchroot as the shared primitive (goals 2 & 3)
The convergent design: lchroot becomes the one sanctioned **"enter a (possibly foreign)
rootfs and run a command, under a lock"** primitive that both `pack` and image-creation
call. Findings backing this (verified against the luna + TrinityX source this session):

- **`luna osimage pack` (goal 2).** Its plugin does `mount dev/proc/sys into <image>` →
  `os.chroot` → `dracut` (build initramfs) → `tar` the rootfs — the exact unsafe chroot
  pattern lchroot replaces. **It checks no lchroot edit lock** (only luna-DB relations
  + its own housekeeper queue), so a concurrent edit session is a real hazard: pack's
  `umount <image>/proc` can yank a live lchroot session's mounts, and tarring a
  being-edited rootfs yields a torn image. **Fix direction:** route pack's dracut step
  through `lchroot <image> dracut …` (namespace safety + lock interlock) — and the *same*
  change makes **foreign-arch packing work**, because a plain chroot can't run an ARM
  `dracut` on x86 but lchroot+qemu can. Caveats to validate: does `dracut` work with
  `CAP_SYS_ADMIN` dropped (likely yes); pack needs the kernel/initrd **output** copied out
  → either dracut writes into the image and luna copies host-side, or add an lchroot
  `--output-bind`. This is a **luna-repo change with its own ADR**, not lchroot's job.

- **TrinityX `image-create` ansible role (goal 3).** Already has `x64`/`aa64` var splits,
  but those are only *package lists*; the build is host-arch-locked and there's **no
  qemu/binfmt anywhere** (so aa64 is currently built on aa64 hardware). Two gaps:
  - **Configure phase** runs in-image commands via raw `chroot {{ image_path }} <cmd>`
    (ubuntu/opensuse tasks). **Swap → lchroot**: gains foreign-arch (auto-qemu) *and* fixes
    the unsafe chroot. This is the easy, high-value, "adjust the playbook" part. (Ansible's
    `community.general.chroot` connection + binfmt is the *native* alternative for this, but
    it has no hardening/lock — lchroot is the safer version. `buildah`/`libvirt_qemu` are
    other paradigms; out of scope.)
  - **Bootstrap phase** (lay down the first foreign rootfs) is **not** lchroot (no rootfs to
    enter yet): needs `debootstrap --arch=arm64 --foreign`/`mmdebstrap`, `dnf --installroot
    --forcearch=aarch64`, `zypper --root` + host qemu/binfmt. Tool-driven, no ansible
    plugin does it. (`infra.osbuild` is an all-in-one alternative that *replaces* the model.)
  - **Integration gap:** lchroot resolves images **by luna name** (ADR 0005), but creation is
    **path-centric** and may precede luna registration ⇒ lchroot likely needs a direct
    **`--path/--rootfs` mode** that bypasses luna resolution. Capture as a sub-decision when
    we build it.

  So cross-arch creation = *(host-side cross-arch bootstrap)* + *(configure via lchroot)* +
  *(pack via lchroot)*, all standing on host qemu/binfmt.

## Out of scope (KISS)
Booting, kernels as VMs, `-machine`, disk images, full-system emulation. Resource limits.
Auto-registering binfmt. This is userspace emulation for *editing/building* an osimage.

## Considered & rejected
- **Dynamic qemu + lib dir + `LD_LIBRARY_PATH` "qemu-static" shell wrapper** — shebang
  recursion in the guest ns + `INTERP` resolves before `LD_LIBRARY_PATH`; more files +
  bind-mounts than one static binary. Rejected.
- **docker `multiarch/qemu-user-static --privileged`** — works but heavy/privileged for
  what is "copy one static file + one binfmt line." Rejected for routine use.
- **From-source `--static` build on the controller** — EL9 lacks static `-devel` libs;
  yak-shave on a prod box. Rejected; use a prebuilt static binary.
- **A flag to *enable* emulation** — rejected; auto-detect is the default, `--no-emulate`
  is the only (opt-out) flag.
