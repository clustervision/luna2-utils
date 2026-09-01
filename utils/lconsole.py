#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

# This code is part of the TrinityX software suite
# Copyright (C) 2023  ClusterVision Solutions b.v.
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

"""
lconsole v4.0 — interactive SOL console for a TrinityX compute node.

  lconsole node001              Serial-over-LAN via the BMC (IPMI or Redfish)

Design: ONE event loop, ONE escape parser, ZERO rendering.
Remote bytes are written to the terminal untouched — no pinned status bar, no
scroll region, no terminal emulation. BIOS/PXE screens use absolute cursor
addressing over the full screen, so any reserved row fights the remote for
real estate; a plain banner at connect time interferes with nothing.
Keystrokes are forwarded byte-for-byte to the SOL session.

SOL is the only transport (netconsole support was removed in v4.0): the BMC
covers the whole lifetime of the node — BIOS, PXE, bootloader, kernel (with
console= on the osimage kernel options) and the booted OS. A dead or frozen
session is restarted automatically, forever, paced by --sol-fail-grace plus
a random jitter so a fleet-wide event doesn't turn many concurrent sessions
into a synchronized retry storm against the same BMCs. Retry chatter goes
quiet after the first couple of attempts (everything is still in the file
log); the session announces itself once when it comes back.

Exiting a session:
  <escape>..   (default escape char '!', at the start of a line)
  Ctrl-C       while SOL is up: first Ctrl-C is forwarded to the node,
               a second Ctrl-C within 1 s exits lconsole.
               While SOL is down (reconnecting): exits immediately.

--debug prints a periodic heartbeat (SOL byte counts, process-alive) — use it
before speculating about a "blank screen" report. It also logs (file only,
not drawn in-stream) every raw stdin read and the result of escape-filtering
it — proves whether keystrokes reach this process at all, which a
nested-SSH-hop or terminal problem could break before lconsole ever sees them.

ipmitool's own local "SOL Session operational" banner is stripped from the
IPMI backend's first read and never counted as real SOL activity — proven live
(22 Jul 2026) to otherwise fool the staleness/reconnect bookkeeping into
thinking a session had recovered when only the local banner had arrived and
the node itself had gone silent for good this cycle.
"""

__author__      = 'Dev-team'
__copyright__   = 'Copyright 2025, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '4.0'
__maintainer__  = 'Dev-team'
__email__       = 'support@clustervision.com'
__status__      = 'Development'

import argparse
import fcntl
import getpass
import logging
import os
import random
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import tty
import termios
import shutil
import requests

from utils.utils.ini import Ini
from utils.utils.token import Token

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LUNA_CONFIG_PATH       = '/trinity/local/luna/utils/config/luna.ini'
LOG_FILE               = '/var/log/luna/lconsole.log'

DEFAULT_SOL_CIPHER     = 3
DEFAULT_SOL_FAIL_GRACE = 10   # seconds between reconnect attempts while SOL is down
DEFAULT_SOL_ESCAPE     = '!'   # escape prefix for exit; use --sol-escape to override
IPMI_PREP_TIMEOUT      = 5
SOL_STALE_AFTER_INPUT  = 5.0  # seconds of silence after a keystroke before we warn
DEBUG_HEARTBEAT_SECS   = 5.0  # --debug: how often to print the byte-count heartbeat
# Reconnects are unbounded on purpose: SOL is the only transport, and a live
# capture (22 Jul 2026) showed a real BMC needing ~10 reconnect cycles to get
# through a rocky chipset-init/POST phase before the node's own SOL output
# stabilized — any cap risks giving up mid-recovery on exactly that case. The
# operator ends a session; the tool doesn't.
SOL_RECONNECT_JITTER_MAX = 5.0  # random 0..N-second delay before each reconnect
# After a reconnect the payload is often still dead (the BMC drops SOL over
# and over around kexec/PXE — reproduced live, 16 Aug 2026: each reconnect
# "succeeded" but delivered nothing until the operator typed again). So every
# reconnect arms a probe: if not a single byte arrives within
# SOL_EXPECT_OUTPUT_SECS, reconnect again on our own — but only up to
# MAX_SILENT_RECONNECTS consecutive silent cycles, because an idle login
# prompt after a genuine recovery is legitimately silent forever and must not
# be reconnected out from under the operator indefinitely. Any received byte
# resets the count and ends the probing.
SOL_EXPECT_OUTPUT_SECS   = 10.0
MAX_SILENT_RECONNECTS    = 12   # covers the observed ~10-cycle rocky transition
# attempt, so a fleet-wide event (mass reboot, common BMC hiccup) doesn't turn
# hundreds/thousands of concurrent lconsole sessions into a synchronized retry
# storm against the same BMCs/network at the same instant.
CTRL_C                 = b'\x03'
CTRL_C_EXIT_WINDOW     = 1.0   # second Ctrl-C within this many seconds exits


