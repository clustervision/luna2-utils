# 11. Read-write entry is gated to the master controller; no HA image sync

## Status
Accepted — 2026-06-03. **Supersedes ADR 0004** (HA sync on master exit). 0004 is left
intact (append-only); its decision is replaced by this one.

## Context
Two facts about the old HA behaviour drove this change:

1. **The sync was a no-op in the oracle.** The bash `lchroot-legacy`'s "sync to the twin
   server" path existed but never actually performed a sync (confirmed against the
   oracle). The new tool faithfully mirrored it (D7, ADR 0004) with a best-effort
   `/ha/syncimage/<image>` on master rw-exit plus a `--no-sync` opt-out. Since the
   behaviour it mirrored did nothing, carrying it forward added a luna call, a flag, and
   an ADR for no real effect. TrinityX keeps the controllers' images consistent by other
   means; lchroot's job is to *enter* a sandbox, not to replicate images.

2. **Nothing stopped a build on the secondary.** On a non-master controller lchroot only
   *warned* ("changes will be LOST after the next sync") and then entered read-write
   anyway. An operator could customise an image on the standby controller — exactly the
   footgun that diverges the pair — and the only guard was a log line premised on a sync
   that did not happen.

Not all installations are HA: a standalone controller has no master/secondary distinction
and must be unaffected.

## Decision
- **Remove the HA image sync entirely.** Drop `LunaClient.sync_image`, the `--no-sync`
  flag, and the master rw-exit sync call. lchroot performs no cross-controller
  replication.
- **Gate read-write entry to the master.** When HA is **enabled** and this host is **not
  the master**, a read-write entry is refused with `SecondaryControllerError` → exit
  code **10**. There is **no override flag** — mutating an image on the secondary is
  never the right thing; do it on the master.
- **Scope of the gate:**
  - Only when HA is enabled (`ha.enabled`). Standalone installs never gate.
  - Only for a mutating (rw) entry. `--ro` cannot change the image and is allowed on
    either controller, so inspection/debugging on the secondary still works.
  - Never in `--path` mode (luna-free, `client is None`): there is no luna to ask for HA
    state, and `--path` is for path-centric callers (pack / image-create) by design.

The HA state (`ha.enabled`, `ha.master`) still comes from luna's `/ha/state`
(`LunaClient.ha_state`); only the *response* to "not master" changed from warn-then-enter
to refuse.

## Consequences
- Image customisation can only mutate on the authoritative (master) controller, so the
  pair cannot diverge through lchroot.
- A new exit code **10** ("refused on a non-master HA controller") joins the contract
  (`findings-lchroot.md`); callers/scripts and the daemon pack path can distinguish it
  from locked (9) and bad-input (7).
- `--no-sync` is gone. No caller should pass it (it was only ever suppressing a no-op);
  the integration harness and any automation drop the flag.
- Pinned by `tests/test_main.py` (`test_non_master_rw_is_refused`,
  `test_non_master_ro_is_allowed`, `test_ha_disabled_rw_is_allowed_on_non_master`,
  `test_rw_entry_enters_directly_on_master`) and
  `tests/test_entry.py::test_secondary_controller_maps_to_10`.

## Considered alternatives
- **Keep warn-and-enter (status quo)** — rejected: a warning does not stop the divergent
  build; the user asked for a hard gate.
- **Add a `--force`/`--allow-secondary` override** — rejected explicitly: there is no
  legitimate reason to build on the secondary, and an escape hatch is the thing that gets
  reached for by habit and reintroduces the footgun.
- **Also block `--ro` on the secondary** — rejected: read-only cannot mutate, so blocking
  it removes a safe, useful capability (inspecting the standby's image) for no benefit.
- **Keep the sync (drop only the gate)** — rejected: the sync mirrored a no-op; retaining
  it is dead code and a misleading flag.
