# compute-arm via lchroot — build fixes & open items (2026-06-03)

Goal: build `compute-arm` end-to-end with the image entered via **lchroot**
everywhere (Ansible Phase-B connection + daemon pack), not raw chroot. As the
build progressed, lchroot's namespace isolation (`--unshare-pid/--unshare-uts`,
tmpfs `/run`) exposed places where the TrinityX setup chain quietly relied on
raw chroot sharing the host's namespaces. Below, fixes are split into:

- **(A) Legitimate lchroot-compat fixes** — correct, keep. The playbook leaned on
  chroot semantics; we restore the equivalent behaviour without weakening lchroot.
- **(B) Real implementation problems** — worth fixing properly at the source
  (role / RPM / package list). Worked around for now; flagged here for Alex.
- **(C) Pre-existing / benign** — same behaviour under raw chroot; not a regression.

---

## (A) Legitimate lchroot-compat fixes (keep)

1. **lchroot Ansible connection plugin** (`site/connection_plugins/lchroot.py`,
   wired via `ansible.cfg` + `dynamic_hosts`). New, correct — runs every in-image
   step through `lchroot --path`. Not a workaround.

2. **In-image hostname** — lchroot got an opt-in `--hostname` flag
   (luna2-utils), and the connection passes the controller's hostname. Raw chroot
   shared the host UTS namespace so in-image `ansible_hostname` was the controller;
   lchroot's `--unshare-uts` otherwise reports the image name, breaking
   `trinity/init` controller-hostname detection. Faithful chroot reproduction; UTS
   isolation unchanged.

3. **`service` module → systemd backend** (`module_defaults: {service: {use:
   systemd}}` on in-image plays). The generic `service` module auto-detects the
   init system from inside the target (`/proc/1`, `/run/systemd/system`); under
   lchroot `/proc/1`=bwrap and `/run` is a tmpfs, so it fell back to sysvinit and
   failed ("Could not find the requested service haveged"). `systemctl enable`
   works offline, so forcing systemd is correct. (A canary `/run/systemd/system`
   was rejected: it makes `sd_booted()` true and systemctl then needs the D-Bus.)

4. **`ansible_service_mgr` fact override** (play var `ansible_service_mgr: systemd`).
   For roles that read the fact directly (the prometheus exporter roles'
   `Assert ansible_service_mgr == 'systemd'`). The image IS systemd; the sandbox
   fact is "bwrap" only due to `--unshare-pid`. Asserting the truth, not masking.

5. **`SYSTEMD_OFFLINE=1` in the lchroot sandbox** (luna2-utils sandbox.py). Ansible's
   `systemd` module (and `systemctl`) decide whether the init is live; under
   `--unshare-pid`, `is_chroot()` returns False (/proc/1/root == /), so a task like
   prometheus-node-exporter's `state: started` tried to reach a non-existent D-Bus
   and failed ("Service is in unknown state"). A raw chroot was auto-detected as
   offline and degraded to enable-only. SYSTEMD_OFFLINE=1 is the documented switch
   for exactly this; universally correct for lchroot (the image's systemd is never
   running inside the sandbox). Env hygiene, like the TMPDIR pin.

---

## (B) Real implementation problems — fix properly at source

1. **Backwards connection test `ansible_connection in 'chroot'`** — GENUINE BUG in
   TrinityX roles (not lchroot-specific). `'<conn>' in 'chroot'` is a substring
   test against the literal `'chroot'`, so it's True only for `chroot` (and
   accidental substrings) and False for `lchroot` — and would misbehave for any
   other connection name too. Found in:
   - `roles/trinity/init/tasks/main.yml` (sets `in_image` — wrong value broke the
     whole in-image/controller task split)
   - `roles/ansible/read_facts/tasks/main.yml` (silently skipped → trix_ctrl_*/luna
     creds never loaded)
   - `roles/trinity/trix-tree/tasks/main.yml`
   **Fixed properly** by flipping operands to `'chroot' in ansible_connection`
   (matches chroot + lchroot, still excludes ssh). This is the correct upstream
   fix — worth landing in TrinityX mainline regardless of lchroot.

   > Checked repo-wide: these 3 were the ONLY occurrences; all now fixed. The
   > remaining `in 'chroot'` strings in the repo are only in explanatory comments.

2. _(none confirmed yet — watching for aarch64 package/RPM gaps. haveged was fine.)_

---

## (C) Pre-existing / benign (ignored; same under chroot)

- `trinity/password` / `trinity/ood-apps` "Load a variable file ... free-form"
  — `include_vars` free-form misses on the OS-specific file; `ignore_errors`.
- `trinity/tunables` "Setting compute performance tuned profile"
  (`tuned-adm profile hpc-compute`) — needs a running TuneD daemon + D-Bus, which
  no image build has (chroot or lchroot); `ignore_errors`. The tuned profile is
  simply not applied at build time (it applies on the booted node). Benign, but
  note it if a "profile must be baked in" requirement ever appears.

---

## Build progress
- build4: 104 in-image tasks → hostname, then service_mgr
- build7: 246 in-image tasks → prometheus `ansible_service_mgr` assert
- build8: in progress (after the fact override)