def _init_logger():
    """File logger; falls back to a null logger when the log path is unwritable
    (non-root users must still be able to run lconsole)."""
    log = logging.getLogger('lconsole')
    log.setLevel(logging.INFO)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        handler = logging.FileHandler(LOG_FILE)
        handler.setFormatter(logging.Formatter(
            '[%(levelname)s]:[%(asctime)s]:[%(threadName)s]:'
            '[%(filename)s:%(funcName)s@%(lineno)d] - %(message)s'))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())
    return log


logger = _init_logger()
requests.packages.urllib3.disable_warnings()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _info(msg):
    print(f'\033[36m[lconsole]\033[0m {msg}', flush=True)


def _warn(msg):
    print(f'\033[33m[lconsole] warning:\033[0m {msg}', flush=True)


def terminal_size():
    """(rows, cols) of the operator's terminal, or (24, 80)."""
    try:
        buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
        rows, cols = struct.unpack('HHHH', buf)[:2]
        return rows or 24, cols or 80
    except (OSError, ValueError):
        return 24, 80


# ---------------------------------------------------------------------------
# Luna API helpers
# ---------------------------------------------------------------------------

def call_api(conf, path):
    token = Token.get_token(
        username=conf['USERNAME'], password=conf['PASSWORD'],
        protocol=conf['PROTOCOL'], endpoint=conf['ENDPOINT'],
        verify_certificate=conf['VERIFY_CERTIFICATE'],
    )
    url  = f"{conf['PROTOCOL']}://{conf['ENDPOINT']}{path}"
    resp = requests.get(url, headers={'x-access-tokens': token}, timeout=15,
                        verify=conf['VERIFY_CERTIFICATE'])
    resp.raise_for_status()
    return resp.json()


def get_node_config(nodename, conf):
    data = call_api(conf, f'/config/node/{nodename}')
    node = data.get('config', {}).get('node', {}).get(nodename)
    if not node:
        raise RuntimeError(f'Node {nodename} not found in Luna API')
    return node


def get_group_config(groupname, conf):
    data  = call_api(conf, f'/config/group/{groupname}')
    group = data.get('config', {}).get('group', {}).get(groupname)
    if not group:
        raise RuntimeError(f'Group {groupname} not found in Luna API')
    return group


def get_bmcsetup_config(name, conf):
    data     = call_api(conf, f'/config/bmcsetup/{name}')
    bmcsetup = data.get('config', {}).get('bmcsetup', {}).get(name)
    if not bmcsetup:
        raise RuntimeError(f'Bmcsetup {name} not found in Luna API')
    return bmcsetup


def resolve_node_details(nodename, conf):
    node    = get_node_config(nodename, conf)
    boot_ip = None
    bmc_ip  = None
    for iface in node.get('interfaces', []):
        n = iface.get('interface', '').upper()
        if n == 'BOOTIF':
            boot_ip = iface.get('ipaddress')
        elif n == 'BMC':
            bmc_ip = iface.get('ipaddress')
    if not boot_ip:
        try:
            boot_ip = socket.gethostbyname(nodename)
        except socket.gaierror as exc:
            raise RuntimeError(f'Cannot resolve BOOTIF IP for {nodename}: {exc}')
    groupname     = node.get('group')
    group         = get_group_config(groupname, conf) if groupname else {}
    bmcsetup_name = node.get('bmcsetupname') or group.get('bmcsetupname')
    bmcsetup      = get_bmcsetup_config(bmcsetup_name, conf) if bmcsetup_name else None
    return {
        'node': node, 'group': group,
        'boot_ip': boot_ip, 'bmc_ip': bmc_ip,
        'bmcsetup_name': bmcsetup_name, 'bmcsetup': bmcsetup,
    }


# ---------------------------------------------------------------------------
# Escape / exit detection — the ONE state machine for every mode
# ---------------------------------------------------------------------------

