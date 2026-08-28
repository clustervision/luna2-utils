# 9. Foreign-arch emulation lives in a dedicated `qemu-static` tool

Date: 2026-06-01

Status: accepted (amends [0007](0007-foreign-arch-entry-via-qemu-user.md))

## Context

ADR 0007 established foreign-arch entry via host `qemu-user` + `binfmt_misc` (the `F`
fix-binary flag, so the interpreter survives bwrap's mount namespace). It said lchroot would
**never auto-register binfmt** and assumed the static qemu interpreter lived on the host OS
tree (`/usr/bin/qemu-<arch>-static`).

Operating that way surfaced several issues:

1. **Distribution.** `qemu-user-static` is in no EL9/EPEL repo; the interpreter was a
   hand-placed file and the binfmt registration was live-only (lost on reboot).
2. **A systemd unit / `/etc/binfmt.d` would exist only to survive reboots** — but a foreign
   binary only runs when something enters/uses the image; persistence is not the real need.
3. **The `F` flag opens the interpreter fd at registration time**, so the registration path
   only needs validity at that instant — it need not be on the OS tree at all.
4. **Emulation is a shared concern, not an lchroot feature.** The foreign-bootstrap
   `dnf --installroot --forcearch` step runs package scriptlets under the *system* binfmt
   handler, entirely outside lchroot. Baking qemu/binfmt into lchroot (and adding a
   `lchroot --setup-emulation` flag) bloated lchroot past its job ("enter a rootfs safely")
   and made the non-lchroot caller depend on lchroot for something unrelated to chrooting.

## Decision

Foreign-arch *capability* lives in a dedicated package/tool, **`utils.qemu_static`** (CLI:
`qemu-static`), independent of lchroot:

- It **bundles** the static `qemu-<arch>-static` interpreters **inside the package**
  (`utils/qemu_static/`), not in `/usr/bin`. The `F` flag makes the in-package path work
  even inside bwrap's namespace.
- It **registers the binfmt handler, idempotently** (no systemd unit, no `/etc/binfmt.d`;
  re-registers if missing). This **reverses 0007's "never auto-register"**: registration is
  scoped to the handler needed, pointing at the bundled binary.
- The **`qemu-static ARCH`** / **`qemu-static --image DIR`** CLI is the entry point for
  callers that run foreign binaries *outside* lchroot — notably foreign-bootstrap.
- **lchroot delegates**: on foreign entry it ELF-sniffs the image arch and calls
  `qemu_static.ensure_for_arch` (or, under `--no-emulate`, refuses with exit code 8). lchroot
  carries no qemu binary and no binfmt code — just a thin adapter that translates
  `qemu_static.QemuError` into its `EmulationError`. There is **no `lchroot --setup-emulation`**.

The interpreter is **per-host AND per-target** (no single binary does both directions); only
the x86-built `qemu-aarch64-static` is bundled today. ARM controllers will need an
aarch64-built `qemu-x86_64-static` added; carrying both host variants or arch-specific
packages is a later packaging decision.

## Consequences

- lchroot stays focused on sandboxing; `qemu-static` is the single owner of qemu+binfmt,
  used symmetrically by lchroot (library) and by the bootstrap (CLI).
- A fresh controller is foreign-arch-capable after `luna2-utils` installs, with no manual
  qemu install, no `/etc/binfmt.d`, no reboot-persistence concern — at the cost of a
  privileged binfmt write the first time emulation is needed (root, available).
- The ~7 MB qemu binary ships in the package (GPLv2 source-availability obligation rides with
  luna2-utils distribution).
- Verified live (2026-06-01) on an x86 controller: `qemu-static aarch64` registers the handler
  (pointing into the package); `lchroot --path <arm>` delegates and runs aarch64 `uname`;
  `--no-emulate` → exit 8. 19 unit tests across `test_qemu_static` + `test_emulation`; gates
  green (ruff/mypy --strict/pytest 139 passed, 94.8%).
