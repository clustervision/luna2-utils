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
ldebug replays what the luna2-daemon hands a node on its way from iPXE to a
running installer, and shows each part as a readable section. It is a client of
the boot, install, roles, profiles, scripts, secrets and monitor routes; the
'api' area is a generic request against any route with the luna.ini token.
"""

__author__      = 'Antoine Schonewille'
__copyright__   = 'Copyright 2026, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '2.2'
__maintainer__  = 'Dev-team'
__email__       = 'antoine.schonewille@clustervision.com'
__status__      = 'Development'

#VERSION: 0.1.0

import os
import re
import sys
import json
import base64
import logging
import getpass
import argparse
import difflib
from datetime import datetime

import requests
from requests import Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry
from termcolor import colored

import argcomplete

from utils.utils.log import Log
from utils.utils.ini import Ini
from utils.utils.token import Token
from utils.utils.interrupt import exit_on_interrupt

INI_FILE = os.environ.get('LDEBUG_INI', '/trinity/local/luna/utils/config/luna.ini')
LOG_FILE = '/var/log/luna/ldebug.log'
DUMP_DIR = '/var/log/luna/ldebug'
STAGES = ('menu', 'discover', 'install', 'scripts', 'roles', 'profiles', 'secrets', 'mounts', 'status')
RENDER_STAGES = ('scripts', 'roles', 'profiles', 'mounts')
MASK_LINE = re.compile(r'^(\s*(?:export\s+)?[A-Za-z_]*(?:TOKEN|PASSWORD)=)(["\']?)(.*?)\2\s*$', re.M)
B64_INPUT = re.compile(r'echo "([A-Za-z0-9+/=]*)" \| base64 -d(.*)')
B64_TEXT = re.compile(r'^[A-Za-z0-9+/]+={0,2}$')
INPUT_NAMES = (('MPATH', 'dirs'), ('MLINE', 'fstab'), ('luna.exports', 'exports'),
               ('storage.json', 'disklayout'), ('osimage-filter.json', 'osimage-filter'))
INPUT_FILES = {'dirs': '40-mounts/dirs', 'fstab': '40-mounts/fstab', 'exports': '40-mounts/exports',
               'disklayout': '41-disklayout.json', 'osimage-filter': '42-osimage-filter.json'}

urllib3.disable_warnings()
logger = logging.getLogger('ldebug')
USE_COLOR = sys.stdout.isatty() and not os.environ.get('NO_COLOR')


def _color(text, color=None, on_color=None, attrs=None):
    """Colour text only when writing to a terminal, so pipes stay clean."""
    if USE_COLOR:
        return colored(text, color, on_color, attrs=attrs)
    return text


def warn(message):
    """Print a warning to stderr."""
    sys.stderr.write(_color('WARNING', 'yellow', attrs=['bold']) + f' :: {message}\n')


def error_exit(message, code=1):
    """Print an error to stderr and stop."""
    sys.stderr.write(_color('ERROR', 'red', attrs=['bold']) + f' :: {message}\n')
    logger.error(message)
    sys.exit(code)


def masked(value):
    """Replace a secret with its length, so a diff still shows that it changed."""
    return f'***len={len(value)}'


def mask_text(text):
    """Mask token and password assignments in a rendered script."""
    return MASK_LINE.sub(lambda m: f'{m.group(1)}{m.group(2)}{masked(m.group(3))}{m.group(2)}', text)


def mask_json(data, keys=('password',)):
    """Mask the named keys in a daemon response, recursively."""
    if isinstance(data, dict):
        return {key: masked(value) if key in keys and isinstance(value, str) else mask_json(value, keys)
                for key, value in data.items()}
    if isinstance(data, list):
        return [mask_json(item, keys) for item in data]
    return data


def decode_fields(data):
    """Decode base64 text values in a daemon response, so scripts and units read as written."""
    if isinstance(data, dict):
        return {key: decode_fields(value) for key, value in data.items()}
    if isinstance(data, list):
        return [decode_fields(item) for item in data]
    if isinstance(data, str) and len(data) >= 8 and len(data) % 4 == 0 and B64_TEXT.match(data):
        try:
            text = base64.b64decode(data, validate=True).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return data
        if all(char.isprintable() or char in '\n\r\t' for char in text):
            return text
    return data


# ---------------------------------------------------------------------------
# reading the rendered installer
# ---------------------------------------------------------------------------

def parse_variables(render):
    """Collect the export lines and the banner facts the installer prints about itself."""
    variables = {}
    for line in re.findall(r'^export ([A-Za-z_]+=.*)$', render, re.M):
        name, _, value = line.partition('=')
        variables[name] = value
    banner = re.search(r'Luna2: nodename is (\S+), member of (\S*), distribution is (\S*) ?(\S*)"', render)
    if banner:
        variables['NODE'], variables['GROUP'] = banner.group(1), banner.group(2)
        variables['DISTRIBUTION'] = f'{banner.group(3)} {banner.group(4)}'.strip()
    mode = re.search(r'Luna2: install_mode is (\S+), installing via the (.*)"', render)
    if mode:
        variables['INSTALL_MODE'] = f'{mode.group(1)} ({mode.group(2)})'
    methods = re.search(r'for method in (.*); do', render)
    if methods:
        variables['PROVISION_METHODS'] = methods.group(1)
    for kind in ('ROLES', 'PROFILES', 'SCRIPTS'):
        variables[kind] = ','.join(parse_names(render, kind))
    return variables


def parse_names(render, kind):
    """The role, profile or script names the installer will fetch (ROLES, PROFILES, SCRIPTS)."""
    match = re.search(r'local %s=\$\(echo (\S*) \| tr' % kind, render)
    if not match or not match.group(1):
        return []
    return match.group(1).split(',')


def parse_heredoc(render, target):
    """Content of the heredoc the installer writes to target."""
    match = re.search(r"cat << 'LUNAEOF' > %s\n(.*?)\nLUNAEOF" % re.escape(target), render, re.S)
    return match.group(1) if match else ''


def parse_function(render, name):
    """Body of a top-level bash function in the installer."""
    match = re.search(r'^function %s \{\n(.*?)^\}' % re.escape(name), render, re.S | re.M)
    return match.group(1) if match else ''


def resolve_value(value, env):
    """One right-hand side of a bash assignment in change_net, as the installer would see it."""
    by_mac = re.fullmatch(r'\$\(get_interface_by_mac "([^"]*)" "([^"]*)"\)', value)
    if by_mac:
        name, mac = by_mac.groups()
        return f'<NIC with MAC {mac}>' if name == 'non_existing' else f'<NIC with MAC {mac}, else {name}>'
    return re.sub(r'\$\{?([A-Za-z_]+)\}?', lambda m: env.get(m.group(1), m.group(0)), value)


def walk_assignments(lines, env):
    """Follow interface_name, parent_name and interface_bootif through the lines, in order.

    The one branch that matters is the LUNA_BOOTIF test: dracut sets it, so the else
    branch that would name the interface BOOTIF is the one the node never takes."""
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == 'else' and env.get('_bootif_test'):
            skipping = True
        elif stripped == 'fi':
            skipping = False
            env.pop('_bootif_test', None)
        elif stripped.startswith('if [ -n "${LUNA_BOOTIF}" ]'):
            env['_bootif_test'] = True
        if skipping:
            continue
        match = re.fullmatch(r'(interface_name|parent_name|interface_bootif)=(.*)', stripped)
        if match:
            env[match.group(1)] = resolve_value(match.group(2), env)


def bash_default(value):
    """The innermost default of a ${VAR:-${OTHER:-literal}} chain, which is what dracut leaves it at."""
    while True:
        reduced = re.sub(r'\$\{[A-Za-z_]+:-([^{}]*)\}', r'\1', value)
        if reduced == value:
            return value
        value = reduced


def substitute(text, env):
    """Replace the change_net variables in a block with what they resolve to."""
    return re.sub(r'\$\{?(interface_name|parent_name|interface_bootif|rootmnt)\}?', lambda m: env.get(m.group(1), ''), text)


def parse_interfaces(render, bootif=''):
    """One block per interface from change_net: name, destination file, the config written there,
    with the installer's own variables resolved the way its bash resolves them. bootif is the
    NIC the node booted from when it is known; the installer learns it from dracut."""
    body = parse_function(render, 'change_net')
    pieces = re.split(r'^\s*######### (\S+) #########\n', body, flags=re.M)
    rootmnt = re.search(r'^\s*export rootmnt="([^"]*)"', render, re.M)
    env = {'LUNA_BOOTIF': bootif or '${LUNA_BOOTIF}',
           'rootmnt': bash_default(rootmnt.group(1)) if rootmnt else '${rootmnt}'}
    walk_assignments(pieces[0].splitlines(), env)
    interfaces = []
    for name, block in zip(pieces[1::2], pieces[2::2]):
        config = re.search(r'cat << LUNAEOF > "/lunatmp/interface_\$\{interface_name\}\.conf"\n(.*?)\nLUNAEOF', block, re.S)
        content = substitute(config.group(1) if config else '', env)
        destination = re.search(r'^#FILE (.*)$', content, re.M)
        calls = [line.strip() for line in block.splitlines() if 'config_' in line and '(' not in line]
        interfaces.append({
            'name': name,
            'destination': destination.group(1) if destination else '',
            'content': content,
            'calls': substitute('\n'.join(calls), env),
            'variables': {key: env.get(key, '') for key in ('interface_name', 'parent_name')},
        })
        walk_assignments(block.splitlines(), env)
    return interfaces


def parse_inputs(render):
    """The base64 payloads the render carries inline: mounts, disklayout, osimage filter."""
    inputs = {}
    for encoded, tail in B64_INPUT.findall(render):
        for marker, name in INPUT_NAMES:
            if marker in tail:
                inputs[name] = base64.b64decode(encoded).decode('utf-8', 'replace') if encoded else ''
    return inputs


# ---------------------------------------------------------------------------
# REST, output and dump
# ---------------------------------------------------------------------------

class Rest():
    """Raw REST client: every call returns the response untouched and is logged."""

    def __init__(self):
        config = Ini.read_ini(ini_file=INI_FILE)
        self.username = config['USERNAME']
        self.password = config['PASSWORD']
        self.verify = config['VERIFY_CERTIFICATE']
        self.base = f"{config['PROTOCOL']}://{config['ENDPOINT']}"
        self.timeout = 60
        self.token = None
        self.calls = []
        self.session = Session()
        retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[502, 503, 504],
                        allowed_methods={'GET', 'POST'})
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def _headers(self, auth):
        headers = {'Content-Type': 'application/json'}
        if auth:
            if self.token is None:
                protocol, endpoint = self.base.split('://')
                self.token = Token.get_token(username=self.username, password=self.password,
                                             protocol=protocol, endpoint=endpoint,
                                             verify_certificate=self.verify)
            headers['x-access-tokens'] = self.token
        return headers

    def call(self, method, route, payload=None, auth=True):
        """Send one request and return the response; connection trouble ends the run."""
        route = route.lstrip('/')
        url = f'{self.base}/{route}'
        logger.debug(f'{method} {url}')
        try:
            response = self.session.request(method, url, json=payload, headers=self._headers(auth),
                                            timeout=self.timeout, verify=self.verify)
        except requests.exceptions.ConnectionError:
            error_exit(f'cannot reach {url}')
        except requests.exceptions.Timeout:
            error_exit(f'timeout after {self.timeout}s on {url}')
        self.calls.append((method, '/' + route, response.status_code, len(response.content),
                           int(response.elapsed.total_seconds() * 1000)))
        return response

    def get(self, route, auth=True):
        """GET a route."""
        return self.call('GET', route, auth=auth)

    def json(self, route, auth=True):
        """GET a route and return its JSON, or None on anything but 200."""
        response = self.get(route, auth=auth)
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            return None

    def request_log(self):
        """Every request of this run as text: method route status bytes ms."""
        return '\n'.join(f'{m:<4} {r:<50} {s:>4} {b:>8} {t:>6}ms' for m, r, s, b, t in self.calls)


class Output():
    """Prints sections and, with --dump, writes the same content as files."""

    def __init__(self, args):
        self.raw = args.raw
        self.show_secrets = args.show_secrets
        self.failed = False
        self.directory = None
        if args.dump:
            self.directory = self._prepare(args.dump_dir, args.subdir)

    def _prepare(self, base, subdir):
        directory = os.path.join(base, subdir, datetime.now().strftime('%Y%m%d-%H%M%S'))
        try:
            os.makedirs(base, mode=0o700, exist_ok=True)
            os.makedirs(directory, mode=0o700)
        except PermissionError:
            error_exit(f'cannot write to {base}; pick another directory with -d')
        return directory

    def section(self, title, body, status=None, note=None, filename=None, display=None):
        """Print one section; status 2xx is green, anything else red and remembered.
        display, when given, is what the terminal gets instead of body; the dump always gets body."""
        header = _color(f' {title} ', 'white', 'on_blue', attrs=['bold'])
        if status is not None:
            ok = 200 <= status < 300
            self.failed = self.failed or not ok
            header += ' ' + _color(str(status), 'green' if ok else 'red', attrs=['bold'])
        if note:
            header += ' ' + _color(note, 'yellow')
        print(header)
        text = body if isinstance(body, str) else json.dumps(body, indent=2)
        shown = text if display is None else display
        if shown:
            print(shown if self.raw else '\n'.join('  ' + line for line in shown.splitlines()))
        print()
        if filename and self.directory:
            self.write(filename, text)

    def write(self, filename, text):
        """Write one dump file; secrets shown means the file is private."""
        path = os.path.join(self.directory, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text if text.endswith('\n') or not text else text + '\n')
        if self.show_secrets:
            os.chmod(path, 0o600)


def api_body(response, show_secrets, keys=('password',)):
    """A response body as pretty JSON when it parses, else as text."""
    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError:
        return response.text if show_secrets else mask_text(response.text)
    return data if show_secrets else mask_json(data, keys)


# ---------------------------------------------------------------------------
# area: boot
# ---------------------------------------------------------------------------

def node_facts(rest, node):
    """What the tool needs to know about the node before it replays the boot."""
    data = rest.json(f'config/node/{node}')
    if not data:
        error_exit(f'{node} is not a known node')
    facts = data['config']['node'][node]
    provision = (facts.get('provision_interface') or 'BOOTIF').split(' ')[0]
    mac = next((iface.get('macaddress') or '' for iface in facts.get('interfaces', [])
                if iface['interface'] == provision), '')
    osimage = rest.json(f"config/osimage/{facts.get('osimage')}") if facts.get('osimage') else None
    imagefile = ''
    if osimage:
        imagefile = osimage['config']['osimage'][facts['osimage']].get('imagefile') or ''
    return {'provision_interface': provision, 'mac': mac.lower(), 'osimage': facts.get('osimage'),
            'imagefile': imagefile, 'install_mode': facts.get('install_mode')}


def stage_menu(rest, out):
    """The iPXE menus every node gets before it is known."""
    for index, route in enumerate(('boot', 'boot/short', 'boot/disk')):
        response = rest.get(route, auth=False)
        out.section(f'iPXE menu /{route}', response.text, response.status_code,
                    filename=f"0{index}-{route.replace('/', '-')}.ipxe")


def stage_discover(rest, out, node, facts, mac, allow_mutation):
    """Discovery by MAC: the nodeboot script, as the daemon answers the node's own MAC."""
    if mac and mac != facts['mac']:
        if not allow_mutation:
            error_exit(f"{mac} is not the MAC of {node}'s {facts['provision_interface']}; "
                       'discovery of a foreign MAC can assign it, create a node and restart DHCP. '
                       'Pass --allow-mutation to do that on purpose.')
        warn(f'discovering {mac}, which is not the MAC of {node}: the daemon may change the database')
    mac = mac or facts['mac']
    if not mac:
        out.section('Discovery', f"{node} has no MAC on {facts['provision_interface']}; "
                    'nothing to discover (pass --mac to try one)', note='skipped')
        return
    response = rest.get(f'boot/search/mac/{mac}', auth=False)
    out.section(f'Discovery /boot/search/mac/{mac}', response.text, response.status_code,
                note='daemon sets install.discovered', filename='10-nodeboot.ipxe')


