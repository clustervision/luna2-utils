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
lconsole v2.5 — stream console output from a TrinityX compute node.

  lconsole node001              SOL first, auto-handoff to netconsole (default)
  lconsole node001 --netconsole Netconsole UDP only
  lconsole node001 --sol-only   Interactive SOL only (IPMI or Redfish)

SOL uses a real PTY (os.openpty) so ipmitool/ssh get a genuine TTY on stdin.
Keystrokes are forwarded byte-for-byte; SIGWINCH propagates terminal resize.
Type <escape>. at the start of a line, or Ctrl+~, to exit an interactive session.
"""

__author__      = 'Dev-team'
__copyright__   = 'Copyright 2025, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '2.5'
__maintainer__  = 'Dev-team'
__email__       = 'support@clustervision.com'
__status__      = 'Development'

import argparse
import fcntl
import getpass
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import tty
import termios
import shutil
import requests

from utils.utils.log import Log
from utils.utils.ini import Ini
from utils.utils.token import Token

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NETCONSOLE_PORT        = 6666
LUNA_CONFIG_PATH       = '/trinity/local/luna/utils/config/luna.ini'
LOG_FILE               = '/var/log/luna/lconsole.log'

DEFAULT_SOL_TIMEOUT    = 180
DEFAULT_HANDOFF_GRACE  = 5
DEFAULT_SOL_CIPHER     = 3
DEFAULT_READY_MARKER   = 'TRINITYX_LCONSOLE_NETCONSOLE_READY'
DEFAULT_SOL_FAIL_GRACE = 10
DEFAULT_SOL_ESCAPE     = '!'   # escape prefix for SOL exit; use --sol-escape to override
IPMI_PREP_TIMEOUT      = 5
SSH_PROBE_TIMEOUT      = 1
logger = Log.init_log(log_file=LOG_FILE, log_level='info')
requests.packages.urllib3.disable_warnings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ConsoleEvent:
    def __init__(self, source, line):
        self.source = source
        self.line   = line


def _info(msg):
    print(f'\033[36m[lconsole]\033[0m {msg}', flush=True)


def _warn(msg):
    print(f'\033[33m[lconsole] warning:\033[0m {msg}', flush=True)


def _banner(msg):
    """Orange banner printed once at connection time."""
    print(f'\033[38;5;214m[lconsole]\033[0m {msg}', flush=True)


def node_is_booted(ip, timeout=SSH_PROBE_TIMEOUT):
    """TCP probe port 22; returns True if node OS is already up."""
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError):
        return False


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
# Netconsole UDP listener
# ---------------------------------------------------------------------------

class NetconsoleListener:
    def __init__(self, node_ip, port=NETCONSOLE_PORT):
        self.node_ip = node_ip
        self.port    = port
        self.sock    = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', self.port))
        self.sock.setblocking(False)

    def fileno(self):
        return self.sock.fileno()

    def read_events(self):
        events = []
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
            except BlockingIOError:
                break
            if addr[0] != self.node_ip:
                continue
            line = data.decode('utf-8', errors='replace').rstrip('\n')
            events.append(ConsoleEvent('NET', line))
        return events

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# ---------------------------------------------------------------------------
# SOL backend abstract base
# ---------------------------------------------------------------------------

class SolBackend:
    """
    Abstract base for all SOL backends (IPMI, Redfish SSH).

    Subclasses implement: start, read_events, is_alive, exit_summary, fileno, stop.
    The PTY helpers (_open_pty, _close_slave, _close_master) and the interactive
    loop (_pty_interactive / run_interactive) are shared by all backends.
    """

    def start(self):          raise NotImplementedError
    def read_events(self):    raise NotImplementedError
    def is_alive(self):       raise NotImplementedError
    def exit_summary(self):   raise NotImplementedError
    def fileno(self):         raise NotImplementedError
    def stop(self):           raise NotImplementedError

    @staticmethod
    def _get_terminal_size():
        """Return (rows, cols) of the operator's terminal, or (24, 80)."""
        try:
            buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
            rows, cols = struct.unpack('HHHH', buf)[:2]
            return rows or 24, cols or 80
        except Exception:
            return 24, 80

    @staticmethod
    def _set_pty_size(slave_fd):
        """Propagate the operator's terminal size to the PTY slave (TIOCSWINSZ)."""
        try:
            rows, cols = SolBackend._get_terminal_size()
            buf = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, buf)
        except Exception:
            pass

    def run_interactive(self, escape_char=DEFAULT_SOL_ESCAPE):
        """Block until the operator exits with <escape>+., Ctrl+~, or child exit."""
        esc = escape_char.encode() if isinstance(escape_char, str) else escape_char
        self._pty_interactive(esc)

    def _pty_interactive(self, esc_prefix=b'~'):
        """Bidirectional PTY passthrough. Exits on <esc_prefix>+'.', Ctrl+~, or child exit."""
        proc = getattr(self, 'proc', None)
        if proc is None:
            raise RuntimeError('SOL process not started — call start() first')

        master_fd = getattr(self, '_master_fd', None)
        slave_fd  = getattr(self, '_slave_fd',  None)
        if master_fd is None:
            raise RuntimeError('No PTY master fd — backend did not call _open_pty()')

        fd_in  = sys.stdin.fileno()
        old_tc = termios.tcgetattr(fd_in) if os.isatty(fd_in) else None

        # Push current terminal size into the slave
        if slave_fd is not None:
            self._set_pty_size(slave_fd)

        # Install SIGWINCH handler to forward resize events
        _slave_ref = [slave_fd]
        def _on_winch(signum, frame):
            if _slave_ref[0] is not None:
                self._set_pty_size(_slave_ref[0])
            self._draw_bar(f' [lconsole] {nodename}  |  BMC: {bmc_ip}  |  SOL  |  !. or Ctrl+~ to exit ')
            self._pin_top_row()

        old_winch = signal.signal(signal.SIGWINCH, _on_winch)

        at_line_start  = True
        escape_pending = False

        nodename = getattr(self, 'nodename', '?')
        bmc_ip   = getattr(self, 'bmc_ip', '?')

        try:
            if old_tc is not None:
                tty.setraw(fd_in)

            while proc.poll() is None:
                rlist = [fd_in, master_fd]
                try:
                    ready, _, _ = select.select(rlist, [], [], 0.2)
                except (ValueError, OSError):
                    break

                # ---- operator → node ----
                if fd_in in ready:
                    try:
                        chunk = os.read(fd_in, 256)
                    except OSError:
                        break
                    if not chunk:
                        break

                    filtered = bytearray()
                    for byte in (bytes([b]) for b in chunk):
                        # Ctrl+~ (RS, 0x1e) — instant exit, bypass escape state machine
                        if byte == b'\x1e':
                            self._unpin_top_row()
                            if old_tc is not None:
                                termios.tcsetattr(fd_in, termios.TCSADRAIN, old_tc)
                                old_tc = None
                            print('\r\n[lconsole] Ctrl+~ — disconnecting.', flush=True)
                            return
                        if escape_pending:
                            if byte == b'.':
                                self._unpin_top_row()
                                if old_tc is not None:
                                    termios.tcsetattr(fd_in, termios.TCSADRAIN, old_tc)
                                    old_tc = None
                                print('\r\n[lconsole] escape sequence — disconnecting.',
                                      flush=True)
                                return
                            else:
                                filtered += esc_prefix
                                filtered += byte
                            escape_pending = False
                            at_line_start  = False
                        elif at_line_start and byte == esc_prefix:
                            escape_pending = True
                        else:
                            filtered += byte
                            at_line_start = (byte in (b'\r', b'\n'))

                    if filtered:
                        try:
                            os.write(master_fd, bytes(filtered))
                        except OSError:
                            break

                # ---- node → operator ----
                if master_fd in ready:
                    try:
                        out = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not out:
                        break
                    # Strip \033[r scroll-region resets injected by ipmitool/ssh
                    import re as _re
                    out = _re.sub(b'\033\[(?:0;)?r', b'', out)
                    os.write(sys.stdout.fileno(), out)
                    # Re-apply pin in case other escape sequences reset scroll region
                    rows, _ = self._get_terminal_size()
                    if rows > 1:
                        sys.stdout.write(f'\033[2;{rows}r')
                        sys.stdout.flush()
                    at_line_start = out[-1:] in (b'\r', b'\n')

        except KeyboardInterrupt:
            pass
        finally:
            _slave_ref[0] = None
            signal.signal(signal.SIGWINCH, old_winch)
            self._unpin_top_row()
            if old_tc is not None:
                try:
                    termios.tcsetattr(fd_in, termios.TCSADRAIN, old_tc)
                except Exception:
                    pass
            self.stop()

    def _open_pty(self):
        """Open a PTY pair; call before Popen(). Child gets slave, parent keeps master."""
        master_fd, slave_fd = os.openpty()
        self._set_pty_size(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._master_fd = master_fd
        self._slave_fd  = slave_fd
        return master_fd, slave_fd

    def _close_slave(self):
        if getattr(self, '_slave_fd', None) is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None

    def _close_master(self):
        if getattr(self, '_master_fd', None) is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    # ── terminal UI (clear, status bar, scroll regions) ──

    @staticmethod
    def _clear_screen():
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()

    # ── fixed status bar ──

    @staticmethod
    def _draw_bar(msg):
        """Orange bar with black text pinned at the very top of the terminal."""
        rows, cols = SolBackend._get_terminal_size()
        bar = msg.ljust(cols)[:cols]
        sys.stdout.write(f'\0337\033[1;1H\033[48;5;214m\033[30m{bar}\033[0m\0338')
        sys.stdout.flush()

    @staticmethod
    def _pin_top_row():
        """Reserve the top row for the status bar; all scrolling happens in rows 2..N."""
        rows, _ = SolBackend._get_terminal_size()
        if rows > 1:
            sys.stdout.write(f'\033[2;{rows}r\033[{rows};1H')
            sys.stdout.flush()

    @staticmethod
    def _unpin_top_row():
        """Restore full-screen scroll region and clear the status bar line."""
        sys.stdout.write('\033[r')
        sys.stdout.write('\033[1;1H\033[2K')
        sys.stdout.flush()

# ---------------------------------------------------------------------------
# IPMI SOL backend
# ---------------------------------------------------------------------------

class IpmiSolBackend(SolBackend):
    """IPMI v2/lanplus backend via ipmitool. Prep (enable, deactivate) runs in background."""

    def __init__(self, nodename, bmc_ip, bmcsetup, cipher=DEFAULT_SOL_CIPHER):
        self.nodename   = nodename
        self.bmc_ip     = bmc_ip
        self.bmcsetup   = bmcsetup
        self.cipher     = cipher
        self.proc       = None
        self._prep_done = threading.Event()

    def _env(self):
        env = os.environ.copy()
        env['IPMI_PASSWORD'] = self.bmcsetup['password']
        return env

    def _base_cmd(self):
        return ['ipmitool', '-E', '-I', 'lanplus',
                '-C', str(self.cipher), '-H', self.bmc_ip,
                '-U', self.bmcsetup['username']]

    def _run_prep_cmd(self, extra):
        try:
            subprocess.run(self._base_cmd() + extra, env=self._env(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=IPMI_PREP_TIMEOUT)
        except Exception as exc:
            logger.debug('IPMI prep %s skipped: %s', extra, exc)

    def _prep_background(self):
        self._run_prep_cmd(['sol', 'set', 'enabled', 'true'])
        self._run_prep_cmd(['sol', 'set', 'volatile-bit-rate', '115.2'])
        self._run_prep_cmd(['sol', 'deactivate'])
        self._prep_done.set()

    def start(self):
        t = threading.Thread(target=self._prep_background, daemon=True, name='ipmi-prep')
        t.start()
        self._prep_done.wait(timeout=IPMI_PREP_TIMEOUT * 3 + 2)

        master_fd, slave_fd = self._open_pty()

        cmd = self._base_cmd() + ['sol', 'activate']
        logger.info('IPMI SOL activate: %s via %s', self.nodename, self.bmc_ip)
        self.proc = subprocess.Popen(
            cmd, env=self._env(),
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True,
        )
        self._close_slave()

    def fileno(self):
        return getattr(self, '_master_fd', None)

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_summary(self):
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is None:
            return None
        return f'IPMI SOL exited (code {rc})'

    def read_events(self):
        events  = []
        mfd = getattr(self, '_master_fd', None)
        if mfd is None or not self.is_alive():
            return events
        while True:
            try:
                chunk = os.read(mfd, 4096)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            for line in chunk.decode('utf-8', errors='replace').splitlines():
                if line:
                    events.append(ConsoleEvent('SOL', line))
        return events

    def stop(self):
        if self.proc is not None:
            try:
                mfd = getattr(self, '_master_fd', None)
                if mfd is not None:
                    os.write(mfd, b'\r~.')
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self._close_master()



# ---------------------------------------------------------------------------
# Redfish SOL backend
# ---------------------------------------------------------------------------

class RedfishSolBackend(SolBackend):
    """
    Redfish SerialConsole SSH backend.

    Queries GET /redfish/v1/Systems/{id} for SerialConsole.SSH.Port, then
    opens an SSH subprocess to the BMC (sshpass for non-interactive auth).
    SYSTEM_PATHS are tried in order to cover OpenBMC, iDRAC, iLO, AMI.
    Set bmcsetup.redfish_port in Luna to skip discovery and use a static port.
    """

    # Candidate system paths tried in order
    SYSTEM_PATHS = [
        '/redfish/v1/Systems/1',
        '/redfish/v1/Systems/system',
        '/redfish/v1/Systems/System.Embedded.1',
        '/redfish/v1/Systems/Self',
    ]

    def __init__(self, nodename, bmc_ip, bmcsetup,
                 system_path=None):
        self.nodename    = nodename
        self.bmc_ip      = bmc_ip
        self.bmcsetup    = bmcsetup
        self._system_path_override = system_path
        self.proc        = None
        self._ssh_port   = None   

    # --- Redfish discovery ---

    def _redfish_get(self, path):
        """GET a Redfish path; returns parsed JSON or raises."""
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
        """Return the first system path that responds with HTTP 200."""
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
        """Return SSH serial port: static bmcsetup.redfish_port or discovered via Redfish."""
        static = self.bmcsetup.get('redfish_port')
        if static:
            logger.info('Using static Redfish SSH port %s for %s', static, self.nodename)
            return int(static)

        system_path = self._discover_system_path()
        data        = self._redfish_get(system_path)
        serial      = data.get('SerialConsole', {})
        ssh_info    = serial.get('SSH', {})

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

    # --- SSH subprocess helpers ---

    @staticmethod
    def _sshpass_available():
        return shutil.which('sshpass') is not None

    def _build_ssh_cmd(self, port):
        base = [
            'ssh',
            '-tt',                          # force PTY allocation
            '-p', str(port),
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
        if self._sshpass_available():
            return ['sshpass', '-p', self.bmcsetup['password']] + base
        return base

    # --- SolBackend interface ---

    def start(self):
        """Discover SSH port via Redfish, then launch the SSH subprocess."""
        _info(f'Querying Redfish on {self.bmc_ip} for serial console port...')
        self._ssh_port = self._discover_ssh_port()
        _info(f'Redfish: serial console SSH port = {self._ssh_port}')

        if not self._sshpass_available():
            _warn(
                'sshpass not found — SSH password prompt will appear in the terminal. '
                'Install sshpass for fully automated login: '
                'dnf install sshpass  or  apt install sshpass'
            )

        cmd = self._build_ssh_cmd(self._ssh_port)
        env = os.environ.copy()
        env['DISPLAY'] = ''   # suppress graphical password prompts

        master_fd, slave_fd = self._open_pty()

        logger.info('Launching Redfish SSH SOL for %s: port %s', self.nodename, self._ssh_port)
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True,
        )
        self._close_slave()

    def fileno(self):
        return getattr(self, '_master_fd', None)

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_summary(self):
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is None:
            return None
        return f'Redfish SSH SOL exited (code {rc})'

    def read_events(self):
        events = []
        mfd = getattr(self, '_master_fd', None)
        if mfd is None or not self.is_alive():
            return events
        while True:
            try:
                chunk = os.read(mfd, 4096)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            for line in chunk.decode('utf-8', errors='replace').splitlines():
                if line:
                    events.append(ConsoleEvent('SOL', line))
        return events

    def stop(self):
        if self.proc is not None:
            try:
                mfd = getattr(self, '_master_fd', None)
                if mfd is not None:
                    os.write(mfd, b'\r~.')
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self._close_master()


# ---------------------------------------------------------------------------
# Main console controller
# ---------------------------------------------------------------------------

class LConsole:
    """Drives netconsole listener and/or SOL backend (hybrid / netconsole / sol)."""

    def __init__(self, nodename, details, mode='hybrid',
                 sol_backend_name='ipmi', sol_timeout=DEFAULT_SOL_TIMEOUT,
                 handoff_grace=DEFAULT_HANDOFF_GRACE, ready_marker=DEFAULT_READY_MARKER,
                 cipher=DEFAULT_SOL_CIPHER, sol_fail_grace=DEFAULT_SOL_FAIL_GRACE,
                 sol_escape=DEFAULT_SOL_ESCAPE):
        self.nodename         = nodename
        self.details          = details
        self.mode             = mode
        self.sol_backend_name = sol_backend_name
        self.sol_timeout      = sol_timeout
        self.handoff_grace    = handoff_grace
        self.ready_marker     = ready_marker
        self.cipher           = cipher
        self.sol_fail_grace   = sol_fail_grace
        self.sol_escape       = sol_escape
        self.net               = None
        self.sol               = None
        self.net_seen          = False
        self.marker_seen       = False
        self.net_first_seen_at = None
        self.start_time        = None
        self.sol_failed_at     = None
        self.sol_fail_reported = False

    def _bar_label(self):
        """Build the status-bar label for the current mode."""
        boot_ip = self.details['boot_ip'] or '?'
        bmc_ip  = self.details['bmc_ip'] or 'N/A'
        if self.mode == 'netconsole':
            body = f'{self.nodename}  |  {boot_ip}  |  netconsole UDP :{NETCONSOLE_PORT}'
        elif self.mode == 'sol':
            body = f'{self.nodename}  |  BMC: {bmc_ip}  |  {self.sol_backend_name.upper()} SOL  |  {self.sol_escape}. or Ctrl+~ to exit'
        else:
            body = f'{self.nodename}  |  {boot_ip}  |  BMC: {bmc_ip}  |  {self.sol_backend_name.upper()} + netconsole :{NETCONSOLE_PORT}'
        return f' [lconsole] {body} '

    def _setup_ui(self):
        """Clear screen, draw status bar, pin top row."""
        SolBackend._clear_screen()
        SolBackend._draw_bar(self._bar_label())
        SolBackend._pin_top_row()

    def _redraw_bar(self):
        """Redraw the status bar (after a mode change)."""
        SolBackend._draw_bar(self._bar_label())

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
        self.start_time = time.time()
        boot_ip = self.details['boot_ip']
        bmc_ip  = self.details['bmc_ip'] or 'N/A'
        if self.mode == 'netconsole':
            self._start_netconsole_only(boot_ip)
        elif self.mode == 'sol':
            self._start_sol_only(bmc_ip)
        else:
            self._start_hybrid(boot_ip, bmc_ip)

    def _start_netconsole_only(self, boot_ip):
        self._setup_ui()
        self.net = NetconsoleListener(boot_ip)
        self.net.start()
        _info('Waiting for kernel printk messages...')

    def _start_sol_only(self, bmc_ip):
        self._setup_ui()
        self.sol = self._build_sol()
        self.sol.start()
        self.sol.run_interactive(escape_char=self.sol_escape)

    def _start_hybrid(self, boot_ip, bmc_ip):
        self._setup_ui()
        self.net = NetconsoleListener(boot_ip)
        self.net.start()
        _info('SSH probe...')
        if node_is_booted(boot_ip):
            _info('Node already up — skipping SOL, netconsole-only.')
            self.mode = 'netconsole'
            self._redraw_bar()
            return
        _info('No SSH response — node is booting.')

        if not self.details['bmc_ip'] or not self.details['bmcsetup']:
            _warn('No BMC IP or bmcsetup — netconsole-only fallback.')
            self.mode = 'netconsole'
            self._redraw_bar()
            return

        _info(f'Starting {self.sol_backend_name.upper()} SOL...')
        try:
            self.sol = self._build_sol()
            self.sol.start()
        except NotImplementedError as exc:
            _warn(str(exc))
            _info('Falling back to netconsole-only.')
            self.sol = None
            self.mode = 'netconsole'
            self._redraw_bar()
            return
        except Exception as exc:
            _warn(f'SOL start failed: {exc}')
            self.sol = None
            self.mode = 'netconsole'
            self._redraw_bar()
            return

        _info(f"SOL active — handoff on '{self.ready_marker}' or net +{self.handoff_grace}s.")

    def _print_event(self, event):
        print(f'[{event.source}] {event.line}', flush=True)

    def _handle_net_event(self, event):
        self.net_seen = True
        if self.net_first_seen_at is None:
            self.net_first_seen_at = time.time()
            logger.info('First netconsole pkt from %s', self.nodename)
        if self.ready_marker in event.line:
            self.marker_seen = True
            logger.info('Ready marker seen for %s', self.nodename)
        self._print_event(event)

    def _maybe_handoff(self):
        if self.mode != 'hybrid':
            return
        now = time.time()

        if self.sol is not None and not self.sol.is_alive():
            if not self.sol_fail_reported:
                summary = self.sol.exit_summary() or 'SOL exited unexpectedly'
                _info(f'SOL: {summary}')
                logger.warning('SOL for %s exited: %s', self.nodename, summary)
                self.sol_fail_reported = True
                self.sol_failed_at     = now
            if self.net_seen:
                _info('netconsole active — continuing netconsole-only.\n')
                self.sol  = None
                self.mode = 'netconsole'
                return
            if self.sol_failed_at and (now - self.sol_failed_at) >= self.sol_fail_grace:
                _info(f'No netconsole after {self.sol_fail_grace}s since SOL exit; '
                      'waiting netconsole-only.\n')
                self.sol  = None
                self.mode = 'netconsole'
                return
            return

        if self.sol is None:
            return

        if self.marker_seen:
            _info(f'Ready marker seen — closing SOL after {self.handoff_grace}s grace.\n')
            time.sleep(self.handoff_grace)
            self.sol.stop()
            self.sol  = None
            self.mode = 'netconsole'
            return

        if self.net_seen and self.net_first_seen_at and \
                (now - self.net_first_seen_at) >= self.handoff_grace:
            _info('Netconsole confirmed — closing SOL.\n')
            self.sol.stop()
            self.sol  = None
            self.mode = 'netconsole'
            return

        if not self.net_seen and (now - self.start_time) >= self.sol_timeout:
            _warn(f'No netconsole after {self.sol_timeout}s — '
                  'switching SOL to interactive mode.\n')
            # Hand control to the full interactive PTY loop; this blocks
            # until the operator exits, then we fall through to loop() cleanup.
            self.sol.run_interactive(escape_char=self.sol_escape)
            self.sol  = None
            self.mode = 'sol'

    def loop(self):
        if self.mode == 'sol':
            return
        try:
            while True:
                fds    = []
                sol_fd = None
                if self.net is not None:
                    fds.append(self.net.fileno())
                if self.sol is not None:
                    sol_fd = self.sol.fileno()
                    if sol_fd is not None and self.sol.is_alive():
                        fds.append(sol_fd)
                if not fds:
                    time.sleep(0.5)
                    self._maybe_handoff()
                    continue
                ready, _, _ = select.select(fds, [], [], 1.0)
                if self.net is not None and self.net.fileno() in ready:
                    for ev in self.net.read_events():
                        self._handle_net_event(ev)
                if sol_fd is not None and sol_fd in ready and self.sol is not None:
                    for ev in self.sol.read_events():
                        self._print_event(ev)
                self._maybe_handoff()
        except KeyboardInterrupt:
            print('\n[lconsole] interrupted, exiting.', flush=True)
        finally:
            SolBackend._unpin_top_row()
            if self.sol is not None:
                self.sol.stop()
            if self.net is not None:
                self.net.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='lconsole',
        description=(
            'Stream console output from a compute node.\n\n'
            'MODES (mutually exclusive):\n'
            '  lconsole node001              Default: SOL → netconsole handoff\n'
            '  lconsole node001 --netconsole Netconsole UDP only (no SOL)\n'
            '  lconsole node001 --sol-only   Interactive SOL only (no netconsole)\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('nodename', help='Compute node name (e.g. node001)')

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--netconsole', action='store_true',
                      help='Netconsole UDP listener only — no SOL')
    mode.add_argument('--sol-only', action='store_true',
                      help='Interactive SOL only')

    parser.add_argument('--sol-backend', default='ipmi', choices=['ipmi', 'redfish'],
                        help='SOL backend (default: ipmi; redfish: stub)')
    parser.add_argument('--sol-timeout', type=int, default=DEFAULT_SOL_TIMEOUT,
                        metavar='SEC',
                        help='Seconds to wait for first netconsole pkt (default: %(default)s)')
    parser.add_argument('--handoff-grace', type=int, default=DEFAULT_HANDOFF_GRACE,
                        metavar='SEC',
                        help='Grace seconds before closing SOL after netconsole seen (default: %(default)s)')
    parser.add_argument('--ready-marker', default=DEFAULT_READY_MARKER,
                        help='Marker that signals netconsole is ready (default: %(default)s)')
    parser.add_argument('--sol-cipher', type=int, default=DEFAULT_SOL_CIPHER,
                        help='IPMI cipher suite (default: %(default)s)')
    parser.add_argument('--sol-fail-grace', type=int, default=DEFAULT_SOL_FAIL_GRACE,
                        metavar='SEC',
                        help='Seconds to wait for netconsole after SOL exits early (default: %(default)s)')
    parser.add_argument('--sol-escape', default=DEFAULT_SOL_ESCAPE,
                        metavar='CHAR',
                        help='Escape char for SOL exit (type <char>. at line start, or Ctrl+~; default: %(default)r)')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logger.info('User %s ran => lconsole %s', getpass.getuser(), ' '.join(sys.argv[1:]))

    if args.netconsole:
        mode = 'netconsole'
    elif args.sol_only:
        mode = 'sol'
    else:
        mode = 'hybrid'

    conf    = Ini.read_ini(ini_file=LUNA_CONFIG_PATH)
    details = resolve_node_details(args.nodename, conf)

    if args.sol_escape == '~' and (os.environ.get('SSH_TTY') or os.environ.get('SSH_CONNECTION')):
        _warn(
            'You are connected via SSH and the SOL escape character is "~".\n'
            '  SSH also uses ~ as its own escape — typing ~. may disconnect\n'
            '  your SSH session instead of just SOL.  Use --sol-escape to pick\n'
            '  a different character (e.g. --sol-escape !).'
        )

    app = LConsole(
        nodename         = args.nodename,
        details          = details,
        mode             = mode,
        sol_backend_name = args.sol_backend,
        sol_timeout      = args.sol_timeout,
        handoff_grace    = args.handoff_grace,
        ready_marker     = args.ready_marker,
        cipher           = args.sol_cipher,
        sol_fail_grace   = args.sol_fail_grace,
        sol_escape       = args.sol_escape,
    )
    app.start()
    app.loop()


if __name__ == '__main__':
    main()
