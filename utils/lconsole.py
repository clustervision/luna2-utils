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
import base64
import fcntl
import getpass
import logging
import os
import random
import re
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
from utils.utils.interrupt import exit_on_interrupt

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
# Silence alone is never evidence of a fault (see the note in __init__), so this
# only ever prints — it must not reconnect. It exists because the alternative is
# a screen that has been dead for minutes and has said nothing about it.
SOL_SILENT_NOTICE_SECS   = 60.0
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


def get_redfishsetup_config(name, conf):
    """Tolerant: a 2.1 daemon has no redfishsetup endpoint, and a node without
    one is normal — the Redfish backend then falls back to bmcsetup credentials."""
    try:
        data = call_api(conf, f'/config/redfishsetup/{name}')
        return data.get('config', {}).get('redfishsetup', {}).get(name)
    except Exception as exc:
        logger.info('redfishsetup %s not resolvable (%s) — using bmcsetup credentials',
                    name, exc)
        return None


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
    redfishsetup_name = node.get('redfishsetup') or group.get('redfishsetup')
    redfishsetup      = (get_redfishsetup_config(redfishsetup_name, conf)
                         if redfishsetup_name else None)
    return {
        'node': node, 'group': group,
        'boot_ip': boot_ip, 'bmc_ip': bmc_ip,
        'bmcsetup_name': bmcsetup_name, 'bmcsetup': bmcsetup,
        'redfishsetup_name': redfishsetup_name, 'redfishsetup': redfishsetup,
    }


# ---------------------------------------------------------------------------
# Console-port detection & three-layer diagnosis (--detect-port / --diagnose)
#
# Which serial port the firmware redirects to is a BIOS *setting*, not a
# board property — two identical boards can differ, and it changes the moment
# someone edits the setup screen. So it can only be read off the machine:
#   1. ACPI SPCR — the firmware declaring its redirection target and address.
#      Authoritative where present (Linux itself consumes it to pick console
#      and earlycon). Absence usually means redirection is off, or the
#      firmware simply does not publish the table.
#   2. /proc/tty/driver/serial — the address-to-ttySX map with tx counters;
#      the port whose tx climbs is the one actually carrying console output.
# Both are read over SSH. No SOL session is needed or taken, so this never
# competes with a live lconsole or sol-grab session.
# ---------------------------------------------------------------------------

UART_ADDRESSES = {0x3f8: 'ttyS0', 0x2f8: 'ttyS1', 0x3e8: 'ttyS2', 0x2e8: 'ttyS3'}


def _node_ssh(host, cmd, timeout=12):
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
         '-o', 'StrictHostKeyChecking=no', f'root@{host}', cmd],
        capture_output=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def parse_spcr(raw):
    """Base address from a raw SPCR table, or None if absent/truncated.

    Layout: 36-byte ACPI header, interface type at 36, then a Generic Address
    Structure at 40 whose 8-byte address sits at offset 44, little-endian.
    """
    if len(raw) < 52:
        return None
    return int.from_bytes(raw[44:52], 'little')


def parse_serial_driver(text):
    """/proc/tty/driver/serial -> {ttySX: (io_address, tx)} for real UARTs."""
    ports = {}
    for line in text.splitlines():
        m = re.match(r'^(\d+):\s+uart:(\S+)\s+port:([0-9A-Fa-f]+)\s+irq:\S+'
                     r'(?:\s+tx:(\d+))?', line.strip())
        if not m or m.group(2) == 'unknown':
            continue
        ports[f'ttyS{m.group(1)}'] = (int(m.group(3), 16), int(m.group(4) or 0))
    return ports


