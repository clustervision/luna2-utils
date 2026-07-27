# This code is part of the TrinityX software suite
# Copyright (C) 2026  ClusterVision Solutions b.v.
# Author: Alex Ninaber
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>

"""Real-sandbox containment tests (the lchroot failure modes), automated.

These run the actual tool against a live image and assert the controller is
unreachable from inside. Gated on an explicit env var PLUS a controller-identity
check so they never run blind (PY-TEST-007). The heavier dnf/systemd scenarios live
in the LLM-driven harness (docs/lchroot/adversarial/POKE_HOLES.md); these are the fast,
safe, deterministic subset suitable for CI on a controller.

**No production image is ever mutated.** Read-only containment checks inspect the base
image (``login``) read-only. The destructive install pipeline (package install, docker,
DOCA) runs against a *disposable clone* that the ``distro_clone`` fixture creates from a
base image via ``luna osimage clone`` and removes at the end of the session — so a wedged
or messy install can only ever dirty a throwaway (the 2026-05-31 overnight run dirtied the
real ``compute-image``; that is what this design prevents).

The destructive pipeline is **parametrized over distros** (el9/``login``, ``ubuntu-image``,
``opensuse-image``): each gets its own clone and runs the native install + containment +
teardown stages. DOCA/OFED is el9-only (the NVIDIA InfiniBand path); the other distros run
docker as their heavy install. A distro whose base image is absent is skipped.

Run with:  LCHROOT_ITEST=1 ./.venv/bin/pytest -m integration
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

# The repo root: the package is `utils/lchroot` here (there is no `src/` layout), so we put
# the repo root on PYTHONPATH and run `-m utils.lchroot` against THIS checkout (finding K1) —
# not whatever `lchroot` happens to be installed on the controller.
REPO_ROOT = Path(__file__).resolve().parents[1]
LCHROOT_MODULE = "utils.lchroot"
SYSTEM_PY = os.environ.get("LCHROOT_PY", "/trinity/local/python/bin/python3")
LUNA_INI = Path("/trinity/local/luna/utils/config/luna.ini")
IMAGES_DIR = Path("/trinity/images")

# The base osimage the read-only checks inspect. `login` is a stable, real luna el9 image;
# nothing here defaults to a production node image (compute-image) any more.
BASE_IMAGE = os.environ.get("LCHROOT_ITEST_BASE", "login")
BASE_PATH = IMAGES_DIR / BASE_IMAGE

# Disposable clones are named "<prefix>-<distro>". Created per distro from its base image
# and removed afterward (see the distro_clone fixture).
CLONE_PREFIX = os.environ.get("LCHROOT_ITEST_CLONE", "lchroot-itest")
# luna also writes each clone's packed image + kernel/initrd here and only reaps them via
# a deferred (~1h) housekeeper task, so the fixture clears them itself to avoid disk creep
# (a ~2 GB tarball per clone) across repeated runs.
LUNA_FILES_DIR = Path(os.environ.get("LCHROOT_LUNA_FILES", "/trinity/local/luna/files"))

# Gate: explicit opt-in AND a real controller (luna config + the base image present).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LCHROOT_ITEST") != "1"
        or not LUNA_INI.exists()
        or not BASE_PATH.is_dir(),
        reason="integration tests require LCHROOT_ITEST=1 on a TrinityX controller",
    ),
]


# ---------------------------------------------------------------------------
# Disposable-clone lifecycle (so destructive tests never touch a production image).
# ---------------------------------------------------------------------------


def _luna(*args: str, timeout: float = 1800) -> subprocess.CompletedProcess[str]:
    """Run ``luna osimage <args>`` and return the completed process (never raises)."""
    return subprocess.run(
        ["luna", "osimage", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def _remove_clone(name: str, path: Path) -> None:
    """Remove a disposable clone from luna AND delete its rootfs + pack artifacts now.

    luna defers the on-disk path removal (and the packed image + kernel/initrd in
    LUNA_FILES_DIR) to a housekeeper queue rather than deleting synchronously, so we delete
    both ourselves to free the disk immediately rather than leaking ~GB (the rootfs plus a
    ~2 GB tarball) until the housekeeper runs.
    """
    _luna("remove", name)  # best-effort: fine if it does not exist
    shutil.rmtree(path, ignore_errors=True)
    for artifact in LUNA_FILES_DIR.glob(f"{name}-*"):
        artifact.unlink(missing_ok=True)


def _kill_image_sandboxes(*images: str) -> None:
    """SIGKILL any leftover ``bwrap`` bound to one of ``images`` (teardown safety net).

    A ``bwrap`` whose launcher was SIGKILLed (e.g. a test timeout) reparents to init and
    keeps running with its children — and a lingering ``dnf`` there holds the image's
    rpmdb lock, poisoning the next test (the cascade in
    RESULTS-2026-05-31-destructive.md). ADR 0006's ``--die-with-parent`` should now make
    such an orphan impossible, but this remains a belt-and-suspenders backstop: a process
    can die for reasons unrelated to its parent. ``pkill`` exits non-zero when nothing
    matches, which is expected and ignored.
    """
    for image in images:
        # The argv is `bwrap (--bind|--ro-bind) /trinity/images/<image> / ...`; the
        # trailing ` /` (the bind target) keeps the match specific to this image's root.
        pattern = rf"bwrap .*/trinity/images/{image} /"
        subprocess.run(["pkill", "-9", "-f", pattern], check=False)


@pytest.fixture(autouse=True)
def _guarantee_teardown() -> Iterator[None]:
    """After every integration test, ensure no sandbox (and no stale lock) survives it.

    Even a test that times out must not leak a ``bwrap`` tree onto the host. This runs on
    every exit path (pass, fail, or timeout) so one wedged test cannot poison the next. It
    covers the base image (read-only checks) and every existing clone.
    """
    yield
    clones = [p.name for p in IMAGES_DIR.glob(f"{CLONE_PREFIX}*") if p.is_dir()]
    _kill_image_sandboxes(BASE_IMAGE, *clones)
    time.sleep(0.2)
    for img_path in (BASE_PATH, *(IMAGES_DIR / c for c in clones)):
        (img_path / "tmp" / "lchroot.lock").unlink(missing_ok=True)


def _in_sandbox(image: str, script: str, *, env: dict[str, str] | None = None) -> str:
    """Run a /bin/sh script inside a read-only lchroot sandbox; return combined output."""
    proc = subprocess.run(
        [SYSTEM_PY, "-m", LCHROOT_MODULE, "--ro", image, "/bin/sh", "-c", script],
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    return proc.stdout + proc.stderr


def test_pid_namespace_isolates_from_host() -> None:
    host_pids = len([p for p in Path("/proc").iterdir() if p.name.isdigit()])
    out = _in_sandbox(BASE_IMAGE, "ls /proc | grep -cE '^[0-9]+$'; cat /proc/1/comm")
    sandbox_pids = int(out.splitlines()[0])
    assert sandbox_pids < 25, out
    assert sandbox_pids < host_pids
    assert "bwrap" in out  # sandbox pid1 is bwrap, not host systemd


def test_remount_is_denied() -> None:
    out = _in_sandbox(BASE_IMAGE, "mount -o remount,rw / 2>&1 || true")
    assert "permission denied" in out.lower(), out


def test_read_only_etc_write_is_denied() -> None:
    out = _in_sandbox(BASE_IMAGE, "touch /etc/lchroot-itest 2>&1 || true")
    assert "read-only file system" in out.lower(), out


def test_systemctl_cannot_reach_host_bus() -> None:
    out = _in_sandbox(
        BASE_IMAGE, "systemctl status nginx 2>&1 | head -1 || true"
    ).lower()
    # The property: systemctl inside the sandbox must NOT reach the host's systemd bus.
    # Different systemd versions say so differently — 15.3 replied "Failed to connect to
    # bus" / "has not been booted"; TX16.1's newer systemd detects the offline/chroot
    # context (SYSTEMD_OFFLINE=1 + --unshare-pid) and replies "Running in chroot, ignoring
    # command" — an even stronger refusal. Accept any of these; all mean "did not reach the
    # host bus". (Validated on TX16.1 / bwrap 0.6.3, 2026-07-08.)
    assert (
        "has not been booted" in out
        or "failed to connect" in out
        or "running in chroot" in out
    ), out


def test_host_process_is_invisible_and_unkillable() -> None:
    sentinel = subprocess.Popen(["sleep", "600"])  # throwaway host sentinel
    try:
        time.sleep(0.3)
        assert sentinel.poll() is None  # alive before
        out = _in_sandbox(
            BASE_IMAGE,
            f"ls -d /proc/{sentinel.pid} >/dev/null 2>&1 && echo VISIBLE || echo hidden; "
            f"kill -9 {sentinel.pid} 2>&1 || true",
        )
        assert "hidden" in out, out
        assert "no such process" in out.lower(), out
        time.sleep(0.3)
        assert sentinel.poll() is None  # survived
    finally:
        sentinel.kill()
        sentinel.wait(timeout=5)


def test_tmpdir_does_not_leak_from_host() -> None:
    # a hostile host TMPDIR must not reach the sandbox (would break dnf/librepo)
    out = _in_sandbox(
        BASE_IMAGE,
        "echo TMPDIR=$TMPDIR",
        env={"TMPDIR": "/tmp/does-not-exist-in-image"},
    )
    assert "TMPDIR=/tmp" in out, out


def test_no_host_mounts_left_behind() -> None:
    _in_sandbox(BASE_IMAGE, "true")
    mounts = Path("/proc/mounts").read_text(encoding="utf-8")
    assert BASE_IMAGE not in mounts, "lchroot left a mount on the host mount table"


# ---------------------------------------------------------------------------
# Destructive install pipeline (per distro). MUTATES the image and downloads large
# packages over the network, so gated behind a SECOND opt-in (LCHROOT_DESTRUCTIVE=1) on
# top of LCHROOT_ITEST. Each distro runs against its own disposable clone (created from its
# base image, removed at session end). Run with:
#
#   LCHROOT_ITEST=1 LCHROOT_DESTRUCTIVE=1 \
#       ./.venv/bin/pytest -m integration -k destructive --no-cov
#
# Tests pre-seed /etc/resolv.conf inside the clone (it is bound rw as /), so package repos
# resolve; behind a proxy you must also pass http(s)_proxy through. Each test is bounded
# (inner + outer no-hang guard in _run_in_image) and the autouse _guarantee_teardown
# fixture SIGKILLs any leftover bwrap after every test.
# ---------------------------------------------------------------------------

TEST_NAMESERVER = os.environ.get("LCHROOT_TEST_NAMESERVER", "8.8.8.8")
# Seed DNS inside the image so the package manager can reach the repos.
_SEED_DNS = f"printf 'nameserver %s\\n' {TEST_NAMESERVER} > /etc/resolv.conf"

DOCKER_REPO = os.environ.get(
    "LCHROOT_DOCKER_REPO", "https://download.docker.com/linux/centos/docker-ce.repo"
)
DOCA_HOST_RPM_URL = os.environ.get(
    "LCHROOT_DOCA_HOST_RPM",
    "https://www.mellanox.com/downloads/DOCA/DOCA_v3.3.0/host/"
    "doca-host-3.3.0-088000_26.01_rhel9.x86_64.rpm",
)
DOCA_PACKAGE = os.environ.get("LCHROOT_DOCA_PACKAGE", "doca-ofed")
# doca-ofed installs ~70 packages incl. a newer kernel, so the final dnf scriptlets
# (initramfs/dracut + dkms OFED module builds) run for many minutes after the download.
# A 30-min budget SIGKILLs it mid-scriptlet (observed 2026-05-31: reached 72/72 then was
# killed), so give this one install a generous, overridable budget.
DOCA_TIMEOUT = int(os.environ.get("LCHROOT_DOCA_TIMEOUT", "3600"))


@dataclass(frozen=True)
class Distro:
    """One distro's destructive-pipeline profile: which image, and native install cmds."""

    name: str  # short label used in the clone name + test id
    base_image: str  # the luna osimage to clone
    small_pkg: (
        str  # shell: install a small real package + verify (install works at all)
    )
    docker_install: str  # shell: install the docker engine + cli, verify the binaries
    kernel_module: str = "mlx5_core"  # an out-of-tree-ish module to attempt loading