def boot_nic(rest, node, mac):
    """The NIC name the node itself reported for its boot MAC, from the newest in-band
    inventory snapshot, as (name, source, updated); empty when nothing was reported."""
    if not mac:
        return '', '', ''
    data = rest.json(f'config/node/{node}/inventory')
    snapshots = ((data or {}).get('config', {}).get('node', {}).get(node, {}) or {}).get('inventory', [])
    ranked = sorted(snapshots, key=lambda s: (s.get('source') == 'inband', s.get('updated') or ''), reverse=True)
    for snapshot in ranked:
        for nic in snapshot.get('nics') or []:
            if (nic.get('mac') or '').lower() == mac and nic.get('name'):
                return nic['name'], snapshot.get('source', ''), snapshot.get('updated', '')
    return '', '', ''


def stage_install(rest, out, node, facts):
    """The rendered installer, split into the parts the node runs."""
    route = f'kickstart/install/{node}' if facts['imagefile'] == 'kickstart' else f'boot/install/{node}'
    response = rest.get(route)
    render = response.text
    shown = render if out.show_secrets else mask_text(render)
    out.section(f'Install /{route}', '' if response.status_code == 200 else shown, response.status_code,
                note='daemon sets install.rendered')
    if response.status_code != 200:
        return None
    if out.directory:
        out.write('20-install.sh', shown)
    variables = parse_variables(shown)
    out.section('Variables', '\n'.join(f'{k}={v}' for k, v in variables.items()), filename='21-variables.env')
    for index, name in enumerate(('pre', 'part', 'post')):
        out.section(f'{name.capitalize()} script', parse_heredoc(shown, f'/lunatmp/{name}script.sh'),
                    filename=f'2{index + 2}-{name}script.sh')
    bmc = parse_function(shown, 'bmcsetup')
    if bmc:
        out.section('BMC', bmc + parse_function(shown, 'config_bmc'), filename='25-bmc.sh')
    nic, source, updated = boot_nic(rest, node, facts['mac']) if '${LUNA_BOOTIF}' in render else ('', '', '')
    runtime = False
    for iface in parse_interfaces(shown, nic):
        body = iface['content'] or iface['calls']
        variables = ' '.join(f'{key}={value or "(empty)"}' for key, value in iface['variables'].items())
        note = f"{variables} -> {iface['destination'] or 'plugin calls, no file'}"
        runtime = runtime or '${LUNA_BOOTIF}' in note or '${LUNA_BOOTIF}' in body
        filename = re.sub(r'\$\{LUNA_BOOTIF\}|<[^>]*>', iface['name'], os.path.basename(iface['destination']))
        out.section(f"Network {iface['name']}", body, note=note,
                    filename=f"30-network/{iface['name']}/{filename or 'calls.sh'}")
    if nic:
        print(f"  boot NIC {nic} taken from the node's {source} inventory of {updated} (MAC {facts['mac']})\n")
    elif runtime:
        print('  ${LUNA_BOOTIF} is the NIC dracut found with the boot MAC; the node has not reported it in its inventory\n')
    return render


