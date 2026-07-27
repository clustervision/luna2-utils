# 13. lchroot options may appear before OR directly after the osimage

## Status
Accepted — 2026-06-03

## Context
`command` is an `argparse.REMAINDER` positional so a command's own flags pass through
verbatim: `lchroot img dnf -y install vim` must give `-y` to dnf, not to lchroot. The
side effect is that REMAINDER also swallows lchroot's *own* flags written after the image
— `lchroot img --ro` parsed `--ro` as a program to run, leaving `ro=False`, and
`lchroot img --unlock` did **not** unlock. Only `lchroot --ro img` / `lchroot --unlock img`
worked. The operator (reviewing the CLI, TODO #18) found this surprising: they expected
both orders to work, since for admin flags (`--status`/`--unlock`) there is no command at
all.

Two naive fixes were rejected:
- **`parse_known_args`** makes both orders work and preserves command order, but is *too
  greedy*: it pulls a recognised flag out of the command no matter where it sits, so
  `lchroot img tar -v` would have lchroot eat tar's `-v` as `--verbose` — silently. For an
  image-mutating tool, silently dropping a command's flag is the worst failure mode.
- **Documenting "flags before the image" (ssh/sudo convention) and erroring otherwise**
  keeps REMAINDER bulletproof but does not give the operator the behaviour they asked for.

## Decision
A small, parser-driven reorder (`__main__._hoist_image_options`) runs before
`parse_args`: it moves the recognised lchroot options that sit *between the osimage and
the first command word* to the front, then hands the result to the unchanged REMAINDER
parser. The rule the operator sees:

> lchroot's own options may hug the image — before **or** directly after it. The command
> is the first real word onward, and its flags (`-v`, `--debug`, anything) are never
> touched. An explicit `--` forces the command boundary and disables the reorder.

Consequences of the rule:
- `lchroot img --ro` == `lchroot --ro img`; `lchroot img --unlock` works.
- `lchroot img tar -v` leaves `-v` with tar (the command word `tar` stops the hoist), so
  **collisions are impossible** — the key advantage over `parse_known_args`.
- `lchroot --path /dir dnf x` is unchanged: the first bare word after `--path`'s value is
  the command head (folded in by `_resolve_target`), and the hoist stops there.
- `--` is honoured: `lchroot img -- --ro` runs a program `--ro` in the image.

The option/value tables are read off the live parser (`parser._actions`) so they can never
drift from the real flag set when flags are added or removed. `_actions` is private but
stable across CPython versions and is only read, never mutated.

Separately, `--list-images` is hidden from `--help` (`argparse.SUPPRESS`): it exists only
to back shell completion (`completions/lchroot.bash` calls it per TAB) and scripting, not
as an operator-facing flag. It still works when typed.

## Consequences
- The common slip (`lchroot img --ro`) now does what the operator means, with no new flag
  and no change to the REMAINDER pass-through contract.
- One extra pre-parse pass over argv; pure, list-in/list-out, unit-tested
  (hoist / collision / `--` boundary / `--path` / bare invocations).
- A genuinely ambiguous case is still resolvable: `--` lets the operator force a flag to
  be part of the command.

## Considered alternatives
- **`parse_known_args`** — rejected: silently steals colliding command flags (`-v`).
- **ssh-style "flags before the target only", with a helpful error on a misplaced flag** —
  rejected: errors where the operator expected it to just work; the reorder is barely more
  code and is strictly friendlier.
- **A custom `--` requirement for all commands** — rejected: breaks the
  `lchroot img dnf …` muscle memory the REMAINDER positional was chosen to preserve.