_EL9 = Distro(
    name="el9",
    base_image=BASE_IMAGE,
    small_pkg="dnf -y install tree && command -v tree",
    docker_install=(
        "dnf -y install dnf-plugins-core && "
        f"dnf -y config-manager --add-repo {DOCKER_REPO} && "
        "dnf -y install docker-ce docker-ce-cli containerd.io && "
        "command -v dockerd && command -v docker"
    ),
)
_UBUNTU = Distro(
    name="ubuntu",
    base_image=os.environ.get("LCHROOT_ITEST_UBUNTU", "ubuntu-image"),
    small_pkg="apt-get update && apt-get install -y tree && command -v tree",
    docker_install=(
        "apt-get update && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io && "
        "command -v dockerd && command -v docker"
    ),
)
_OPENSUSE = Distro(
    name="opensuse",
    base_image=os.environ.get("LCHROOT_ITEST_OPENSUSE", "opensuse-image"),
    small_pkg=(
        "zypper --non-interactive --gpg-auto-import-keys refresh && "
        "zypper --non-interactive install tree && command -v tree"
    ),
    docker_install=(
        "zypper --non-interactive --gpg-auto-import-keys refresh && "
        "zypper --non-interactive install docker && "
        "command -v dockerd && command -v docker"
    ),
)
DISTROS = [_EL9, _UBUNTU, _OPENSUSE]

