# AGENTS.md — read this before you touch the `lchroot` package

> Canonical instructions for AI coding agents working on **lchroot** (the
> bubblewrap-based tool in `utils/lchroot/`). This file is the project-specific
> instantiation of the shared **engineering-standards** Python kit — read that kit for
> the full rule registry, libraries, patterns, playbooks, and rationale:
>
> - Shared kit (generic): the **`engineering-standards`** repo, `python/` directory
>   (locally `<engineering-standards>/python/`) — start at its `AGENTS.md`,
>   full rationale in `STANDARD.md`, rules in `RULES.md`.
> - This file stays thin and carries only what is specific to `lchroot`.

**CONTEXT-LOADED-LCHROOT** ← if you did not emit this token when asked whether you
read this file, you have not loaded it. Load it.

## What this project is
`lchroot` is a bubblewrap-based, security-hardened replacement for the legacy bash
`lchroot` (now `lchroot-legacy`): it enters an OS image (e.g.
`/trinity/images/compute-image`) as a sandbox so an operator can customise it (install
packages, edit config) without endangering the controller. It ships inside
`luna2-utils` as `utils/lchroot/`. Foreign-arch entry is delegated to the sibling
`qemu-static` tool (`utils/qemu_static/`). Behavioural oracle: `findings-lchroot.md`.
Project log + decisions: `JOURNAL.md` and `decisions/` (this directory).

## The two premises (everything follows from these)
1. **Code must survive 7–10 years across Python versions and OSes (RHEL/Rocky,
   Ubuntu, SUSE, …).** Minimise what can rot: stdlib first, few deps, no cleverness.
2. **Code is maintained by agents, not read line-by-line by humans.** The automated
   gates are the review; types + docstrings + tests + ADRs are the durable truth.

## Tech stack & environment (do not deviate without an ADR)
- Python **3.10**, TrinityX interpreter `/trinity/local/python/bin/python3` (NOT host
  `/usr/bin/python3`, which is 3.9). `requires-python = ">=3.10"`.
- Lint+format **Ruff** (line 88) · types **mypy --strict** · tests **pytest +
  Hypothesis** · security **bandit + pip-audit**.
- Runtime deps: **stdlib + `requests` + `pyjwt` only** (already in the luna2-utils
  runtime). Adding any new runtime dep requires the `add-dependency` playbook + an ADR.

## Commands (copy-pasteable)
```bash
# canonical dev tree: <luna2-utils> (branch lchroot-bwrap); gate toolchain in <gate-venv>
cd <luna2-utils>
<gate-venv>/bin/ruff check utils/lchroot utils/qemu_static tests
<gate-venv>/bin/ruff format --check utils/lchroot utils/qemu_static tests
<gate-venv>/bin/mypy utils/lchroot utils/qemu_static tests
<gate-venv>/bin/pytest -m "not integration"        # unit/golden/property
LCHROOT_ITEST=1 ./.venv/bin/pytest -m integration --no-cov  # ONLY on a controller
```
A change is **not done** until `ruff`, `mypy`, and `pytest` are green. Never merge red.

## Hard rules (the few non-negotiables — full set in the shared kit's RULES.md)
- Fully type-annotate everything; `mypy --strict` must pass. (PY-CORE-003)
- Route every subprocess through the one executor module; never `shell=True` with
  interpolated input. (PY-CORE-005)
- No bare `except:`/`except Exception:` (except a top-level boundary); chain with
  `from`. (PY-ERR-001/004)
- Every state-mutating op is re-entrant + dry-runnable; check before acting; never
  silently force-destroy. (PY-CORE-004)
- No `sys.exit` outside `main`/`__main__`; library code raises. (PY-CORE-006)
- `encoding="utf-8"` on every file/text op; use `pathlib`. (PY-PORT-001/003)
- **lchroot-specific security invariant:** **YOU MUST NOT** weaken the sandbox (remove
  `--cap-drop`, `--unshare-pid`, `--die-with-parent`, or mount host systemd/D-Bus
  sockets) — this tool runs as root and mutates node images. Any change here needs a
  security ADR. (JOURNAL.md L6/L21; `decisions/0006`)

## What NOT to touch
- The legacy bash `lchroot-legacy` (the oracle) — keep it authoritative.
- `decisions/` ADRs are **append-only** — supersede, never edit.
- Generated/vendored files; the live luna API credentials in `luna.ini` (never log them).

## When you learn something / get corrected
Follow the shared kit's `RULE_EVOLUTION.md`: a mistake seen once → log it; seen twice →
propose a rule with its enforcement check. **The rules are meant to be improved.**

## Map
- This directory (`docs/lchroot/`): `AGENTS.md` (this file), `JOURNAL.md` (project log),
  `findings-lchroot.md` (behavioural oracle), `regression.md`, `decisions/` (ADRs 0002+),
  `adversarial/`.
- Shared generic kit: `engineering-standards/python/` — `RULES.md`, `LIBRARIES.md`,
  `patterns/`, `playbooks/`, `templates/`, `STANDARD.md`, `decisions/0001` (ADR template).
