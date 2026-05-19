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
lconsole v2.4 — stream console output from a compute node.

MODES
-----
  lconsole node001              Default: SOL first, hand off to netconsole
  lconsole node001 --netconsole Netconsole UDP only (no SOL)
  lconsole node001 --sol-only   Interactive SOL only (no netconsole)

SOL BACKENDS  (--sol-backend ipmi|redfish)
  ipmi     ipmitool lanplus — default, works on most hardware
  redfish  Redfish SerialConsole SSH discovery — stub, not yet implemented

DEFAULT (hybrid) FLOW
  1. Print banner instantly.
  2. Quick SSH probe (1s) — if node already up, skip SOL, netconsole only.
  3. Start SOL (prep in background thread, activate immediately).
  4. select() loop: read SOL stdout + netconsole UDP simultaneously.
  5. Hand off when: ready marker seen OR first netconsole pkt + grace period.
  6. If SOL exits early: wait sol-fail-grace, then continue netconsole only.
"""

__author__      = 'Dev-team'
__copyright__   = 'Copyright 2025, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '2.4'
__maintainer__  = 'Dev-team'
__email__       = 'support@clustervision.com'
__status__      = 'Development'

import argparse
import getpass
import os
import select
import socket
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
IPMI_PREP_TIMEOUT      = 5
SSH_PROBE_TIMEOUT      = 1
REDFISH_SYSTEM_PATH    = '/redfish/v1/Systems/1'

logger = Log.init_log(log_file=LOG_FILE, log_level='info')
requests.packages.urllib3.disable_warnings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ConsoleEvent:
    def __init__(self, source, line, raw=None):
        self.source = source
        self.line   = line
        self.raw    = raw if raw is not None else line


def _info(msg):
    print(f'\033[36m[lconsole]\033[0m {msg}', flush=True)


def _warn(msg):
    print(f'\033[33m[lconsole] warning:\033[0m {msg}', flush=True)


def node_is_booted(ip, timeout=SSH_PROBE_TIMEOUT):
    """TCP probe port 22 — fast check if node OS is already up."""
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
            events.append(ConsoleEvent('NET', line, raw=data))
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
    Abstract base for all Serial-over-LAN backends.

    Subclasses must implement: start, read_events, is_alive,
    exit_summary, fileno, stop.

    run_interactive() provides a raw-terminal passthrough loop suitable
    for both IPMI and SSH-based backends; subclasses may override it.
    """

    def start(self):          raise NotImplementedError
    def read_events(self):    raise NotImplementedError
    def is_alive(self):       raise NotImplementedError
    def exit_summary(self):   raise NotImplementedError
    def fileno(self):         raise NotImplementedError
    def stop(self):           raise NotImplementedError

    def run_interactive(self):
        _info("Interactive SOL — press \033[1mCtrl+C\033[0m to exit cleanly.")
        self._generic_interactive()

    def _generic_interactive(self):
        """
        Raw-terminal passthrough: stdin → SOL process, SOL stdout → terminal.
        Ctrl+C restores terminal and calls stop() before returning.
        Works for any backend that exposes self.proc with stdin/stdout pipes.
        """
        proc = getattr(self, 'proc', None)
        if proc is None:
            raise RuntimeError('SOL process not started')

        fd_in      = sys.stdin.fileno()
        fd_sol_in  = proc.stdin.fileno()  if proc.stdin  else None
        fd_sol_out = proc.stdout.fileno() if proc.stdout else None
        old        = termios.tcgetattr(fd_in)
        try:
            tty.setraw(fd_in)
            while proc.poll() is None:
                rlist = [fd_in] + ([fd_sol_out] if fd_sol_out is not None else [])
                ready, _, _ = select.select(rlist, [], [], 0.2)
                if fd_in in ready:
                    chunk = os.read(fd_in, 256)
                    if not chunk:
                        break
                    if fd_sol_in is not None:
                        try:
                            os.write(fd_sol_in, chunk)
                        except OSError:
                            break
                if fd_sol_out is not None and fd_sol_out in ready:
                    try:
                        out = os.read(fd_sol_out, 4096)
                    except OSError:
                        break
                    if not out:
                        break
                    os.write(sys.stdout.fileno(), out)
        except KeyboardInterrupt:
            pass
        finally:
            termios.tcsetattr(fd_in, termios.TCSADRAIN, old)
            self.stop()
            print('\r\n[lconsole] SOL session ended.', flush=True)


# ---------------------------------------------------------------------------
# IPMI SOL backend
# ---------------------------------------------------------------------------