destructive = pytest.mark.skipif(
    os.environ.get("LCHROOT_DESTRUCTIVE") != "1",
    reason=(
        "destructive install tests need LCHROOT_DESTRUCTIVE=1 (they mutate a disposable "
        "image clone and download large packages over the network)"
    ),
)


@pytest.fixture(scope="session", params=DISTROS, ids=lambda d: d.name)
def distro_clone(request: pytest.FixtureRequest) -> Iterator[tuple[Distro, str]]:
    """Clone the param distro's base image once per session; yield (distro, clone name).

    Session-scoped + parametrized: one clone per distro is created on first use and reused
    across that distro's tests, then removed (luna entry + rootfs + pack artifacts) — so at
    most one clone exists at a time. A distro whose base image is absent is skipped.
    """
    distro: Distro = request.param
    base_path = IMAGES_DIR / distro.base_image
    if not base_path.is_dir():
        pytest.skip(f"base image {distro.base_image} not present on this controller")
    clone = f"{CLONE_PREFIX}-{distro.name}"
    clone_path = IMAGES_DIR / clone
    _remove_clone(clone, clone_path)  # clear any leftover from a prior aborted run
    res = _luna("clone", distro.base_image, clone, "-p", str(clone_path))
    if res.returncode != 0 or not (clone_path / "usr").is_dir():
        _remove_clone(clone, clone_path)
        pytest.fail(
            f"could not clone {distro.base_image} -> {clone} at {clone_path} "
            f"(rc={res.returncode})\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    try:
        yield distro, clone
    finally:
        _remove_clone(clone, clone_path)


def _run_in_image(
    image: str, script: str, *, read_only: bool = False, timeout: int = 1800
) -> subprocess.CompletedProcess[str]:
    """Run a /bin/sh script in ``image`` (a disposable clone); return the completed proc.

    rw by default (installs need to write); lchroot performs no HA sync (ADR 0011).
    Output is captured (combined via the returned proc's stdout/stderr).

    Two layers of no-hang protection (skill.md L21, RESULTS-2026-05-31 step 3):

    1. **Inner guard** — the in-sandbox work is wrapped in coreutils ``timeout`` (present
       in every image) so a wedged command is cut off (SIGKILL) even before the outer
       guard fires. ``timeout`` only signals the foreground command, so a deliberately
       detached child still exercises ADR 0006's teardown rather than being killed here.
    2. **Outer guard** — the launcher runs in its own session (process group), and on the
       launcher timeout the *whole group* is SIGKILLed, not just the Python launcher.
       (``subprocess.run(timeout=)`` only kills the direct child; a ``bwrap`` it spawned
       reparents to init and keeps running — that is what leaked the rpmdb lock before.)

    A launcher that hit the outer timeout returns ``returncode == -signal.SIGKILL``.
    """
    mode_flags = ["--ro"] if read_only else []
    inner = max(30, timeout - 30)
    argv = [
        SYSTEM_PY,
        "-m",
        LCHROOT_MODULE,
        *mode_flags,
        image,
        "timeout",
        "--signal=KILL",
        str(inner),
        "/bin/sh",
        "-c",
        script,
    ]
    proc = subprocess.Popen(
        argv,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
        rc = -signal.SIGKILL
    return subprocess.CompletedProcess(argv, rc, out or "", err or "")


@destructive
def test_destructive_native_package_install(distro_clone: tuple[Distro, str]) -> None:
    """Aim: a real native package install works inside the rw sandbox, per distro.

    Proves rw mode (drops only CAP_SYS_ADMIN) lets the distro's package manager
    (dnf/apt/zypper) write to the image, resolve repos (DNS seeded), and run maintainer
    scriptlets — the whole point of lchroot (image customisation). The fast, cheap proof
    that "installs work at all" before the heavier docker/DOCA stages.
    """
    distro, clone = distro_clone
    proc = _run_in_image(clone, f"set -e; {_SEED_DNS}; {distro.small_pkg}", timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@destructive
def test_destructive_kernel_module_load_is_denied(
    distro_clone: tuple[Distro, str],
) -> None:
    """Aim: loading a kernel module from inside the sandbox fails, on every distro.

    No CAP_SYS_MODULE and the running kernel is the host's (not FAKE_KERN), so the load
    cannot succeed — proving install != live-load (image prep installs packages; the real
    node loads modules at boot). The assertion is the load did not succeed (rc != 0),
    which is robust whether modprobe reports denial, a kernel/module-format mismatch, or
    a missing module.
    """
    distro, clone = distro_clone
    out = _run_in_image(
        clone, f"modprobe {distro.kernel_module} 2>&1; echo rc=$?", timeout=120
    )
    combined = (out.stdout + out.stderr).lower()
    assert "rc=0" not in combined, f"a module loaded inside the sandbox?!\n{combined}"


@destructive
def test_destructive_docker_userspace_installs(
    distro_clone: tuple[Distro, str],
) -> None:
    """Aim: the docker engine + cli install natively under lchroot, per distro.

    el9 uses the docker-ce repo; ubuntu uses docker.io (universe); opensuse uses the OSS
    `docker` package. Service start is skipped (no systemd in the sandbox), but the files
    install and the binaries land — the userspace half of the containment story (the
    daemon-can't-run half is the next test).
    """
    distro, clone = distro_clone
    proc = _run_in_image(
        clone, f"set -e; {_SEED_DNS}; {distro.docker_install}", timeout=1500
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@destructive
def test_destructive_docker_cannot_run_a_container_inside_sandbox(
    distro_clone: tuple[Distro, str],
) -> None:
    """Aim: even with docker installed, no container can run inside the sandbox, per distro.

    Starting a container needs the CAP_SYS_ADMIN we drop (new namespaces/cgroups) and
    there is no systemd as PID 1, so the daemon cannot come up and no container runs.
    Failure to run a container here is the desired security property, not a regression.
    """
    _distro, clone = distro_clone
    script = (
        f"{_SEED_DNS}; "
        "command -v dockerd >/dev/null 2>&1 || { echo NO_DOCKER_INSTALLED; exit 0; }; "
        "( dockerd --iptables=false >/tmp/dockerd.log 2>&1 & ) ; sleep 8; "
        "docker run --rm hello-world 2>&1 || true; "
        "echo '--- dockerd.log tail ---'; tail -n 20 /tmp/dockerd.log 2>/dev/null || true; "
        "echo '--- systemctl ---'; systemctl start docker 2>&1 | head -1 || true"
    )
    proc = _run_in_image(clone, script, timeout=180)
    out = (proc.stdout + proc.stderr).lower()
    assert "hello from docker" not in out, (
        "docker ran a container inside the sandbox — CONTAINMENT FAILED\n" + out
    )
    assert (
        "no_docker_installed" in out
        or "cannot connect to the docker daemon" in out
        or "operation not permitted" in out
        or "permission denied" in out
        or "has not been booted" in out
        or "failed to start daemon" in out
    ), out


@destructive
def test_destructive_detached_process_dies_on_exit(
    distro_clone: tuple[Distro, str],
) -> None:
    """Aim: ``--die-with-parent`` makes the teardown guarantee real, per distro (ADR 0006).

    A process detached inside the sandbox (``setsid``, its own session) is still inside
    the ``--unshare-pid`` namespace. Earlier this work a destructive run found the
    *opposite* of the pitch: bwrap's default pid-1 reaper *waited* on such a child, so the
    launcher hung and a ``bwrap``+``sleep`` tree leaked onto the host. ``--die-with-parent``
    fixes it: when the session's main command exits, the sandbox is torn down and the
    detached process is SIGKILLed. Asserts (a) the launcher returns promptly (no hang),
    (b) the detached process is gone, (c) no leftover mount or lock.
    """
    _distro, clone = distro_clone
    marker = "sleep 8675309"  # unique long sleep; portable host-visible marker
    proc = _run_in_image(
        clone,
        "setsid sleep 8675309 >/dev/null 2>&1 </dev/null & echo started; sleep 1",
        timeout=60,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != -signal.SIGKILL, (
        f"launcher HUNG — teardown failed:\n{out}"
    )
    assert "started" in out, out
    time.sleep(1.0)
    host_ps = subprocess.run(
        ["ps", "-eo", "args"], capture_output=True, text=True, check=False
    ).stdout
    assert marker not in host_ps, (
        "a detached process survived session exit — --die-with-parent teardown failed"
    )
    mounts = Path("/proc/mounts").read_text(encoding="utf-8")
    assert clone not in mounts, "lchroot left a host mount behind"
    assert not (IMAGES_DIR / clone / "tmp" / "lchroot.lock").exists(), (
        "lchroot left its lock behind"
    )


@destructive
def test_destructive_doca_ofed_installs(distro_clone: tuple[Distro, str]) -> None:
    """Aim (el9 only): NVIDIA's DOCA/OFED InfiniBand stack installs under lchroot.

    Fetch the doca-host bootstrap rpm (it lays down the DOCA repos), install it, then
    `dnf install doca-ofed`. No `sudo` — already root inside; rw mode drops only
    CAP_SYS_ADMIN so rpm's chown/mknod still work. doca-ofed is a large meta-package
    (~70 pkgs incl. a newer kernel → slow dkms/initramfs scriptlets), hence DOCA_TIMEOUT.
    DOCA is el9-specific tooling, so this skips on ubuntu/opensuse (they run docker as
    their heavy install instead).
    """
    distro, clone = distro_clone
    if distro.name != "el9":
        pytest.skip("DOCA/OFED pipeline is el9-only; other distros use docker")
    rpm_name = DOCA_HOST_RPM_URL.rsplit("/", 1)[-1]
    script = (
        f"set -e; {_SEED_DNS}; cd /tmp; "
        "command -v wget >/dev/null 2>&1 || dnf -y install wget; "
        f"wget -q {DOCA_HOST_RPM_URL}; "
        f"rpm -i {rpm_name}; "
        "dnf clean all; "
        f"dnf -y install {DOCA_PACKAGE}; "
        f"rpm -q {DOCA_PACKAGE}"
    )
    proc = _run_in_image(clone, script, timeout=DOCA_TIMEOUT)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "doca-ofed" in out.lower(), out