class EscapeFilter:
    """
    Scans operator keystrokes for the exit sequence.

    Exit on <escape>'..' typed at the start of a line, or on Ctrl-C:
    immediately when there is nothing to forward input to (SOL down,
    mid-reconnect), or on a second Ctrl-C within CTRL_C_EXIT_WINDOW seconds
    when the SOL session is up (the first one is forwarded to the node).

    feed() returns (bytes_to_forward, exit_requested). After a forwarded
    Ctrl-C, .notice holds a one-time hint for the operator.
    """

    def __init__(self, escape=b'!'):
        self.escape     = escape
        self.line_start = True
        self.armed      = 0      # 0 idle, 1 <esc> seen, 2 <esc>. seen
        self.last_intr  = 0.0
        self.notice     = None
        self.hinted     = False

    def _plain(self, out, byte):
        out += byte
        self.line_start = byte in (b'\r', b'\n')

    def feed(self, data, interactive=True):
        out = bytearray()
        for b in data:
            byte = bytes([b])
            if byte == CTRL_C:
                now = time.monotonic()
                if not interactive or (now - self.last_intr) <= CTRL_C_EXIT_WINDOW:
                    return bytes(out), True
                self.last_intr = now
                if not self.hinted:
                    self.notice = ('Ctrl-C sent to the node — press Ctrl-C again '
                                   'quickly to exit lconsole')
                    self.hinted = True
                self._plain(out, byte)
            elif self.armed:
                if byte == b'.':
                    self.armed += 1
                    if self.armed == 3:
                        return bytes(out), True
                else:
                    # abort: flush what was swallowed, then handle this byte
                    out += self.escape + b'.' * (self.armed - 1)
                    self.armed = 0
                    self._plain(out, byte)
            elif self.line_start and byte == self.escape:
                self.armed = 1
            else:
                self._plain(out, byte)
        return bytes(out), False


# ---------------------------------------------------------------------------
# SOL backends — shared PTY/subprocess plumbing, per-backend command building
# ---------------------------------------------------------------------------

class SolBackend:
    """
    Base class owning the PTY and child process. Subclasses implement
    command() -> (argv, env) and optionally prepare() for pre-flight work.
    """

    label  = 'SOL'
    # A backend whose client prints its own local banner on activation (ipmitool
    # does; a plain ssh session doesn't) sets this so read() can strip it — see
    # the note on read() below for why this matters more than it looks like it
    # should.
    BANNER = None
    # (noisy, calm) pairs: client-printed error lines that read far more
    # alarming than the event they describe — a BMC tearing down SOL while the
    # chassis resets is routine, and the reconnect logic (not the operator) is
    # what deals with it. read() swaps the wording; a chunk boundary splitting
    # the line would let the original through, which is accepted — worst case
    # is the scary original showing once.
    NOISE  = ()

    def __init__(self, nodename, bmc_ip, bmcsetup):
        self.nodename   = nodename
        self.bmc_ip     = bmc_ip
        self.bmcsetup   = bmcsetup
        self.proc       = None
        self._master_fd = None
        self._banner_pending = self.BANNER is not None

    def prepare(self):
        pass

    def command(self):
        raise NotImplementedError

    def start(self, winsize=(24, 80)):
        self.prepare()
        cmd, env = self.command()
        master_fd, slave_fd = os.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                    struct.pack('HHHH', winsize[0], winsize[1], 0, 0))
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._master_fd = master_fd
        logger.info('%s SOL start for %s via %s', self.label, self.nodename, self.bmc_ip)
        self.proc = subprocess.Popen(
            cmd, env=env,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True, start_new_session=True,
        )
        os.close(slave_fd)

    def fileno(self):
        return self._master_fd

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_summary(self):
        rc = self.proc.poll() if self.proc else None
        return None if rc is None else f'{self.label} SOL exited (code {rc})'

    def read(self):
        chunks = []
        while self._master_fd is not None:
            try:
                chunk = os.read(self._master_fd, 4096)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
            chunks.append(chunk)
        out = b''.join(chunks)
        if self._banner_pending and out:
            # ipmitool prints its own local "[SOL Session operational...]" line
            # the instant the client-side session opens — before the BMC has
            # delivered a single real byte from the node. Counted as genuine
            # activity, it silently defeats the staleness/reconnect logic: a
            # BMC that keeps dropping the actual SOL payload during a rocky
            # POST can reconnect over and over, each time producing just this
            # banner, each time looking "alive" to every caller — proven live
            # (22 Jul 2026): 5+ reconnect cycles where the only bytes seen were
            # this banner, repeated, while the node produced nothing.
            self._banner_pending = False
            if out.startswith(self.BANNER):
                out = out[len(self.BANNER):]
        for noisy, calm in self.NOISE:
            if noisy in out:
                out = out.replace(noisy, calm)
        return out

    def write(self, data):
        """Returns True if the bytes were handed to the PTY. False means the far
        side (ipmitool) has stopped draining its stdin — the operator's keystrokes
        would otherwise vanish silently, which is worse than a visible warning."""
        if self._master_fd is None or not data:
            return True
        try:
            os.write(self._master_fd, data)
            return True
        except OSError:
            return False

    def set_winsize(self, rows, cols):
        if self._master_fd is not None:
            try:
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ,
                            struct.pack('HHHH', rows, cols, 0, 0))
            except OSError:
                pass

    def stop(self):
        if self.proc is not None:
            # graceful deactivate first (~. is the ipmitool/ssh escape)
            self.write(b'\r~.')
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None