def stage_mounts(out, render):
    """Mounts, disklayout and osimage filter, decoded from the render."""
    inputs = parse_inputs(render)
    if not any(inputs.values()):
        out.section('Mounts and layout', 'none carried in the render', note='empty')
        return
    for name, text in inputs.items():
        if text:
            out.section(name.capitalize(), text, filename=INPUT_FILES[name])


def leaves(data, path=()):
    """Every scalar in a nested response with the path that leads to it."""
    if isinstance(data, dict):
        return [leaf for key, value in data.items() for leaf in leaves(value, path + (str(key),))]
    if isinstance(data, list):
        return [leaf for index, value in enumerate(data) for leaf in leaves(value, path + (str(index),))]
    return [(path, data)]


def stage_fetch(rest, out, kind, names, route, prefix):
    """Roles, profiles or scripts: one request per name in the render, each field decoded and shown."""
    if not names:
        out.section(kind.capitalize(), f'no {kind} in the render', note='empty')
        return
    for name in names:
        response = rest.get(f'{route}/{name}')
        body = api_body(response, out.show_secrets)
        if response.status_code != 200 or not isinstance(body, dict):
            out.section(f'{kind.capitalize()} {name} /{route}/{name}', body, response.status_code)
            continue
        out.section(f'{kind.capitalize()} {name} /{route}/{name}', '', response.status_code)
        for path, value in leaves(decode_fields(body)):
            field = '/'.join(path[2:]) if path[1:2] == (name,) else '/'.join(path)
            out.section(f'{kind.capitalize()} {name} {field}', str(value), filename=f'{prefix}-{kind}/{name}/{field}')


