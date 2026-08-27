#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

# This code is part of the TrinityX software suite
# Copyright (C) 2026  ClusterVision Solutions b.v.
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
sol-grab — on-demand, ephemeral SOL capture for the nodeboot GUI's console
panel. SOL is an exclusive, stateful IPMI session per node — holding one open
per node, fleet-wide, would mean thousands of concurrent BMC logins. So SOL
is grabbed only when asked, held only for SOL_GRAB_DURATION seconds, then
dropped.

Runs on the provisioning controller (BMC network access), one small HTTP
endpoint: GET /grab/<node> -> {"lines": [...]} or {"error": "..."}.

Concurrency is bounded two ways:
  - a fixed-size worker pool (MAX_WORKERS) — the rest queue rather than each
    opening their own BMC connection at once.
  - a per-node lock — two callers grabbing the same node join the one grab
    already in flight instead of racing two SOL sessions against one BMC
    (which only allows one SOL client at a time anyway).

Before grabbing, checks for an already-running interactive `lconsole <node>`
session and refuses if found — a human's live debug session must never be
silently kicked off by this.
"""

import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict

from flask import Flask, jsonify, abort

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.lconsole import (  # noqa: E402
    Ini, IpmiSolBackend, RedfishSolBackend, resolve_node_details, terminal_size,
    LUNA_CONFIG_PATH, DEFAULT_SOL_CIPHER,
)

PORT             = int(os.environ.get('SOL_GRAB_PORT', 6667))
MAX_WORKERS      = int(os.environ.get('SOL_GRAB_MAX_WORKERS', 15))
GRAB_DURATION    = float(os.environ.get('SOL_GRAB_DURATION_SECS', 8))
LOG_DIR          = os.environ.get('SOL_GRAB_LOG_DIR', '/var/log/luna/console')
MAX_LINES        = int(os.environ.get('SOL_GRAB_MAX_LINES', 500))
SOL_BACKEND_NAME = os.environ.get('SOL_GRAB_BACKEND', 'ipmi')

_NODE_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
_inflight: Dict[str, Future] = {}
_inflight_lock = threading.Lock()


def live_session_running(node: str) -> bool:
    """True if an interactive `lconsole <node> ...` process is already up —
    grabbing SOL underneath it would silently steal the operator's session."""
    try:
        out = subprocess.run(['pgrep', '-af', 'lconsole'], capture_output=True,
                              text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False  # can't tell -> don't block the grab over a check failure
    pattern = re.compile(rf'\blconsole\s+{re.escape(node)}\b')
    return any(pattern.search(line) for line in out.splitlines())


def _build_backend(node: str):
    conf = Ini.read_ini(ini_file=LUNA_CONFIG_PATH)
    details = resolve_node_details(node, conf)
    if not details['bmc_ip']:
        raise RuntimeError(f'Node {node} has no BMC IP in Luna')
    if not details['bmcsetup']:
        raise RuntimeError(f'Node {node} has no bmcsetup in Luna')
    if SOL_BACKEND_NAME == 'redfish':
        return RedfishSolBackend(node, details['bmc_ip'], details['bmcsetup'])
    return IpmiSolBackend(node, details['bmc_ip'], details['bmcsetup'], cipher=DEFAULT_SOL_CIPHER)


def _grab_lines(node: str) -> list:
    """Runs in a worker thread: open SOL, collect output for GRAB_DURATION
    seconds, close it. Never left running past that window."""
    backend = _build_backend(node)
    backend.start(winsize=terminal_size())
    try:
        buf = bytearray()
        deadline = time.monotonic() + GRAB_DURATION
        while time.monotonic() < deadline:
            chunk = backend.read()
            if chunk:
                buf += chunk
            else:
                time.sleep(0.2)
    finally:
        backend.stop()
    text = buf.decode('utf-8', errors='replace')
    lines = [ln.rstrip('\r') for ln in text.split('\n') if ln.strip()]
    return lines[-MAX_LINES:]


def _write_log(node: str, lines: list):
    path = os.path.join(LOG_DIR, f'{node}.sol.log')
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + ('\n' if lines else ''))
    except OSError as exc:
        sys.stderr.write(f'sol-grab: could not write {path}: {exc}\n')


def _do_grab(node: str) -> dict:
    try:
        lines = _grab_lines(node)
        _write_log(node, lines)
        return {'node': node, 'lines': lines}
    except Exception as exc:  # noqa: BLE001 — surfaced to the GUI, not a 500
        return {'node': node, 'lines': [], 'error': str(exc)}


@app.route('/grab/<node>', methods=['GET'])
def grab(node):
    if not _NODE_NAME_RE.match(node):
        abort(404)

    if live_session_running(node):
        return jsonify({'node': node, 'lines': [],
                         'error': 'console already open in an active lconsole session'})

    with _inflight_lock:
        fut = _inflight.get(node)
        if fut is None:
            fut = executor.submit(_do_grab, node)
            _inflight[node] = fut

    try:
        result = fut.result(timeout=GRAB_DURATION + 15)
    except TimeoutError:
        result = {'node': node, 'lines': [], 'error': 'SOL grab timed out'}
    finally:
        with _inflight_lock:
            if _inflight.get(node) is fut:
                del _inflight[node]

    return jsonify(result)


@app.route('/healthz', methods=['GET'])
def healthz():
    return jsonify({'status': 'ok', 'max_workers': MAX_WORKERS})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, threaded=True)