def detect_console_port(host):
    """Return (port, io_address, evidence) for the node's console UART."""
    rc, out, err = _node_ssh(
        host,
        'base64 /sys/firmware/acpi/tables/SPCR 2>/dev/null; echo ---; '
        'cat /proc/tty/driver/serial')
    if rc != 0:
        detail = err.decode(errors='replace').strip() or 'connection failed'
        raise RuntimeError(f'cannot SSH to {host}: {detail}')
    b64, _, serial_txt = out.decode(errors='replace').partition('---\n')
    if b64.strip():
        try:
            addr = parse_spcr(base64.b64decode(b64))
        except ValueError:
            addr = None
        if addr in UART_ADDRESSES:
            return UART_ADDRESSES[addr], addr, 'SPCR (the firmware declares it)'
    first = parse_serial_driver(serial_txt)
    time.sleep(3)
    rc, out, _ = _node_ssh(host, 'cat /proc/tty/driver/serial')
    second = parse_serial_driver(out.decode(errors='replace')) if rc == 0 else first
    moving = [t for t, (_, tx) in second.items() if tx > first.get(t, (0, 0))[1]]
    if len(moving) == 1:
        port = moving[0]
        return port, second[port][0], 'tx counter climbing (console output observed live)'
    used = sorted(((tx, t, a) for t, (a, tx) in second.items() if tx > 0), reverse=True)
    if used:
        tx, port, addr = used[0]
        return port, addr, f'historical tx ({tx} bytes ever sent; nothing moved during the check)'
    raise RuntimeError('no SPCR and no serial port has ever transmitted — '
                       'console redirection is probably off in the BIOS')


def suggested_kerneloptions(port, addr, baud=115200):
    return (f'earlycon=uart8250,io,0x{addr:x},{baud}n8 console=tty0 '
            f'console=uart,io,0x{addr:x},{baud}n8 console={port},{baud}n8')