class IpmiSolBackend(SolBackend):
    """
    IPMI v2 / lanplus via ipmitool.

    SOL prep commands (sol set enabled, sol deactivate) run in a background
    thread with short timeouts — start() returns in milliseconds.
    """

    def __init__(self, nodename, bmc_ip, bmcsetup, cipher=DEFAULT_SOL_CIPHER):
        self.nodename   = nodename
        self.bmc_ip     = bmc_ip
        self.bmcsetup   = bmcsetup
        self.cipher     = cipher
        self.proc       = None
        self._fd        = None
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
        cmd = self._base_cmd() + ['sol', 'activate']
        logger.info('IPMI SOL activate: %s via %s', self.nodename, self.bmc_ip)
        self.proc = subprocess.Popen(cmd, env=self._env(),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, bufsize=0)
        self._fd = self.proc.stdout.fileno()
        os.set_blocking(self._fd, False)

    def fileno(self):
        return self._fd

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_summary(self):
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is None:
            return None
        try:
            tail = self.proc.stdout.read()
        except Exception:
            tail = b''
        msg = (tail or b'').strip().decode('utf-8', errors='replace')
        return f'IPMI SOL exited (code {rc}){": " + msg if msg else ""}'

    def read_events(self):
        events = []
        if not self.proc or not self.proc.stdout:
            return events
        while True:
            try:
                chunk = self.proc.stdout.read(4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            for line in chunk.decode('utf-8', errors='replace').splitlines():
                if line:
                    events.append(ConsoleEvent('SOL', line))
        return events

    def stop(self):
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.write(b'~.')
                self.proc.stdin.flush()
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
        self._fd  = None

    def run_interactive(self):
        _info("Interactive IPMI SOL — press \033[1mCtrl+C\033[0m "
              "or type \033[1m~.\033[0m on a new line to exit.")
        self._generic_interactive()


# ---------------------------------------------------------------------------
# Redfish SOL backend
# ---------------------------------------------------------------------------

class RedfishSolBackend(SolBackend):
    """
    Redfish-discovered SSH serial console backend.

    HOW IT WORKS
    ------------
    The DMTF Redfish standard (DSP0266) exposes serial console metadata at:

        GET /redfish/v1/Systems/{id}
        → SerialConsole.SSH.ServiceEnabled  (bool)
        → SerialConsole.SSH.Port            (int, commonly 2200)

    Once the port is known, a plain SSH subprocess is opened to the BMC:

        ssh -tt -p <port> -l <user>
            -o StrictHostKeyChecking=no
            -o UserKnownHostsFile=/dev/null
            -o PreferredAuthentications=password
            -o PubkeyAuthentication=no
            <bmc_ip>

    The BMC proxies that SSH session directly to the node's physical serial
    UART.  sshpass is used to supply the password non-interactively.

    VENDOR NOTES
    ------------
    Vendor          | Redfish path                          | Default SSH port
    --------------- | ------------------------------------- | ----------------
    OpenBMC         | /redfish/v1/Systems/system            | 2200
    iDRAC 9+        | /redfish/v1/Systems/System.Embedded.1 | 2200
    HPE iLO 5/6     | /redfish/v1/Systems/1                 | varies (check Port field)
    AMI MegaRAC     | /redfish/v1/Systems/1                 | 2200

    The system_path is tried in order from REDFISH_SYSTEM_PATHS until one
    returns HTTP 200, so this works across vendors without manual config.

    REQUIREMENTS
    ------------
    - sshpass installed on the controller (used for password auth to BMC)
    - Redfish enabled on the BMC and SerialConsole.SSH.ServiceEnabled = true
    - BMC SSH serial port reachable from the controller

    FALLBACK
    --------
    If Redfish discovery fails (e.g. BMC firmware too old, or Redfish
    disabled), set bmcsetup.redfish_port in Luna to a static port number
    and discovery will be skipped.
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
        self._fd         = None
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
        """
        Query Redfish SerialConsole.SSH to get the port.

        Also accepts a static 'redfish_port' key in bmcsetup to skip
        the Redfish query entirely (useful for firewalled BMCs or old FW).
        """
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
        """
        Build the ssh command list for serial console access.
        Uses sshpass for password auth if available; falls back to
        keyboard-interactive (works if the controller has no TTY issues).
        """
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
        # sshpass reads password from -p arg; set DISPLAY='' to suppress
        # graphical prompts on headless systems
        env['DISPLAY'] = ''

        logger.info('Launching Redfish SSH SOL for %s: port %s', self.nodename, self._ssh_port)
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        self._fd = self.proc.stdout.fileno()
        os.set_blocking(self._fd, False)

    def fileno(self):
        return self._fd

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_summary(self):
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is None:
            return None
        try:
            tail = self.proc.stdout.read()
        except Exception:
            tail = b''
        msg = (tail or b'').strip().decode('utf-8', errors='replace')
        return f'Redfish SSH SOL exited (code {rc}){": " + msg if msg else ""}'

    def read_events(self):
        events = []
        if not self.proc or not self.proc.stdout:
            return events
        while True:
            try:
                chunk = self.proc.stdout.read(4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            for line in chunk.decode('utf-8', errors='replace').splitlines():
                if line:
                    events.append(ConsoleEvent('SOL', line))
        return events

    def stop(self):
        if self.proc is None:
            return
        try:
            # Send SSH escape sequence to close the remote session gracefully
            if self.proc.stdin:
                self.proc.stdin.write(b'~.')
                self.proc.stdin.flush()
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
        self._fd  = None

    def run_interactive(self):
        _info(
            f"Interactive Redfish SSH SOL (port {self._ssh_port or '?'}) — "
            "press \033[1mCtrl+C\033[0m or type \033[1m~.\033[0m to exit."
        )
        self._generic_interactive()


# ---------------------------------------------------------------------------
# Main console controller
# ---------------------------------------------------------------------------

class LConsole:
    """
    Drives netconsole listener and/or SOL backend.

    mode='hybrid'     SOL first, auto-handoff to netconsole  (default)
    mode='netconsole' netconsole UDP only, no SOL
    mode='sol'        interactive SOL only, no netconsole
    """

    def __init__(self, nodename, details, mode='hybrid',
                 sol_backend_name='ipmi', sol_timeout=DEFAULT_SOL_TIMEOUT,
                 handoff_grace=DEFAULT_HANDOFF_GRACE, ready_marker=DEFAULT_READY_MARKER,
                 cipher=DEFAULT_SOL_CIPHER, sol_fail_grace=DEFAULT_SOL_FAIL_GRACE):
        self.nodename         = nodename
        self.details          = details
        self.mode             = mode
        self.sol_backend_name = sol_backend_name
        self.sol_timeout      = sol_timeout
        self.handoff_grace    = handoff_grace
        self.ready_marker     = ready_marker
        self.cipher           = cipher
        self.sol_fail_grace   = sol_fail_grace
        self.net               = None
        self.sol               = None
        self.net_seen          = False
        self.marker_seen       = False
        self.net_first_seen_at = None
        self.start_time        = None
        self.sol_failed_at     = None
        self.sol_fail_reported = False

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

    # --- startup ---

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
        self.net = NetconsoleListener(boot_ip)
        self.net.start()
        _info(f'netconsole-only | \033[1m{self.nodename}\033[0m ({boot_ip}) '
              f'→ UDP :{NETCONSOLE_PORT}')
        print('Waiting for kernel printk messages...\n', flush=True)

    def _start_sol_only(self, bmc_ip):
        _info(f'SOL-only | \033[1m{self.nodename}\033[0m '
              f'| BMC: {bmc_ip} | backend: {self.sol_backend_name}')
        self.sol = self._build_sol()
        self.sol.start()
        self.sol.run_interactive()   # blocks until user exits

    def _start_hybrid(self, boot_ip, bmc_ip):
        _info(f'\033[1m{self.nodename}\033[0m '
              f'| boot: {boot_ip} | BMC: {bmc_ip} '
              f'| backend: {self.sol_backend_name} | netconsole :{NETCONSOLE_PORT}')
        self.net = NetconsoleListener(boot_ip)
        self.net.start()

        print('[lconsole] checking if node is already booted...', end=' ', flush=True)
        if node_is_booted(boot_ip):
            print('SSH answered — node is up.')
            _info('Skipping SOL; netconsole-only mode.')
            _info('Note: netconsole only shows kernel printk. '
                  'If silent, netconsole-setup may have run before this session.\n')
            self.mode = 'netconsole'
            return

        print('no SSH response — node is booting.')

        if not self.details['bmc_ip'] or not self.details['bmcsetup']:
            _warn('No BMC IP or bmcsetup in Luna — cannot start SOL.')
            _info(f'Netconsole-only fallback. Waiting up to {self.sol_timeout}s.\n')
            self.mode = 'netconsole'
            return

        _info(f'Starting {self.sol_backend_name.upper()} SOL...')
        try:
            self.sol = self._build_sol()
            self.sol.start()
        except NotImplementedError as exc:
            _warn(str(exc))
            _info('Falling back to netconsole-only.\n')
            self.sol  = None
            self.mode = 'netconsole'
            return
        except Exception as exc:
            _warn(f'SOL start failed: {exc}')
            _info('Falling back to netconsole-only.\n')
            self.sol  = None
            self.mode = 'netconsole'
            return

        _info(f"SOL active | handoff on marker '{self.ready_marker}' "
              f"or first packet + {self.handoff_grace}s grace.\n")

    # --- event handling ---

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

    # --- handoff state machine ---

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
            _warn(f'No netconsole after {self.sol_timeout}s. '
                  'SOL open. Ctrl+C to exit.\n')
            self.sol_timeout = 10 ** 9

    # --- main loop ---

    def loop(self):
        if self.mode == 'sol':   # handled entirely inside start()
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
                      help='Interactive SOL only — Ctrl+C exits cleanly')

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
    )
    app.start()
    app.loop()


if __name__ == '__main__':
    main()