def stage_secrets(rest, out, node):
    """Secrets of the node, masked unless asked otherwise."""
    response = rest.get(f'config/secrets/node/{node}')
    if response.status_code == 404:
        out.section('Secrets', f'no secrets for {node}', note='empty')
        return
    out.section(f'Secrets /config/secrets/node/{node}',
                api_body(response, out.show_secrets, keys=('content', 'password')),
                response.status_code, note=None if out.show_secrets else 'masked', filename='80-secrets.json')


def stage_status(rest, out, node):
    """The monitor row of the node as it is now."""
    response = rest.get(f'monitor/node/{node}', auth=False)
    out.section(f'Node state /monitor/node/{node}', api_body(response, out.show_secrets),
                response.status_code, filename='90-status.json')


def cmd_boot(args, rest):
    """Replay the boot path of one node, stage by stage."""
    stages = list(STAGES) if not args.stage else args.stage.split(',')
    unknown = [stage for stage in stages if stage not in STAGES]
    if unknown:
        error_exit(f"unknown stage {','.join(unknown)}; choose from {','.join(STAGES)}")
    if 'install' not in stages and any(stage in RENDER_STAGES for stage in stages):
        warn('roles, profiles, scripts and mounts are read from the render: adding the install stage')
        stages.append('install')
    if args.diff:
        args.dump = True
    out = Output(args)
    facts = node_facts(rest, args.node)
    render = None
    if 'menu' in stages:
        stage_menu(rest, out)
    if 'discover' in stages:
        stage_discover(rest, out, args.node, facts, (args.mac or '').lower(), args.allow_mutation)
    if 'install' in stages:
        render = stage_install(rest, out, args.node, facts)
    if render:
        if 'mounts' in stages:
            stage_mounts(out, render)
        for kind, route, prefix in (('scripts', 'boot/scripts', '50'), ('roles', 'boot/roles', '60'),
                                    ('profiles', 'boot/profiles', '70')):
            if kind in stages:
                stage_fetch(rest, out, kind, parse_names(render, kind.upper()), route, prefix)
    elif any(stage in RENDER_STAGES for stage in stages):
        warn('no render, so the stages read from it were skipped')
    if 'secrets' in stages:
        stage_secrets(rest, out, args.node)
    if 'status' in stages:
        stage_status(rest, out, args.node)
    if args.diff:
        stage_diff(out, args.diff)
    out.section('Requests', rest.request_log(), filename='requests.log')
    if out.directory:
        print(f'dumped to {out.directory}')
    if out.failed:
        sys.exit(2)