def _maybe_b64(value):
    """The daemon transports free-text fields like kerneloptions base64-encoded
    over the raw API (the luna CLI decodes them before display). A real options
    string has spaces/commas, so it can never false-positive as base64."""
    if not value:
        return value or ''
    try:
        decoded = base64.b64decode(value, validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return value
    return decoded if decoded.isprintable() else value


def analyze_kerneloptions(opts):
    """What a kernel options string means for serial console visibility."""
    tokens = (opts or '').split()
    consoles = [t[len('console='):] for t in tokens if t.startswith('console=')]
    serial = [c for c in consoles if not c.startswith('tty0')]
    named = [c.split(',')[0] for c in serial if c.startswith('ttyS')]
    dev_console = consoles[-1].split(',')[0] if consoles else None
    baud = None
    for c in reversed(serial):
        m = re.search(r'(\d{4,6})', ','.join(c.split(',')[1:]))
        if m:
            baud = int(m.group(1))
            break
    warnings = []
    if not serial:
        warnings.append('no serial console= at all — nothing will ever reach SOL')
    for c in serial:
        if c.startswith('ttyS') and ',' not in c:
            warnings.append(f'console={c} has no baud — the kernel defaults to '
                            f'9600, which will not match a 115200-class SOL')
    for t in tokens:
        if t.startswith('earlycon=') and not t.startswith(('earlycon=uart',
                                                             'earlycon=pl011')):
            warnings.append(f'{t} is not a valid earlycon spec — the kernel '
                            f'ignores it (use earlycon=uart8250,io,<addr>,<baud>n8, '
                            f'or bare earlycon on SPCR firmware)')
    if len(named) > 1:
        warnings.append('more than one named ttyS console — the last one steals '
                        '/dev/console, and on EL8 the second never binds at all')
    if serial and not any(t.startswith('earlycon') for t in tokens):
        warnings.append('no earlycon — output from before the serial driver binds is '
                        'lost, so an early crash looks like a node that never started')
    return {'consoles': consoles, 'dev_console': dev_console, 'named': named,
            'baud': baud, 'warnings': warnings}


def _sol_info(bmc_ip, bmcsetup, cipher):
    """(enabled, bit_rate_kbps) from 'ipmitool sol info'."""
    env = os.environ.copy()
    env['IPMI_PASSWORD'] = bmcsetup['password']
    cmd = ['ipmitool', '-E', '-I', 'lanplus', '-C', str(cipher),
           '-H', bmc_ip, '-U', bmcsetup['username'], 'sol', 'info']
    result = subprocess.run(cmd, env=env, capture_output=True, timeout=20)
    if result.returncode != 0:
        detail = result.stderr.decode(errors='replace').strip()
        raise RuntimeError(detail or 'ipmitool sol info failed')
    enabled, rate = None, None
    for line in result.stdout.decode(errors='replace').splitlines():
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'enabled':
            enabled = value.lower() == 'true'
        elif 'bit rate' in key:
            if rate is None or key.startswith('volatile'):
                rate = value
    return enabled, rate


def run_detect_port(nodename, details, cipher=DEFAULT_SOL_CIPHER, baud=None):
    host = details['boot_ip']
    print(f'[lconsole] Probing {nodename} ({host}) over SSH — no SOL session is taken.')
    port, addr, evidence = detect_console_port(host)
    baud_source = 'set with --baud'
    if not baud and details.get('bmc_ip') and details.get('bmcsetup'):
        try:
            _, rate = _sol_info(details['bmc_ip'], details['bmcsetup'], cipher)
            baud = int(float(rate) * 1000)
            baud_source = 'reported by the BMC'
        except Exception:
            pass
    if not baud:
        baud, baud_source = 115200, 'default'
    options = suggested_kerneloptions(port, addr, baud)
    print(f'''
  Console port : {port}  (io 0x{addr:x})
  Evidence     : {evidence}
  Baud         : {baud}  ({baud_source})

  Kernel options for this hardware:
    {options}

  Apply to the image (or a group/node override):
    luna osimage change --quick-kerneloptions "{options}" <osimage>
''')
    return 0


def run_diagnose(nodename, details, conf, cipher, baud=None):
    failures = 0
    node, group = details['node'], details['group']
    image_name = node.get('osimage') or group.get('osimage')
    image_kopts = ''
    if image_name:
        try:
            data = call_api(conf, f'/config/osimage/{image_name}')
            image = data.get('config', {}).get('osimage', {}).get(image_name) or {}
            image_kopts = _maybe_b64(image.get('kerneloptions'))
        except Exception as exc:
            _warn(f'Could not fetch osimage {image_name}: {exc}')
    kopts = (_maybe_b64(node.get('kerneloptions')) or
             _maybe_b64(group.get('kerneloptions')) or image_kopts)
    override = _maybe_b64(node.get('kerneloptions')) or _maybe_b64(group.get('kerneloptions'))
    source = ('node/group override' if override and override != image_kopts
              else f'osimage {image_name}')
    print(f'[lconsole] Diagnosing {nodename} — a blank console hides one of three layers:\n')

    layer_a = analyze_kerneloptions(kopts)
    print(f'  (a) Kernel options ({source}):')
    print(f'      {kopts or "(none)"}')
    if layer_a['dev_console']:
        print(f'      /dev/console (installer, systemd, login prompt) → '
              f'{layer_a["dev_console"]}  (the last console= wins)')
    for warning in layer_a['warnings']:
        failures += 1
        print(f'      WARN: {warning}')
    if not layer_a['warnings']:
        print('      OK')

    print('\n  (b) BMC Serial-over-LAN:')
    bmc_baud = None
    try:
        enabled, rate = _sol_info(details['bmc_ip'], details['bmcsetup'], cipher)
        print(f'      Enabled: {enabled}   Bit rate: {rate or "?"} kbps')
        try:
            bmc_baud = int(float(rate) * 1000)
        except (TypeError, ValueError):
            pass
        if enabled is False:
            failures += 1
            print('      WARN: SOL is disabled on the BMC — enable it there '
                  '(lconsole also tries once per session)')
        if bmc_baud and layer_a['baud'] and abs(bmc_baud - layer_a['baud']) > 1:
            failures += 1
            print(f'      WARN: the BMC runs {rate} kbps but the kernel options say '
                  f'{layer_a["baud"]} baud — expect garbage or silence')
    except Exception as exc:
        print(f'      Could not query over IPMI: {exc}')
        print('      On a Redfish-only BMC, check Managers/<id>/SerialInterfaces by hand.')

    print('\n  (c) The port the node actually uses (SPCR / tx counters):')
    try:
        port, addr, evidence = detect_console_port(details['boot_ip'])
        print(f'      {port} (io 0x{addr:x}) — {evidence}')
        if layer_a['dev_console'] and layer_a['dev_console'] not in ('tty0', port):
            failures += 1
            best_baud = baud or bmc_baud or layer_a['baud'] or 115200
            print(f'      WARN: the kernel options send /dev/console to '
                  f'{layer_a["dev_console"]}, but this machine\'s console port is {port}.')
            print('      Fix with:')
            print(f'        luna osimage change --quick-kerneloptions '
                  f'"{suggested_kerneloptions(port, addr, best_baud)}" {image_name}')
        elif layer_a['dev_console'] == port:
            print('      OK — matches the kernel options')
    except Exception as exc:
        print(f'      Unknown: {exc}')
        print('      A booted, SSH-reachable node is needed; TRIX-2047 will record '
              'this in the node inventory at install time.')

    print(f'\n[lconsole] Diagnosis: {failures} problem(s) found.'
          if failures else '\n[lconsole] Diagnosis: no problems found.')
    return 1 if failures else 0


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


class RedfishConsoleIsIpmi(RuntimeError):
    """The BMC's Redfish declares IPMI as its (only) serial console transport."""


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

    MANAGER_PATHS = [
        '/redfish/v1/Managers/Self',
        '/redfish/v1/Managers/1',
        '/redfish/v1/Managers/bmc',
        '/redfish/v1/Managers/iDRAC.Embedded.1',
    ]

    def __init__(self, nodename, bmc_ip, bmcsetup, system_path=None,
                 redfishsetup=None):
        super().__init__(nodename, bmc_ip, bmcsetup)
        self._system_path_override = system_path
        self._ssh_port = None
        # Luna's redfishsetup is the account and endpoint provisioned FOR
        # Redfish; prefer it wherever it is set, fall back to bmcsetup so a
        # 2.1 daemon or an unconfigured node keeps working unchanged.
        rf = redfishsetup or {}
        account = next((a for a in rf.get('accounts') or []
                        if a.get('username') and a.get('password')), None)
        if account:
            self._rf_user, self._rf_pass = account['username'], account['password']
            self._cred_source = ("redfishsetup account "
                                 + (account.get('name') or account['username']))
        else:
            self._rf_user, self._rf_pass = bmcsetup['username'], bmcsetup['password']
            self._cred_source = 'bmcsetup credentials'
        scheme = rf.get('scheme') or 'https'
        port   = rf.get('port')
        self._rf_base   = f"{scheme}://{bmc_ip}" + (f":{port}" if port else '')
        self._rf_verify = bool(rf.get('verify'))
        logger.info('Redfish endpoint %s for %s, using %s',
                    self._rf_base, nodename, self._cred_source)

    def _redfish_get(self, path):
        resp = requests.get(
            f'{self._rf_base}{path}',
            auth=(self._rf_user, self._rf_pass),
            verify=self._rf_verify,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _walk_collection(self, collection):
        """Member paths of a Redfish collection — the vendor-proof discovery.
        An empty list (unreachable, unparseable, no members) falls back to the
        known per-vendor paths, and --redfish-path remains the manual escape."""
        try:
            data = self._redfish_get(collection)
        except requests.RequestException:
            return []
        return [m.get('@odata.id') for m in data.get('Members') or []
                if m.get('@odata.id')]

    def _discover_system_path(self):
        if self._system_path_override:
            return self._system_path_override
        members = self._walk_collection('/redfish/v1/Systems')
        if members:
            logger.debug('Redfish Systems collection members: %s', members)
            return members[0]
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
            f'No Redfish System resource found on {self.bmc_ip}: the Systems '
            f'collection lists no members and none of the usual paths answer '
            f'({", ".join(self.SYSTEM_PATHS)}). If you know the path, pass '
            f'--redfish-path /redfish/v1/Systems/<id>.')

    def _connect_types(self, system_data):
        """SerialConsole.ConnectTypesSupported from the System, else the Manager.

        AMI MegaRAC (GIGABYTE R181 class) publishes it on Managers/Self and
        offers only IPMI there — meaning IPMI SOL *is* that BMC's sanctioned
        serial console, and there is nothing SSH-shaped to discover.
        """
        types = {str(t).upper()
                 for t in (system_data.get('SerialConsole') or {})
                          .get('ConnectTypesSupported') or []}
        if types:
            return types
        for path in (self._walk_collection('/redfish/v1/Managers')
                     or self.MANAGER_PATHS):
            try:
                manager = self._redfish_get(path)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                return types
            except requests.RequestException:
                return types
            found = {str(t).upper()
                     for t in (manager.get('SerialConsole') or {})
                              .get('ConnectTypesSupported') or []}
            if found:
                return found
        return types

    def _discover_ssh_port(self):
        static = self.bmcsetup.get('redfish_port')
        if static:
            logger.info('Using static Redfish SSH port %s for %s', static, self.nodename)
            return int(static)

        system_path = self._discover_system_path()
        data        = self._redfish_get(system_path)
        ssh_info    = data.get('SerialConsole', {}).get('SSH', {})

        if not ssh_info:
            types = self._connect_types(data)
            if 'IPMI' in types and 'SSH' not in types:
                raise RedfishConsoleIsIpmi(
                    f'Redfish on {self.bmc_ip} declares IPMI as its serial console '
                    f'transport (ConnectTypesSupported = {sorted(types)}) — this '
                    f'BMC has no SSH console.')
            raise RuntimeError(
                f'No SerialConsole.SSH property in Redfish response from '
                f'{self.bmc_ip}{system_path} (ConnectTypesSupported = '
                f'{sorted(types) if types else "unpublished"}). '
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
        _info(f'Querying Redfish on {self.bmc_ip} for its serial console...')
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
            '-l', self._rf_user,
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
            cmd = ['sshpass', '-p', self._rf_pass] + cmd
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
                 sol_backend_name='ipmi', redfish_path=None,
                 cipher=DEFAULT_SOL_CIPHER,
                 sol_fail_grace=DEFAULT_SOL_FAIL_GRACE,
                 sol_escape=DEFAULT_SOL_ESCAPE, debug=False):
        self.nodename         = nodename
        self.details          = details
        self.sol_backend_name = sol_backend_name
        self.redfish_path     = redfish_path
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
        # prolonged-silence notice (see SOL_SILENT_NOTICE_SECS). Armed whenever
        # SOL starts, cleared by every byte received.
        self._sol_last_rx_at        = None
        self._sol_silent_noted      = False

        # --debug: periodic heartbeat proving (or disproving) that bytes are
        # actually arriving, so "screen is blank" can be told apart from
        # "bytes arrive but nothing visible renders" without guessing.
        self._sol_rx_total     = 0
        self._sol_rx_interval  = 0
        self._debug_last       = None

    # ── presentation ──

    def _session_line(self):
        bmc_ip = self.details['bmc_ip'] or 'N/A'
        escape = f'exit: {self.sol_escape}.. or Ctrl-C twice'
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
            return RedfishSolBackend(self.nodename, bmc_ip, bmcsetup,
                                     system_path=self.redfish_path,
                                     redfishsetup=self.details.get('redfishsetup'))
        raise RuntimeError(f'Unknown SOL backend: {self.sol_backend_name}')

    def start(self):
        _info(f'Starting {self.sol_backend_name.upper()} SOL...')
        self.sol = self._build_sol()
        try:
            self.sol.start(winsize=terminal_size())
        except RedfishConsoleIsIpmi as exc:
            _info(str(exc))
            _info('Falling back to IPMI SOL — the transport this BMC declares via Redfish.')
            self.sol_backend_name = 'ipmi'
            self.sol = self._build_sol()
            self.sol.start(winsize=terminal_size())
        # after the fallback, so the notice times whichever transport actually started
        self._arm_silence_notice()

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
        self._arm_silence_notice()
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

    def _arm_silence_notice(self):
        self._sol_last_rx_at   = time.monotonic()
        self._sol_silent_noted = False

    def _maybe_note_silence(self):
        """Say, once, that nothing has arrived for a long time.

        This deliberately does NOT reconnect. An idle console is legitimately
        silent forever, which is why staleness is judged against operator input
        rather than silence — reconnecting on silence would restart the session
        under anyone sitting at a quiet login prompt. But the same silence is
        also what a console looks like after another SOL session has taken the
        payload: ipmitool stays alive, no error is printed, and nothing arrives
        again until the operator happens to type. Measured on real hardware, a
        passively watched console stays blank indefinitely and then recovers
        within ~5s of the first keystroke. So the gap is not recovery, it is
        that nobody is told there is anything to recover from."""
        if self._sol_last_rx_at is None or self._sol_silent_noted:
            return
        if time.monotonic() - self._sol_last_rx_at < SOL_SILENT_NOTICE_SECS:
            return
        self._sol_silent_noted = True
        self._message(
            f'nothing received for {SOL_SILENT_NOTICE_SECS:.0f}s — the console may just '
            'be idle, or another SOL session may have taken it over. Press Enter to '
            're-check: no reply within '
            f'{SOL_STALE_AFTER_INPUT:.0f}s reconnects automatically.')

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
                        self._arm_silence_notice()
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

                self._maybe_note_silence()
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
            'Live Serial-over-LAN console for a compute node, through its BMC.\n\n'
            '  lconsole node001                     open the console (IPMI SOL)\n'
            '  lconsole node001 --sol-backend redfish\n'
            '  lconsole node001 --detect-port       which serial port is the console?\n'
            '  lconsole node001 --diagnose          why is my console blank?\n\n'
            'Exit a session with <escape>.. at the start of a line (default: !..),\n'
            'or press Ctrl-C twice within one second.\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('nodename', help='compute node name, e.g. node001')

    parser.add_argument('--sol-backend', default='ipmi', choices=['ipmi', 'redfish'],
                        help='SOL transport; redfish falls back to IPMI when the '
                             'BMC declares that as its console (default: %(default)s)')
    parser.add_argument('--sol-cipher', type=int, default=DEFAULT_SOL_CIPHER,
                        metavar='N',
                        help='IPMI cipher suite (default: %(default)s)')
    parser.add_argument('--sol-fail-grace', type=int, default=DEFAULT_SOL_FAIL_GRACE,
                        metavar='SEC',
                        help='seconds between reconnect attempts while SOL is down '
                             '(default: %(default)s)')
    parser.add_argument('--sol-escape', default=DEFAULT_SOL_ESCAPE,
                        metavar='CHAR',
                        help='escape character for exit, typed as <char>.. at the '
                             'start of a line (default: %(default)r)')
    parser.add_argument('--detect-port', action='store_true',
                        help='report which serial port carries the node\'s console '
                             '(ACPI SPCR, then tx counters, over SSH) and print the '
                             'kernel options to set — no console is opened')
    parser.add_argument('--diagnose', action='store_true',
                        help='check the three layers a blank console hides: kernel '
                             'options, BMC SOL settings, and the port the node '
                             'actually uses — no console is opened')
    parser.add_argument('--baud', type=int, default=None, metavar='BAUD',
                        help='serial speed for suggested kernel options (default: '
                             'what the BMC reports, else 115200)')
    parser.add_argument('--redfish-path', default=None, metavar='PATH',
                        help='Redfish System resource path, e.g. '
                             '/redfish/v1/Systems/Self, when automatic discovery '
                             'cannot find it')
    parser.add_argument('--debug', action='store_true',
                        help='periodic byte-count heartbeat — tells a truly silent '
                             'link apart from bytes arriving but not rendering')
    return parser.parse_args(argv)


def main(argv=None):
    """Entry point; Ctrl-C ends the run with a message instead of a traceback."""
    return exit_on_interrupt(_main, argv)


def _main(argv=None):
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

    if args.diagnose or args.detect_port:
        try:
            if args.diagnose:
                return run_diagnose(args.nodename, details, conf, args.sol_cipher,
                                    baud=args.baud)
            return run_detect_port(args.nodename, details, cipher=args.sol_cipher,
                                   baud=args.baud)
        except Exception as exc:
            logger.exception('%s failed for %s',
                             'diagnose' if args.diagnose else 'detect-port', args.nodename)
            print(f'lconsole: {exc}', file=sys.stderr)
            return 1

    app = LConsole(
        nodename         = args.nodename,
        details          = details,
        sol_backend_name = args.sol_backend,
        redfish_path     = args.redfish_path,
        cipher           = args.sol_cipher,
        sol_fail_grace   = args.sol_fail_grace,
        sol_escape       = args.sol_escape,
        debug            = args.debug,
    )
    try:
        app.start()
    except KeyboardInterrupt:
        # a half-started SOL child must not outlive us
        if app.sol is not None:
            app.sol.stop()
        raise
    except Exception as exc:
        logger.exception('starting console for %s failed', args.nodename)
        print(f'lconsole: {exc}', file=sys.stderr)
        return 1
    app.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