class IpmiSolBackend(SolBackend):
    """IPMI v2/lanplus via ipmitool."""

    label  = 'IPMI'
    BANNER = b'[SOL Session operational.  Use ~? for help]\r\n'
    NOISE  = (
        (b'Error: No response to keepalive - Terminating session',
         b'[sol] BMC stopped answering \xe2\x80\x94 it is likely resetting with the chassis (normal during a reboot)'),
        (b'Error: No response de-activating SOL payload', b''),
    )

    def __init__(self, nodename, bmc_ip, bmcsetup, cipher=DEFAULT_SOL_CIPHER):
        super().__init__(nodename, bmc_ip, bmcsetup)
        self.cipher = cipher

    def _env(self):
        env = os.environ.copy()
        env['IPMI_PASSWORD'] = self.bmcsetup['password']
        return env

    def _base_cmd(self):
        return ['ipmitool', '-E', '-I', 'lanplus',
                '-C', str(self.cipher), '-H', self.bmc_ip,
                '-U', self.bmcsetup['username']]

    def prepare(self):
        """Enable SOL and kick any stale session before activating."""
        for extra in (['sol', 'set', 'enabled', 'true'],
                      ['sol', 'set', 'volatile-bit-rate', '115.2'],
                      ['sol', 'deactivate']):
            try:
                subprocess.run(self._base_cmd() + extra, env=self._env(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=IPMI_PREP_TIMEOUT)
            except Exception as exc:
                logger.debug('IPMI prep %s skipped: %s', extra, exc)

    def command(self):
        return self._base_cmd() + ['sol', 'activate'], self._env()


class RedfishSolBackend(SolBackend):
    """
    Redfish SerialConsole SSH backend.

    Queries GET /redfish/v1/Systems/{id} for SerialConsole.SSH.Port, then
    opens an SSH subprocess to the BMC (sshpass for non-interactive auth).
    SYSTEM_PATHS are tried in order to cover OpenBMC, iDRAC, iLO, AMI.
    Set bmcsetup.redfish_port in Luna to skip discovery and use a static port.
    """

    label = 'REDFISH'

    SYSTEM_PATHS = [
        '/redfish/v1/Systems/1',
        '/redfish/v1/Systems/system',
        '/redfish/v1/Systems/System.Embedded.1',
        '/redfish/v1/Systems/Self',
    ]

    def __init__(self, nodename, bmc_ip, bmcsetup, system_path=None):
        super().__init__(nodename, bmc_ip, bmcsetup)
        self._system_path_override = system_path
        self._ssh_port = None

    def _redfish_get(self, path):
        url  = f'https://{self.bmc_ip}{path}'
        resp = requests.get(
            url,
            auth=(self.bmcsetup['username'], self.bmcsetup['password']),
            verify=False,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _discover_system_path(self):
        if self._system_path_override:
            return self._system_path_override
        for path in self.SYSTEM_PATHS:
            try:
                self._redfish_get(path)
                logger.debug('Redfish system path found: %s', path)
                return path
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                raise
        raise RuntimeError(
            f'Cannot find a valid Redfish System path on {self.bmc_ip}. '
            f'Tried: {self.SYSTEM_PATHS}')

    def _discover_ssh_port(self):
        static = self.bmcsetup.get('redfish_port')
        if static:
            logger.info('Using static Redfish SSH port %s for %s', static, self.nodename)
            return int(static)

        system_path = self._discover_system_path()
        data        = self._redfish_get(system_path)
        ssh_info    = data.get('SerialConsole', {}).get('SSH', {})

        if not ssh_info:
            raise RuntimeError(
                f'No SerialConsole.SSH property in Redfish response from '
                f'{self.bmc_ip}{system_path}. '
                f'Your BMC firmware may not support Redfish serial console.')

        if not ssh_info.get('ServiceEnabled', False):
            raise RuntimeError(
                f'Redfish SerialConsole.SSH.ServiceEnabled is false on '
                f'{self.bmc_ip}. Enable it in the BMC web UI or via:\n'
                f'  PATCH {system_path}  '
                f'{{"SerialConsole": {{"SSH": {{"ServiceEnabled": true}}}}}}')

        port = ssh_info.get('Port')
        if not port:
            raise RuntimeError(
                f'Redfish SerialConsole.SSH.Port is missing on {self.bmc_ip}. '
                f'Try setting bmcsetup.redfish_port manually in Luna.')

        logger.info('Redfish discovered SSH serial port %s on %s', port, self.bmc_ip)
        return int(port)

    def prepare(self):
        _info(f'Querying Redfish on {self.bmc_ip} for serial console port...')
        self._ssh_port = self._discover_ssh_port()
        _info(f'Redfish: serial console SSH port = {self._ssh_port}')
        if not shutil.which('sshpass'):
            _warn(
                'sshpass not found — SSH password prompt will appear in the terminal. '
                'Install sshpass for fully automated login: '
                'dnf install sshpass  or  apt install sshpass'
            )

    def command(self):
        cmd = [
            'ssh',
            '-tt',                          # force PTY allocation
            '-p', str(self._ssh_port),
            '-l', self.bmcsetup['username'],
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3',
            '-o', 'PubkeyAuthentication=no',
            '-o', 'PreferredAuthentications=password,keyboard-interactive',
            self.bmc_ip,
        ]
        if shutil.which('sshpass'):
            cmd = ['sshpass', '-p', self.bmcsetup['password']] + cmd
        env = os.environ.copy()
        env['DISPLAY'] = ''   # suppress graphical password prompts
        return cmd, env


# ---------------------------------------------------------------------------
# Main console controller — one loop for every mode
# ---------------------------------------------------------------------------

class LConsole:
    """
    Drives the SOL backend: stdin is filtered through EscapeFilter and
    forwarded to the SOL PTY (so the operator can interact with BIOS/grub
    during boot), remote bytes stream to the terminal verbatim, and a dead or
    frozen session is restarted on a paced cadence without blocking the loop.
    """

    def __init__(self, nodename, details,
                 sol_backend_name='ipmi', cipher=DEFAULT_SOL_CIPHER,
                 sol_fail_grace=DEFAULT_SOL_FAIL_GRACE,
                 sol_escape=DEFAULT_SOL_ESCAPE, debug=False):
        self.nodename         = nodename
        self.details          = details
        self.sol_backend_name = sol_backend_name
        self.cipher           = cipher
        self.sol_fail_grace   = sol_fail_grace
        self.sol_escape       = sol_escape
        self.debug            = debug

        self.filter = EscapeFilter(sol_escape.encode())
        self.sol    = None

        self.sol_gone_at = None   # when a dead SOL was first noticed
        self.exit_reason = None
        self._winch      = False
        # SOL "frozen but still alive" detection: ipmitool can keep running
        # while the BMC has silently dropped the payload (common across reboots).
        # An idle console legitimately produces zero output, so staleness is
        # judged against OPERATOR INPUT getting no reply — not against silence
        # alone, which would false-positive on any quiet login prompt.
        self._sol_input_sent_at     = None
        self._sol_output_since_input = True
        self._sol_stale_warned      = False
        self._sol_reconnect_count   = 0
        # post-reconnect liveness probe (see SOL_EXPECT_OUTPUT_SECS)
        self._sol_probe_deadline    = None
        self._silent_reconnects     = 0

        # --debug: periodic heartbeat proving (or disproving) that bytes are
        # actually arriving, so "screen is blank" can be told apart from
        # "bytes arrive but nothing visible renders" without guessing.
        self._sol_rx_total     = 0
        self._sol_rx_interval  = 0
        self._debug_last       = None

    # ── presentation ──

    def _session_line(self):
        bmc_ip = self.details['bmc_ip'] or 'N/A'
        escape = f'{self.sol_escape}.. or Ctrl-C to exit'
        return f'{self.nodename}  |  BMC: {bmc_ip}  |  {self.sol_backend_name.upper()} SOL  |  {escape}'

    def _banner(self):
        """One orange line at connect time; scrolls away, fights nothing."""
        line = f' [lconsole] {self._session_line()} '
        if sys.stdout.isatty():
            self._write(f'\r\033[48;5;214m\033[30m{line}\033[0m\r\n'.encode())
        else:
            self._write(f'{line}\r\n'.encode())

    def _write(self, data):
        """Remote bytes → terminal, verbatim."""
        os.write(sys.stdout.fileno(), data)

    def _message(self, msg):
        """A local status line inside the stream (col 0, own line)."""
        self._write(f'\r\n\033[36m[lconsole]\033[0m {msg}\r\n'.encode())
        logger.info('%s: %s', self.nodename, msg)

    # ── construction ──

    def _build_sol(self):
        bmc_ip   = self.details['bmc_ip']
        bmcsetup = self.details['bmcsetup']
        if not bmc_ip:
            raise RuntimeError(f'Node {self.nodename} has no BMC IP in Luna')
        if not bmcsetup:
            raise RuntimeError(f'Node {self.nodename} has no bmcsetup in Luna')
        if self.sol_backend_name == 'ipmi':
            return IpmiSolBackend(self.nodename, bmc_ip, bmcsetup, cipher=self.cipher)
        if self.sol_backend_name == 'redfish':
            return RedfishSolBackend(self.nodename, bmc_ip, bmcsetup)
        raise RuntimeError(f'Unknown SOL backend: {self.sol_backend_name}')

    def start(self):
        _info(f'Starting {self.sol_backend_name.upper()} SOL...')
        self.sol = self._build_sol()
        self.sol.start(winsize=terminal_size())

    # ── event handling ──

    def _restart_sol(self, why, quiet_after=2):
        """Tear down and restart the SOL backend (jittered). quiet_after=N
        prints the first N attempts, announces once that retrying continues
        quietly, then logs the rest file-only — a slow POST can take dozens
        of paced attempts and narrating each one buries the console."""
        self._sol_reconnect_count += 1
        n = self._sol_reconnect_count
        jitter = random.uniform(0, SOL_RECONNECT_JITTER_MAX)
        line = f'{why} — reconnecting SOL in {jitter:.1f}s (attempt {n})...'
        loud = quiet_after is None or n <= quiet_after
        if loud:
            self._message(line)
        else:
            if n == quiet_after + 1:
                self._message('SOL is still down — retrying quietly until it comes back.')
            logger.info('%s: %s', self.nodename, line)
        time.sleep(jitter)
        if self.sol is not None:
            self.sol.stop()
        try:
            self.sol = self._build_sol()
            self.sol.start(winsize=terminal_size())
        except Exception as exc:
            if loud:
                self._message(f'warning: reconnect failed: {exc}')
            else:
                logger.info('%s: reconnect failed: %s', self.nodename, exc)
            self.sol = None
            return
        self._sol_input_sent_at      = None
        self._sol_output_since_input = True
        self._sol_stale_warned       = False
        # arm the liveness probe: a reconnect that delivers nothing within
        # this window is treated as another dead payload, not a success
        self._sol_probe_deadline = time.monotonic() + SOL_EXPECT_OUTPUT_SECS
        self._message('SOL reconnected.')

    def _maybe_recover_sol(self):
        """A dead SOL session gets restarted on the sol_fail_grace cadence,
        forever: BMCs routinely kill the SOL payload once while the chassis
        resets (keepalive timeout, especially on shared-NIC BMCs) and come
        back seconds later. With SOL the only transport, giving up would mean
        a dark screen for the rest of the boot — the operator ends a session,
        the tool doesn't."""
        if self.sol is not None and self.sol.is_alive():
            self.sol_gone_at = None
            # post-reconnect probe: "reconnected" but not one byte arrived —
            # the payload is likely still dead (BMCs drop SOL repeatedly
            # around kexec/PXE); reconnect again without waiting for the
            # operator to type. Bounded: an idle prompt after a genuine
            # recovery is legitimately silent, so after MAX_SILENT_RECONNECTS
            # consecutive silent cycles stop probing and say so once.
            if (self._sol_probe_deadline is not None
                    and time.monotonic() >= self._sol_probe_deadline):
                self._sol_probe_deadline = None
                if self._silent_reconnects < MAX_SILENT_RECONNECTS:
                    self._silent_reconnects += 1
                    self._restart_sol('reconnected but no output yet')
                else:
                    self._message('console is still silent after '
                                  f'{MAX_SILENT_RECONNECTS} reconnects — probably just '
                                  'an idle console; type something to re-check.')
            return
        now = time.monotonic()
        if self.sol_gone_at is None:
            self.sol_gone_at = now
            if self.sol is not None:
                summary = self.sol.exit_summary() or 'SOL exited unexpectedly'
                self._message(f'{summary} — restarting in {self.sol_fail_grace}s.')
            return
        if now - self.sol_gone_at < self.sol_fail_grace:
            return
        self.sol_gone_at = None
        self._restart_sol('SOL is down')

    def _debug_heartbeat(self, sol_fd):
        """--debug: prove whether bytes are actually arriving. Tells apart a
        truly silent link (0 bytes, process dead or BMC dropped it) from bytes
        arriving but not visibly rendering (a real count, screen still blank)."""
        now = time.monotonic()
        if self._debug_last is None:
            self._debug_last = now
            return
        if now - self._debug_last < DEBUG_HEARTBEAT_SECS:
            return
        sol_alive = self.sol.is_alive() if self.sol is not None else None
        self._message(
            f'debug: sol_fd={sol_fd} sol_alive={sol_alive} '
            f'reconnects={self._sol_reconnect_count} '
            f'sol_rx last{DEBUG_HEARTBEAT_SECS:.0f}s/total={self._sol_rx_interval}/{self._sol_rx_total}B')
        self._sol_rx_interval = 0
        self._debug_last = now

    def _on_winch(self, signum, frame):
        self._winch = True

    def _handle_resize(self):
        if self.sol is not None:
            self.sol.set_winsize(*terminal_size())

    # ── the loop ──

    def _on_term(self, signum, frame):
        # convert SIGTERM/SIGHUP into an exception so the finally block
        # restores the operator's terminal (echo, raw mode) before dying
        self.exit_reason = f'signal {signum}'
        raise SystemExit(128 + signum)

    def run(self):
        fd_in      = sys.stdin.fileno()
        stdin_tty  = os.isatty(fd_in)
        old_tc     = termios.tcgetattr(fd_in) if stdin_tty else None
        stdin_open = True
        old_winch  = signal.signal(signal.SIGWINCH, self._on_winch)
        old_term   = signal.signal(signal.SIGTERM, self._on_term)
        old_hup    = signal.signal(signal.SIGHUP, self._on_term)
        self._banner()
        try:
            if old_tc is not None:
                tty.setraw(fd_in)

            while True:
                if self._winch:
                    self._winch = False
                    self._handle_resize()

                sol_fd = self.sol.fileno() if (self.sol and self.sol.is_alive()) else None
                if sol_fd is None and self.sol is not None:
                    out = self.sol.read()   # drain what the child wrote before exiting
                    if out:
                        self._write(out)

                fds = []
                if stdin_open:
                    fds.append(fd_in)
                if sol_fd is not None:
                    fds.append(sol_fd)
                if not fds:
                    self.exit_reason = 'nothing left to watch'
                    return

                ready, _, _ = select.select(fds, [], [], 0.2)

                # operator → escape filter → node
                if stdin_open and fd_in in ready:
                    try:
                        data = os.read(fd_in, 256)
                    except OSError:
                        data = b''
                    if not data:
                        stdin_open = False   # EOF (piped stdin) — keep streaming
                    else:
                        if self.debug:
                            # Proves bytes reach this process at all — the one thing a
                            # nested SSH-hop / terminal-multiplexer problem could break
                            # before lconsole ever sees a keystroke. Log-only (not drawn
                            # in-stream) so it doesn't clutter what you're watching.
                            logger.info('%s: stdin raw bytes: %r', self.nodename, data)
                        fwd, want_exit = self.filter.feed(data, interactive=sol_fd is not None)
                        if self.debug:
                            logger.info('%s: after escape-filter: fwd=%r want_exit=%s '
                                        'sol_fd=%s', self.nodename, fwd, want_exit, sol_fd)
                        if self.filter.notice:
                            self._message(self.filter.notice)
                            self.filter.notice = None
                        if sol_fd is not None and fwd:
                            if not self.sol.write(fwd):
                                self._message('warning: could not send keystrokes to the '
                                              'node — the SOL link appears stuck.')
                            else:
                                self._sol_input_sent_at      = time.monotonic()
                                self._sol_output_since_input = False
                                self._sol_stale_warned       = False
                        if want_exit:
                            self.exit_reason = 'exit requested'
                            return

                # node → operator
                if sol_fd is not None and sol_fd in ready:
                    out = self.sol.read()
                    if out:
                        self._write(out)
                        self._sol_output_since_input = True
                        self._sol_reconnect_count     = 0  # proven healthy again
                        self._sol_probe_deadline      = None
                        self._silent_reconnects       = 0
                        if self.debug:
                            self._sol_rx_total    += len(out)
                            self._sol_rx_interval += len(out)

                if (sol_fd is not None and self._sol_input_sent_at is not None
                        and not self._sol_output_since_input and not self._sol_stale_warned
                        and time.monotonic() - self._sol_input_sent_at > SOL_STALE_AFTER_INPUT):
                    self._sol_stale_warned = True
                    # frozen-but-alive payload: same disease as a dead one —
                    # restart rather than advise
                    self._restart_sol(
                        f'no response {SOL_STALE_AFTER_INPUT:.0f}s after your last '
                        'keystroke — the BMC may have dropped the SOL session')

                if self.debug:
                    self._debug_heartbeat(sol_fd)

                self._maybe_recover_sol()

        except KeyboardInterrupt:
            self.exit_reason = 'interrupted'
        finally:
            signal.signal(signal.SIGWINCH, old_winch)
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGHUP, old_hup)
            if old_tc is not None:
                try:
                    termios.tcsetattr(fd_in, termios.TCSADRAIN, old_tc)
                except termios.error:
                    pass
            if self.sol is not None:
                self.sol.stop()
            # \033[0m\033[?25h: undo any colour/hidden-cursor state the remote left
            print(f'\r\033[K\033[0m\033[?25h\033[36m[lconsole]\033[0m disconnected from '
                  f'{self.nodename} ({self.exit_reason or "session ended"}).', flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='lconsole',
        description=(
            'Interactive Serial-over-LAN console for a compute node.\n\n'
            '  lconsole node001\n\n'
            'EXIT: type <escape>.. at the start of a line (default: !..),\n'
            'or press Ctrl-C (twice within 1s while the SOL session is up).\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('nodename', help='Compute node name (e.g. node001)')

    parser.add_argument('--sol-backend', default='ipmi', choices=['ipmi', 'redfish'],
                        help='SOL backend (default: %(default)s)')
    parser.add_argument('--sol-cipher', type=int, default=DEFAULT_SOL_CIPHER,
                        help='IPMI cipher suite (default: %(default)s)')
    parser.add_argument('--sol-fail-grace', type=int, default=DEFAULT_SOL_FAIL_GRACE,
                        metavar='SEC',
                        help='Seconds between reconnect attempts while SOL is down (default: %(default)s)')
    parser.add_argument('--sol-escape', default=DEFAULT_SOL_ESCAPE,
                        metavar='CHAR',
                        help='Escape char for exit (type <char>.. at line start; default: %(default)r)')
    parser.add_argument('--debug', action='store_true',
                        help='Print a periodic byte-count heartbeat (SOL rx, process '
                             'alive) — tells a truly silent link apart from bytes '
                             'arriving but not visibly rendering.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logger.info('User %s ran => lconsole %s', getpass.getuser(), ' '.join(sys.argv[1:]))

    if args.sol_escape == '~' and (os.environ.get('SSH_TTY') or os.environ.get('SSH_CONNECTION')):
        _warn(
            'You are connected via SSH and the SOL escape character is "~".\n'
            '  SSH also uses ~ as its own escape — typing ~. may disconnect\n'
            '  your SSH session instead of just SOL.  Use --sol-escape to pick\n'
            '  a different character (e.g. --sol-escape !).'
        )

    try:
        conf    = Ini.read_ini(ini_file=LUNA_CONFIG_PATH)
        details = resolve_node_details(args.nodename, conf)
    except Exception as exc:
        logger.exception('resolving %s failed', args.nodename)
        print(f'lconsole: {exc}', file=sys.stderr)
        return 1

    app = LConsole(
        nodename         = args.nodename,
        details          = details,
        sol_backend_name = args.sol_backend,
        cipher           = args.sol_cipher,
        sol_fail_grace   = args.sol_fail_grace,
        sol_escape       = args.sol_escape,
        debug            = args.debug,
    )
    try:
        app.start()
    except Exception as exc:
        logger.exception('starting console for %s failed', args.nodename)
        print(f'lconsole: {exc}', file=sys.stderr)
        return 1
    app.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
