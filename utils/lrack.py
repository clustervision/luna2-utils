#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

# This code is part of the TrinityX software suite
# Copyright (C) 2025  ClusterVision Solutions b.v.
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
lrack manages racks and the placement of devices inside them, mirroring the
TrinityX OOD Rack View from the command line. It is a client of the luna2-daemon
rack API (/config/rack and /config/rack/inventory).
"""

__author__      = 'Antoine Schonewille'
__copyright__   = 'Copyright 2025, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '2.1'
__maintainer__  = 'Dev-team'
__email__       = 'antoine.schonewille@clustervision.com'
__status__      = 'Development'

#VERSION: 0.1.0

import os
import sys
import json
import logging
import getpass
import argparse

import requests
from requests import Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry
import hostlist
from prettytable import PrettyTable
from termcolor import colored

import argcomplete

from utils.utils.log import Log
from utils.utils.ini import Ini
from utils.utils.token import Token

INI_FILE = os.environ.get('LRACK_INI', '/trinity/local/luna/utils/config/luna.ini')
LOG_FILE = '/var/log/luna/lrack.log'
DEVICE_TYPES = ['node', 'switch', 'otherdevices', 'controller']
ORIENTATIONS = ['front', 'back']
ORDERS = ['ascending', 'descending']
DEFAULT_SIZE = 42
DEFAULT_HEIGHT = 1
SUBCOMMANDS = ('list', 'show', 'add', 'change', 'rename', 'remove', 'place',
               'unplace', 'resize', 'orient', 'inventory', 'pool')

urllib3.disable_warnings()
logger = logging.getLogger('lrack')


def _color(text, color=None, attrs=None):
    """Colour text only when writing to a terminal, so pipes stay clean."""
    if sys.stdout.isatty():
        return colored(text, color, attrs=attrs)
    return text


def info(message):
    """Print an informational message."""
    print(_color('INFO', 'green', ['bold']) + f' :: {message}')


def warn(message):
    """Print a warning to stderr."""
    sys.stderr.write(_color('WARNING', 'yellow', ['bold']) + f' :: {message}\n')


def error_exit(message, code=1):
    """Print an error to stderr and stop."""
    sys.stderr.write(_color('ERROR', 'red', ['bold']) + f' :: {message}\n')
    logger.error(message)
    sys.exit(code)


def expand_devices(expression):
    """Expand a hostlist expression (node[001-010],switch01) into a name list."""
    try:
        return hostlist.expand_hostlist(expression)
    except hostlist.BadHostlist:
        error_exit(f'{expression} is not a valid host/device list')


class Rest():
    """Minimal REST client for the luna2-daemon rack API."""

    def __init__(self):
        config = Ini.read_ini(ini_file=INI_FILE)
        self.username = config['USERNAME']
        self.password = config['PASSWORD']
        self.verify = config['VERIFY_CERTIFICATE']
        self.base = f"{config['PROTOCOL']}://{config['ENDPOINT']}"
        self.timeout = 20
        self.token = None
        self.session = Session()
        retries = Retry(
            total=6,
            backoff_factor=0.2,
            status_forcelist=[502, 503, 504],
            allowed_methods={'GET', 'POST'}
        )
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def _headers(self):
        if self.token is None:
            self.token = Token.get_token(
                username=self.username, password=self.password,
                protocol=self.base.split('://')[0], endpoint=self.base.split('://')[1],
                verify_certificate=self.verify)
        return {'x-access-tokens': self.token, 'Content-Type': 'application/json'}

    def get(self, route):
        """GET a route and return parsed JSON, or None when not found."""
        url = f'{self.base}/{route}'
        logger.debug(f'GET {url}')
        try:
            response = self.session.get(url, headers=self._headers(),
                                        timeout=self.timeout, verify=self.verify)
        except requests.exceptions.ConnectionError:
            error_exit(f'request timeout while reaching {url}')
        if response.status_code == 200:
            return response.json()
        return None

    def post(self, route, payload):
        """POST a payload and return (ok, message)."""
        url = f'{self.base}/{route}'
        logger.debug(f'POST {url} {payload}')
        try:
            response = self.session.post(url, json=payload, headers=self._headers(),
                                         timeout=self.timeout, verify=self.verify)
        except requests.exceptions.ConnectionError:
            error_exit(f'request timeout while reaching {url}')
        return self._result(response)

    def get_action(self, route):
        """GET a daemon action route (used for _delete) and return (ok, message)."""
        url = f'{self.base}/{route}'
        logger.debug(f'GET {url}')
        try:
            response = self.session.get(url, headers=self._headers(),
                                        timeout=self.timeout, verify=self.verify)
        except requests.exceptions.ConnectionError:
            error_exit(f'request timeout while reaching {url}')
        return self._result(response)

    @staticmethod
    def _result(response):
        ok = response.status_code in [200, 201, 204]
        message = ''
        try:
            body = response.json()
            message = body.get('message', body)
        except requests.exceptions.JSONDecodeError:
            message = response.text
        return ok, message


# ---------------------------------------------------------------------------
# helpers shared by the command handlers
# ---------------------------------------------------------------------------

def fetch_racks(rest, name=None):
    """Return the {name: rack} mapping from the daemon."""
    route = f'config/rack/{name}' if name else 'config/rack'
    data = rest.get(route)
    if not data:
        return {}
    return data.get('config', {}).get('rack', {})


def fetch_inventory(rest, subset=None):
    """Return the inventory list from the daemon."""
    route = f'config/rack/inventory/{subset}' if subset else 'config/rack/inventory'
    data = rest.get(route)
    if not data:
        return []
    return data.get('config', {}).get('rack', {}).get('inventory', [])


def device_index(rest):
    """Map every known device name to its inventory record (for type lookups)."""
    index = {}
    for device in fetch_inventory(rest):
        index[device['name']] = device
    return index


def lookup_type(index, name):
    """Resolve a device name to its type, erroring out when unknown."""
    if name not in index:
        error_exit(f'{name} is not a known device in the inventory')
    return index[name]['type']


def occupied_slots(devices, ignore=None):
    """Build a {u_position: device_name} map from placed devices."""
    ignore = ignore or set()
    slots = {}
    for device in devices:
        if device['name'] in ignore:
            continue
        position = device.get('position')
        if not position:
            continue
        position = int(position)
        height = int(device.get('height') or DEFAULT_HEIGHT)
        for unit in range(position, position + height):
            slots[unit] = device['name']
    return slots


def find_free(slots, height, size, order):
    """Find the first free U run of `height`, following the rack numbering order."""
    starts = range(1, size - height + 2)
    if order == 'descending':
        starts = range(size - height + 1, 0, -1)
    for start in starts:
        if all((start + offset) not in slots for offset in range(height)):
            return start
    return None


def render_rack(name, rack):
    """Render a rack as a Rack-View-style ASCII elevation."""
    size = int(rack.get('size') or DEFAULT_SIZE)
    order = rack.get('order') or 'ascending'
    devices = rack.get('devices', [])
    occupies = {}
    for device in devices:
        position = device.get('position')
        if not position:
            continue
        position = int(position)
        height = int(device.get('height') or DEFAULT_HEIGHT)
        for unit in range(position, position + height):
            occupies[unit] = device

    width = 44
    border = '+----+' + '-' * (width + 2) + '+'
    header = (f"{name}  (site: {rack.get('site') or '-'}, room: {rack.get('room') or '-'}, "
              f"{size}U, {order})")
    lines = ['', _color(header, 'cyan', ['bold']), border]
    units = range(size, 0, -1) if order != 'descending' else range(1, size + 1)
    for unit in units:
        device = occupies.get(unit)
        if device is None:
            cell = ' ' * width
        else:
            if int(device.get('position')) == unit:
                height = int(device.get('height') or DEFAULT_HEIGHT)
                text = (f"{device['name']}  {device['type']}  "
                        f"{device.get('vendor') or '-'}  ({height}U) "
                        f"{device.get('orientation') or 'front'}")
            else:
                text = '  ...'
            cell = text[:width].ljust(width)
            cell = _color(cell, 'yellow' if device.get('orientation') == 'back' else 'green')
        lines.append(f"| {unit:>2} | {cell} |")
    lines.append(border)
    used = sum(int(d.get('height') or DEFAULT_HEIGHT) for d in devices)
    lines.append(f"used {used}U / free {size - used}U   devices: {len(devices)}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------

def cmd_list(args, rest):
    """List all racks with their utilisation."""
    racks = fetch_racks(rest)
    if args.raw:
        print(json.dumps({'config': {'rack': racks}}, indent=4))
        return
    if not racks:
        info('no racks defined')
        return
    table = PrettyTable(['name', 'site', 'room', 'size (U)', 'used (U)', 'free (U)', 'devices'])
    table.align = 'l'
    for name, rack in racks.items():
        size = int(rack.get('size') or DEFAULT_SIZE)
        used = sum(int(d.get('height') or DEFAULT_HEIGHT) for d in rack.get('devices', []))
        table.add_row([name, rack.get('site') or '-', rack.get('room') or '-',
                       size, used, size - used, len(rack.get('devices', []))])
    print(table)


def cmd_show(args, rest):
    """Show one or more racks and the devices placed in them."""
    names = args.rack or list(fetch_racks(rest).keys())
    if args.raw:
        result = {name: fetch_racks(rest, name).get(name, {}) for name in names}
        print(json.dumps({'config': {'rack': result}}, indent=4))
        return
    if not names:
        info('no racks defined')
        return
    for name in names:
        rack = fetch_racks(rest, name).get(name)
        if not rack:
            warn(f'rack {name} not found')
            continue
        print(render_rack(name, rack))


def cmd_add(args, rest):
    """Create a rack."""
    rack = {}
    for field in ['size', 'order', 'room', 'site']:
        value = getattr(args, field)
        if value is not None:
            rack[field] = value
    payload = {'config': {'rack': {args.name: rack}}}
    ok, message = rest.post(f'config/rack/{args.name}', payload)
    if not ok:
        error_exit(message)
    info(message or f'rack {args.name} created')


def cmd_change(args, rest):
    """Change rack properties."""
    rack = {}
    for field in ['size', 'order', 'room', 'site']:
        value = getattr(args, field)
        if value is not None:
            rack[field] = value
    if not rack:
        error_exit('nothing to change; supply at least one of --size/--order/--room/--site')
    payload = {'config': {'rack': {args.name: rack}}}
    ok, message = rest.post(f'config/rack/{args.name}', payload)
    if not ok:
        error_exit(message)
    info(message or f'rack {args.name} updated')


def cmd_rename(args, rest):
    """Rename a rack."""
    payload = {'config': {'rack': {args.name: {'newrackname': args.newname}}}}
    ok, message = rest.post(f'config/rack/{args.name}', payload)
    if not ok:
        error_exit(message)
    info(message or f'rack {args.name} renamed to {args.newname}')


def cmd_remove(args, rest):
    """Delete a rack; its devices return to the pool."""
    ok, message = rest.get_action(f'config/rack/{args.name}/_delete')
    if not ok:
        error_exit(message)
    info(message or f'rack {args.name} removed')


def cmd_place(args, rest):
    """Place device(s) into a rack; stack from --position, or auto-fill free slots."""
    names = expand_devices(args.devices)
    rack = fetch_racks(rest, args.rack).get(args.rack)
    if not rack:
        error_exit(f'rack {args.rack} not found; create it first with: lrack add {args.rack}')
    size = int(rack.get('size') or DEFAULT_SIZE)
    order = rack.get('order') or 'ascending'
    index = device_index(rest)
    slots = occupied_slots(rack.get('devices', []), ignore=set(names))

    placed = []
    cursor = args.position
    for name in names:
        device_type = lookup_type(index, name)
        height = args.height if args.height is not None else int(index[name].get('height') or DEFAULT_HEIGHT)
        if args.position is not None:
            start = cursor
        else:
            start = find_free(slots, height, size, order)
            if start is None:
                error_exit(f'no free {height}U slot for {name} in rack {args.rack} (size {size}U)')
        top = start + height - 1
        if start < 1 or top > size:
            error_exit(f'{name} ({height}U at U{start}) does not fit rack {args.rack} '
                       f'(size {size}U); placement declined')
        clash = [slots[u] for u in range(start, top + 1) if u in slots]
        if clash and not args.force:
            error_exit(f'{name} at U{start} overlaps {sorted(set(clash))}; '
                       f'use --force to override')
        device = {'name': name, 'type': device_type, 'position': start, 'height': height}
        if args.orientation:
            device['orientation'] = args.orientation
        placed.append(device)
        for unit in range(start, top + 1):
            slots[unit] = name
        if args.position is not None:
            cursor = top + 1

    payload = {'config': {'rack': {args.rack: {'devices': placed}}}}
    ok, message = rest.post(f'config/rack/{args.rack}', payload)
    if not ok:
        error_exit(message)
    for device in placed:
        info(f"placed {device['name']} at U{device['position']} "
             f"({device['height']}U) in {args.rack}")


def cmd_unplace(args, rest):
    """Remove devices from their rack, returning them to the pool."""
    index = device_index(rest)
    for name in expand_devices(args.devices):
        device_type = lookup_type(index, name)
        ok, message = rest.get_action(
            f'config/rack/inventory/{name}/type/{device_type}/_delete')
        if not ok:
            error_exit(message)
        info(f'{name} returned to the pool')


def cmd_resize(args, rest):
    """Set the height (in U) of a device in the inventory."""
    index = device_index(rest)
    inventory = []
    for name in expand_devices(args.devices):
        device_type = lookup_type(index, name)
        if args.height == 0 and device_type != 'otherdevices':
            error_exit(f'{name} ({device_type}) is not allowed to have 0 height')
        inventory.append({'name': name, 'type': device_type, 'height': args.height})
    _post_inventory(rest, inventory)
    for device in inventory:
        info(f"{device['name']} height set to {args.height}U")


def cmd_orient(args, rest):
    """Set the orientation (front/back) of a device in the inventory."""
    index = device_index(rest)
    inventory = []
    for name in expand_devices(args.devices):
        device_type = lookup_type(index, name)
        inventory.append({'name': name, 'type': device_type, 'orientation': args.orientation})
    _post_inventory(rest, inventory)
    for device in inventory:
        info(f"{device['name']} orientation set to {args.orientation}")


def cmd_inventory(args, rest):
    """List the device inventory, optionally a configured/unconfigured subset."""
    devices = fetch_inventory(rest, args.subset)
    if args.raw:
        print(json.dumps({'config': {'rack': {'inventory': devices}}}, indent=4))
        return
    if not devices:
        info('inventory is empty')
        return
    table = PrettyTable(['name', 'type', 'vendor', 'height (U)', 'orientation'])
    table.align = 'l'
    for device in devices:
        table.add_row([device['name'], device['type'], device.get('vendor') or '-',
                       device.get('height') or DEFAULT_HEIGHT,
                       device.get('orientation') or 'front'])
    print(table)


def cmd_pool(args, rest):
    """List unconfigured devices available for placement."""
    args.subset = 'unconfigured'
    cmd_inventory(args, rest)


def _post_inventory(rest, inventory):
    payload = {'config': {'rack': {'inventory': inventory}}}
    ok, message = rest.post('config/rack/inventory', payload)
    if not ok:
        error_exit(message)


# ---------------------------------------------------------------------------
# argcomplete dynamic completers
# ---------------------------------------------------------------------------

def rack_completer(prefix, **kwargs):
    """Complete rack names from the daemon."""
    try:
        return [n for n in fetch_racks(Rest()).keys() if n.startswith(prefix)]
    except Exception:
        return []


def device_completer(prefix, **kwargs):
    """Complete device names from the inventory."""
    try:
        return [d['name'] for d in fetch_inventory(Rest()) if d['name'].startswith(prefix)]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def get_parser():
    """Build the argument parser. Also used by shtab to generate completion."""
    parser = argparse.ArgumentParser(
        prog='lrack',
        description='Manage racks and the placement of devices inside them.')
    parser.add_argument('-V', '--version', action='version',
                        version=f'%(prog)s {__version__}')
    parser.add_argument('-v', '--verbose', action='store_true', help='verbose mode')
    parser.add_argument('-R', '--raw', action='store_true', help='raw JSON output')
    subparsers = parser.add_subparsers(dest='command', help='see details with <command> --help')

    sub = subparsers.add_parser('list', help='list all racks')

    sub = subparsers.add_parser('show', help='show racks and their devices')
    sub.add_argument('rack', nargs='*', help='rack name(s); all racks when omitted').completer = rack_completer

    sub = subparsers.add_parser('add', help='create a rack')
    sub.add_argument('name', help='rack name')
    _add_rack_args(sub)

    sub = subparsers.add_parser('change', help='change rack properties')
    sub.add_argument('name', help='rack name').completer = rack_completer
    _add_rack_args(sub)

    sub = subparsers.add_parser('rename', help='rename a rack')
    sub.add_argument('name', help='current rack name').completer = rack_completer
    sub.add_argument('newname', help='new rack name')

    sub = subparsers.add_parser('remove', help='delete a rack')
    sub.add_argument('name', help='rack name').completer = rack_completer

    sub = subparsers.add_parser('place', help='place device(s) into a rack')
    sub.add_argument('devices', help='device name or hostlist (node[001-010])').completer = device_completer
    sub.add_argument('-r', '--rack', required=True, help='target rack').completer = rack_completer
    sub.add_argument('-p', '--position', type=int,
                     help='starting U position (auto-stack into free slots when omitted)')
    sub.add_argument('-o', '--orientation', choices=ORIENTATIONS, help='device orientation')
    sub.add_argument('-H', '--height', type=int, help='override device height in U')
    sub.add_argument('-f', '--force', action='store_true', help='override overlap warnings')

    sub = subparsers.add_parser('unplace', help='remove device(s) from their rack')
    sub.add_argument('devices', help='device name or hostlist').completer = device_completer

    sub = subparsers.add_parser('resize', help='set device height in U')
    sub.add_argument('devices', help='device name or hostlist').completer = device_completer
    sub.add_argument('-H', '--height', type=int, required=True, help='height in U')

    sub = subparsers.add_parser('orient', help='set device orientation')
    sub.add_argument('devices', help='device name or hostlist').completer = device_completer
    sub.add_argument('-o', '--orientation', choices=ORIENTATIONS, required=True, help='orientation')

    sub = subparsers.add_parser('inventory', help='list the device inventory')
    sub.add_argument('subset', nargs='?', choices=['configured', 'unconfigured'],
                     help='limit to a subset')

    sub = subparsers.add_parser('pool', help='list unconfigured devices (placeable pool)')

    return parser


def _add_rack_args(sub):
    """Shared rack property options for add/change."""
    sub.add_argument('-s', '--size', type=int, help=f'rack size in U (default {DEFAULT_SIZE})')
    sub.add_argument('-d', '--order', choices=ORDERS, help='numbering order')
    sub.add_argument('-m', '--room', help='room')
    sub.add_argument('-t', '--site', help='site')


HANDLERS = {
    'list': cmd_list, 'show': cmd_show, 'add': cmd_add, 'change': cmd_change,
    'rename': cmd_rename, 'remove': cmd_remove, 'place': cmd_place,
    'unplace': cmd_unplace, 'resize': cmd_resize, 'orient': cmd_orient,
    'inventory': cmd_inventory, 'pool': cmd_pool,
}


def rewrite_easy(argv):
    """Translate the easy positional grammar into canonical subcommand arguments.

        node[001-010] in rack01 at 5 back   ->  place node[001-010] -r rack01 -p 5 -o back
        node001 out                         ->  unplace node001
        rack01                              ->  show rack01
    """
    lead = []
    rest = list(argv)
    while rest and rest[0] in ('-v', '--verbose', '-R', '--raw'):
        lead.append(rest.pop(0))
    if not rest or rest[0] in SUBCOMMANDS or rest[0] in ('-h', '--help', '-V', '--version'):
        return argv

    if 'in' in rest:
        marker = rest.index('in')
        if marker == 0 or marker + 1 >= len(rest):
            return argv
        out = lead + ['place', rest[marker - 1], '-r', rest[marker + 1]]
        tail = rest[marker + 2:]
        index = 0
        while index < len(tail):
            word = tail[index]
            if word == 'at' and index + 1 < len(tail):
                out += ['-p', tail[index + 1]]
                index += 2
            elif word in ORIENTATIONS:
                out += ['-o', word]
                index += 1
            elif word in ('-f', '--force', 'force'):
                out += ['-f']
                index += 1
            else:
                index += 1
        return out

    if len(rest) == 2 and rest[1] == 'out':
        return lead + ['unplace', rest[0]]

    if len(rest) == 1 and not rest[0].startswith('-'):
        return lead + ['show', rest[0]]

    return argv


def _init_logger(verbose):
    """Initialise the file logger, degrading to a null logger without write access."""
    level = 'debug' if verbose else 'info'
    log_dir = os.path.dirname(LOG_FILE)
    writable = os.access(log_dir, os.W_OK) or \
        (not os.path.exists(log_dir) and os.access(os.path.dirname(log_dir), os.W_OK))
    if writable:
        return Log.init_log(log_file=LOG_FILE, log_level=level)
    fallback = logging.getLogger('lrack')
    fallback.addHandler(logging.NullHandler())
    return fallback


def main():
    """Entry point: parse arguments and dispatch to the matching handler."""
    parser = get_parser()
    argcomplete.autocomplete(parser, always_complete_options=False)
    args = parser.parse_args(rewrite_easy(sys.argv[1:]))

    global logger
    logger = _init_logger(args.verbose)
    logger.info(f"User {getpass.getuser()} ran => {' '.join(sys.argv)}")

    if not args.command:
        parser.print_help()
        sys.exit(0)

    rest = Rest()
    HANDLERS[args.command](args, rest)


if __name__ == '__main__':
    main()