def earlier_dump(current, wanted):
    """The dump to compare with: the newest sibling of the current one, or the directory named."""
    if wanted != 'latest':
        return wanted if os.path.isdir(wanted) else ''
    parent = os.path.dirname(current)
    siblings = sorted(name for name in os.listdir(parent)
                      if os.path.isdir(os.path.join(parent, name)) and name != os.path.basename(current))
    return os.path.join(parent, siblings[-1]) if siblings else ''


def dump_files(directory):
    """Every file of a dump by its relative path, without the request log and an earlier
    diff, which are about a run rather than about the node."""
    files = {}
    for root, _, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            relative = os.path.relpath(path, directory)
            if relative not in ('requests.log', 'diff.txt'):
                files[relative] = path
    return files


def compare_dumps(before, after):
    """Unified diff of two dumps as (text, changed, added, removed)."""
    old, new = dump_files(before), dump_files(after)
    lines, changed = [], 0
    for relative in sorted(set(old) | set(new)):
        if relative not in old:
            lines.append(f'added:   {relative}')
            continue
        if relative not in new:
            lines.append(f'removed: {relative}')
            continue
        with open(old[relative], encoding='utf-8', errors='replace') as handle:
            old_lines = handle.readlines()
        with open(new[relative], encoding='utf-8', errors='replace') as handle:
            new_lines = handle.readlines()
        diff = list(difflib.unified_diff(old_lines, new_lines, f'{before}/{relative}', f'{after}/{relative}'))
        if diff:
            changed += 1
            lines.append(''.join(diff).rstrip('\n'))
    added = len(set(new) - set(old))
    removed = len(set(old) - set(new))
    return '\n'.join(lines), changed, added, removed


def colour_diff(text):
    """Colour the lines of a unified diff for the terminal."""
    coloured = []
    for line in text.splitlines():
        if line.startswith('+++') or line.startswith('---'):
            coloured.append(_color(line, attrs=['bold']))
        elif line.startswith('+') or line.startswith('added:'):
            coloured.append(_color(line, 'green'))
        elif line.startswith('-') or line.startswith('removed:'):
            coloured.append(_color(line, 'red'))
        elif line.startswith('@@'):
            coloured.append(_color(line, 'cyan'))
        else:
            coloured.append(line)
    return '\n'.join(coloured)


def stage_diff(out, wanted):
    """Compare this run's dump with an earlier one, file by file."""
    before = earlier_dump(out.directory, wanted)
    if not before:
        out.section('Diff', 'no earlier dump to compare with' if wanted == 'latest' else f'{wanted} is not a directory',
                    note='skipped')
        return
    text, changed, added, removed = compare_dumps(before, out.directory)
    note = (f'{changed} changed, {added} added, {removed} removed' if changed or added or removed else 'identical')
    out.section(f'Diff {before} -> {out.directory}', text, note=note, filename='diff.txt', display=colour_diff(text))


# ---------------------------------------------------------------------------
# area: api
# ---------------------------------------------------------------------------

def cmd_api(args, rest):
    """One request against any route, printed and optionally dumped."""
    payload = None
    if args.json:
        text = args.json
        if text.startswith('@'):
            try:
                with open(text[1:], encoding='utf-8') as handle:
                    text = handle.read()
            except OSError as exp:
                error_exit(f'cannot read {text[1:]}: {exp}')
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exp:
            error_exit(f'-j is not valid JSON: {exp}')
    out = Output(args)
    response = rest.call(args.method, args.route, payload=payload, auth=not args.no_auth)
    out.section(f'{args.method} /{args.route.lstrip("/")}', api_body(response, args.show_secrets),
                response.status_code, filename=re.sub(r'[^A-Za-z0-9._-]+', '_', args.route.strip('/')) + '.out')
    out.section('Requests', rest.request_log(), filename='requests.log')
    if out.directory:
        print(f'dumped to {out.directory}')
    if out.failed:
        sys.exit(2)


# ---------------------------------------------------------------------------
# parser and entry point
# ---------------------------------------------------------------------------

def add_global_options(parser, after_area=False):
    """The options accepted before and after the area. After it they carry no default,
    so an area's parser never resets what was already parsed in front of it."""
    flag = {'default': argparse.SUPPRESS} if after_area else {}
    parser.add_argument('-v', '--verbose', action='store_true', help='verbose mode', **flag)
    parser.add_argument('--no-color', action='store_true', help='plain output, also implied by NO_COLOR', **flag)
    parser.add_argument('--raw', action='store_true', help='print bodies untouched, without indentation', **flag)
    parser.add_argument('--dump', action='store_true', help='also write every section to a directory', **flag)
    parser.add_argument('-d', '--dump-dir', metavar='DIR', default=argparse.SUPPRESS if after_area else DUMP_DIR,
                        help=f'where --dump writes (default {DUMP_DIR})')
    parser.add_argument('--show-secrets', action='store_true', **flag,
                        help='show tokens, passwords and secret contents instead of masking them')


def get_parser():
    """Build the argument parser. Also used by shtab to generate completion."""
    parser = argparse.ArgumentParser(
        prog='ldebug',
        description='Replay and inspect what the luna2-daemon serves; exit 2 when a request failed.')
    parser.add_argument('-V', '--version', action='version', version=f'%(prog)s {__version__}')
    add_global_options(parser)
    subparsers = parser.add_subparsers(dest='area', help='see details with <area> --help')

    sub = subparsers.add_parser('boot', help='replay the iPXE, discovery and install path of a node')
    add_global_options(sub, after_area=True)
    sub.add_argument('node', help='node name')
    sub.add_argument('--stage', metavar='STAGE[,STAGE]', help=f"stages to run, default all: {','.join(STAGES)}")
    sub.add_argument('--mac', metavar='MAC', help='discover this MAC instead of the node\'s own')
    sub.add_argument('--allow-mutation', action='store_true',
                     help='allow discovery that can assign a MAC, create a node or restart DHCP')
    sub.add_argument('--diff', nargs='?', const='latest', metavar='DIR',
                     help='dump this run and compare it with an earlier dump: the newest other one of this node, or DIR')

    sub = subparsers.add_parser('api', help='one request against any daemon route')
    add_global_options(sub, after_area=True)
    sub.add_argument('method', choices=['GET', 'POST'], help='HTTP method')
    sub.add_argument('route', help='route, e.g. /monitor/queue')
    sub.add_argument('-j', '--json', metavar='JSON|@FILE', help='request body as JSON text or @file')
    sub.add_argument('--no-auth', action='store_true', help='send no token, the way iPXE calls the daemon')
    return parser


def _init_logger(verbose):
    """File logger when the log directory is writable, otherwise silent."""
    level = 'debug' if verbose else 'info'
    log_dir = os.path.dirname(LOG_FILE)
    writable = os.access(log_dir, os.W_OK) or \
        (not os.path.exists(log_dir) and os.access(os.path.dirname(log_dir), os.W_OK))
    if writable:
        return Log.init_log(log_file=LOG_FILE, log_level=level)
    fallback = logging.getLogger('ldebug')
    fallback.addHandler(logging.NullHandler())
    return fallback


def main():
    """Entry point; Ctrl-C ends the run with a message instead of a traceback."""
    return exit_on_interrupt(_main)


def _main():
    """Parse arguments and dispatch to the area handler."""
    global logger, USE_COLOR
    parser = get_parser()
    argcomplete.autocomplete(parser, always_complete_options=False)
    args = parser.parse_args()
    if args.no_color:
        USE_COLOR = False
    logger = _init_logger(args.verbose)
    logger.info(f"User {getpass.getuser()} ran => {' '.join(sys.argv)}")
    if not args.area:
        parser.print_help()
        sys.exit(0)
    args.subdir = args.node if args.area == 'boot' else 'api'
    {'boot': cmd_boot, 'api': cmd_api}[args.area](args, Rest())


if __name__ == '__main__':
    main()
