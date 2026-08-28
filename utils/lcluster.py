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
lcluster Utility for Trinity Project
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2026, Luna2 Project [UTILITY]"
__license__     = "GPL"
__version__     = "2.2"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Production"


import getpass
import json
import os
import re
import shlex
import shutil
import sys
import threading
import textwrap
from configparser import RawConfigParser
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import quote, urlparse, urlunparse

try:
    from hostlist import collect_hostlist
    import jwt
    import requests
    from requests import Session
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
    import urllib3
    from prettytable import PrettyTable
    from termcolor import colored
except ImportError as exp:
    sys.stderr.write(f"ERROR :: Missing required Python module: {exp}\n")
    sys.exit(1)

try:
    import requests_unixsocket
except ImportError:
    requests_unixsocket = None

import subprocess as sp

TOKEN_FILE = '/trinity/local/luna/utils/config/token.txt'
INI_FILE = '/trinity/local/luna/utils/config/luna.ini'

SLURM_API_VERSIONS = [
    'v0.0.45', 'v0.0.44', 'v0.0.43', 'v0.0.42',
    'v0.0.41', 'v0.0.40', 'v0.0.39', 'v0.0.38'
]

SLURM_UNIX_SOCKET_CANDIDATES = [
    '/run/slurmrestd/slurmrestd.sock',
    '/var/run/slurmrestd/slurmrestd.sock',
    '/var/lib/slurmrestd.socket',
    '/run/slurmrestd/slurmrestd.socket',
    '/run/slurmrestd.socket',
    '/var/run/slurmrestd.socket',
]

SLURM_FALLBACK_PORTS = [6820, 6802]

ALERTX_STATUS_PRIORITY = {
    'OK': 0,
    'FAIL': 1,
    'FAIL + NHC': 2,
    'DOWN': 3,
}

ALERTX_RULE_CATEGORIES = {'generic', 'service', 'hardware', 'other'}
ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*m')
DEFAULT_OUTPUT_CHUNK_SIZE = 5
TABLE_COLUMN_MAX_WIDTHS = [4, 9, 17, 10, 28, 24, 21]


class LCluster():
    """
    LCluster Class responsible to all Monitoring activities.
    """

    def __init__(self):
        """
        Default variables should be here before calling any method.
        """
        self.errors = []
        self.username = None
        self.password = None
        self.daemon = None
        self.daemon_host = None
        self.secret_key = None
        self.protocol = None
        self.security = ''
        self.table = PrettyTable()
        self.slurm_backend = None
        self.slurm_auth = None
        self.slurm_verify = None
        self.slurm_timeout = 3.0
        self.ipmi_poll_interval = 1.0
        self.ipmi_max_wait = 120.0
        self.request_timeout = 5
        self.output_chunk_size = DEFAULT_OUTPUT_CHUNK_SIZE
        self.prometheus = None
        self.prometheus_status = None

        file_check = os.path.isfile(INI_FILE)
        read_check = os.access(INI_FILE, os.R_OK)
        if file_check and read_check:
            configparser = RawConfigParser()
            configparser.read(INI_FILE)
            if configparser.has_section('API'):
                self.username = self.get_option(configparser, 'API', 'USERNAME')
                self.password = self.get_option(configparser, 'API', 'PASSWORD')
                self.secret_key = self.get_option(configparser, 'API', 'SECRET_KEY')
                self.protocol = self.get_option(configparser, 'API', 'PROTOCOL')
                self.daemon = self.get_option(configparser, 'API', 'ENDPOINT')
                self.security = self.get_option(configparser, 'API', 'VERIFY_CERTIFICATE')
                self.security = True if str(self.security).lower() in ['y', 'yes', 'true'] else False
                if self.slurm_verify is None:
                    self.slurm_verify = self.security
                self.daemon_host = self._extract_host(self.daemon)
                self.daemon = f'{self.protocol}://{self.daemon}'
                self.prometheus = self._replace_url_port(self.daemon, 9090)
                self.prometheus_status = f'{self.prometheus}/api/v1/rules'
            else:
                self.errors.append(f'API section is not found in {INI_FILE}.')
        else:
            self.errors.append(f'{INI_FILE} is not found on this machine.')

        if self.errors:
            sys.stderr.write('You need to fix following errors...\n')
            for num, error in enumerate(self.errors, start=1):
                sys.stderr.write(f'{num}. {error}\n')
            sys.exit(1)

        urllib3.disable_warnings()
        self.session = Session()
        self.retries = Retry(
            total=10,
            backoff_factor=0.1,
            status_forcelist=[502, 503, 504],
            allowed_methods={'GET', 'POST'},
        )
        self.session.mount('https://', HTTPAdapter(max_retries=self.retries))
        self.session.mount('http://', HTTPAdapter(max_retries=self.retries))
        self.daemon_validation()


    def _extract_host(self, endpoint):
        """
        Extract host from luna.ini ENDPOINT. Supports host, host:port and URL-ish values.
        """
        if not endpoint:
            return None
        endpoint = str(endpoint).strip()
        endpoint = re.sub(r'^https?://', '', endpoint)
        endpoint = endpoint.split('/')[0]
        if endpoint.startswith('[') and ']' in endpoint:
            return endpoint[1:endpoint.index(']')]
        return endpoint.split(':')[0]


    def _replace_url_port(self, url, port):
        """
        Build a URL using the same scheme and hostname as `url`, but with a different port.
        """
        parsed = urlparse(str(url))
        scheme = parsed.scheme or self.protocol or 'http'
        host = parsed.hostname or self._extract_host(url)
        if not host:
            return str(url).rstrip('/')

        if ':' in host and not host.startswith('['):
            host = f'[{host}]'

        return urlunparse((scheme, f'{host}:{port}', '', '', '', ''))


    def get_option(self, parser=None, section=None, option=None):
        """
        Retrieve a value from the INI file.
        """
        response = False
        if parser.has_option(section, option):
            response = parser.get(section, option)
        else:
            self.errors.append(f'{option} is not found in {section} section in {INI_FILE}.')
        return response


    def exit_lcluster(self, message=None):
        """
        Exit from the script with the message.
        """
        sys.stderr.write(colored(f'ERROR :: {message}\n', 'red', attrs=['bold']))
        sys.exit(1)


    def run_cmd(self, cmd=None, timeout=30):
        """
        Returns: return_code, stdout, stderr, exception.
        """
        if not cmd:
            return 255, '', '', 'Empty command'

        try:
            proc = sp.run(cmd, shell=True, stdout=sp.PIPE, stderr=sp.PIPE, text=True, timeout=timeout, check=False)
            return proc.returncode, proc.stdout or '', proc.stderr or '', ''
        except sp.TimeoutExpired as exp:
            return 124, exp.stdout or '', exp.stderr or '', exp
        except sp.SubprocessError as exp:
            return 255, '', '', exp


    def daemon_validation(self):
        """
        Check if Luna daemon is reachable.
        """
        daemon_url = f'{self.daemon}/version'
        try:
            self.session.get(url=daemon_url, timeout=2, verify=self.security)
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(ssl_loop_error)
        except requests.exceptions.ConnectionError as conn_error:
            self.exit_lcluster(conn_error)
        except requests.exceptions.ReadTimeout as time_error:
            self.exit_lcluster(time_error)
        return False


    def token(self):
        """
        Fetch a valid Luna token.
        """
        response = False
        data = {'username': self.username, 'password': self.password}
        daemon_url = f'{self.daemon}/token'
        try:
            call = self.session.post(url=daemon_url, json=data, stream=True, timeout=5, verify=self.security)
            if call.content:
                data = call.json()
                if 'token' in data:
                    response = data['token']
                    with open(TOKEN_FILE, 'w', encoding='utf-8') as file_data:
                        file_data.write(response)
                elif 'message' in data:
                    self.exit_lcluster(f'ERROR :: {data["message"]}.')
            else:
                error = f'ERROR :: Received Nothing {self.daemon}.'
                error = f'{error} ERROR :: HTTP Code {call.status_code}.'
                self.exit_lcluster(error)
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.ConnectionError:
            self.exit_lcluster(f'ERROR :: Unable to Connect => {self.daemon}.')
        except requests.exceptions.JSONDecodeError:
            self.exit_lcluster(f'ERROR :: Response is not JSON {call.content}.')
        return response


    def get_token(self):
        """
        Get a cached Luna token or generate a new one.
        """
        response = False
        if os.path.isfile(TOKEN_FILE):
            with open(TOKEN_FILE, 'r', encoding='utf-8') as token:
                token_data = token.read().strip()
                try:
                    jwt.decode(token_data, self.secret_key, algorithms=['HS256'])
                    response = token_data
                except jwt.exceptions.DecodeError:
                    sys.stderr.write('Token Decode Error, Getting New Token.\n')
                    response = self.token()
                except jwt.exceptions.ExpiredSignatureError:
                    sys.stderr.write('Expired Signature Error, Getting New Token.\n')
                    response = self.token()
        if response is False:
            response = self.token()
        return response


    def post_data(self, url=None, daemon=False, payload=None):
        """
        Make a POST request.
        """
        response = None
        try:
            headers = {'x-access-tokens': self.get_token()} if daemon else None
            response = self.session.post(url=url, json=payload if payload else None, stream=True, headers=headers, timeout=5, verify=self.security)
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
        return response


    def get_data_real(self, url=None, daemon=False, payload=None):
        """
        Make a GET request and return the raw response object.
        """
        response = None
        try:
            headers = {'x-access-tokens': self.get_token()} if daemon else None
            response = self.session.get(url=url, json=payload if payload else None, stream=True, headers=headers, timeout=5, verify=self.security)
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
        except requests.exceptions.RequestException:
            self.exit_lcluster(f'Request Exception on {url}.')
        return response


    def get_data(self, url=None, daemon=False, payload=None):
        """
        Make a GET request and return JSON data.
        """
        response = None
        try:
            headers = {'x-access-tokens': self.get_token()} if daemon else None
            call = self.session.get(url=url, json=payload if payload else None, stream=True, headers=headers, timeout=5, verify=self.security)
            response = call.json()
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
        except requests.exceptions.RequestException:
            self.exit_lcluster(f'Request Exception on {url}.')
        except requests.exceptions.JSONDecodeError:
            self.exit_lcluster(f'Response is not JSON on {url}.')
        return response


    def health_checkup(self):
        """
        Fetch Luna node list, then stream node health rows in chunks.
        """
        node_url = f'{self.daemon}/config/node'
        get_node_list = self._run_with_loader('Fetching Nodes Stats...', self.get_data, node_url, True)
        if not get_node_list:
            self.exit_lcluster(f'No Nodes available with {self.daemon}')

        node_config = get_node_list.get('config', {}).get('node', {})
        if not node_config:
            self.exit_lcluster(f'No Nodes available with {self.daemon}')

        nodes = list(node_config.keys())
        node_status = {node: node_config[node].get('status') for node in nodes}
        node_hostname = {
            node: node_config[node].get('hostname') or node
            for node in nodes
        }

        alertx_state = self._run_with_loader('Fetching AlertX Status...', self.get_overview, list(node_hostname.values()))
        slurm_state = self.call_slurm(nodes)
        widths = self._table_widths(nodes, node_hostname, node_status, alertx_state, slurm_state)
        sys.stdout.write(colored(f'Wait, Fetching IPMI Status of Nodes with {self.daemon} ...\n', 'yellow'))
        self._stream_table_start(widths)

        row_number = 1
        chunks = list(self._chunks(nodes, self.output_chunk_size))
        total_chunks = len(chunks)
        total_nodes = len(nodes)

        for chunk_index, chunk_nodes in enumerate(chunks, start=1):
            start_row = row_number
            end_row = row_number + len(chunk_nodes) - 1
            ipmi_state = self._run_with_loader(f'Fetching Nodes Stats... {start_row}-{end_row}/{total_nodes}', self.get_ipmi_state, chunk_nodes, False)

            rows = []
            for node in chunk_nodes:
                hostname = node_hostname.get(node) or node
                rows.append([
                    self.get_colored(row_number),
                    self.get_colored(node),
                    self.get_colored(hostname),
                    self.get_colored(self._lookup_alertx_status(alertx_state, hostname, node)),
                    self.get_colored(ipmi_state.get(node)),
                    self.get_colored(node_status.get(node)),
                    self.get_colored(slurm_state.get(node)),
                ])
                row_number += 1

            self._stream_table_rows(rows, widths)

            if chunk_index < total_chunks:
                sys.stdout.flush()

        self._stream_table_finish(widths)
        return True


    def get_prometheus_status(self):
        """
        Fetch Prometheus alerting rules. This is intentionally non-fatal:
        """
        if not self.prometheus_status:
            return None

        try:
            response = self.session.get(self.prometheus_status, stream=True, timeout=self.request_timeout, verify=self.security)
            if response.status_code != 200:
                sys.stderr.write(colored(f'WARNING :: AlertX/Prometheus returned HTTP {response.status_code} from {self.prometheus_status}.\n', 'yellow'))
                return None
            return response.json()
        except requests.exceptions.RequestException as exp:
            sys.stderr.write(colored(f'WARNING :: Unable to fetch AlertX status from {self.prometheus_status}: {exp}\n', 'yellow'))
        except ValueError:
            sys.stderr.write(colored(f'WARNING :: AlertX/Prometheus response is not JSON: {self.prometheus_status}\n', 'yellow'))
        return None

    def get_overview(self, hostnames):
        """
        Return one AlertX status per hostname.
        """
        hostnames = [host for host in (hostnames or []) if host]
        alertx_state = {host: 'OK' for host in hostnames}
        host_lookup = {}

        for host in hostnames:
            for key in self._hostname_keys(host):
                host_lookup[key] = host

        rules = self.get_prometheus_status()
        if not rules:
            return {host: 'N/A' for host in hostnames}

        groups = rules.get('data', {}).get('groups', [])
        if not isinstance(groups, list):
            return {host: 'N/A' for host in hostnames}

        for group in groups:
            if group.get('name') not in ['trinityx', 'trinityx_hw']:
                continue

            for rule in group.get('rules', []) or []:
                for alert in rule.get('alerts', []) or []:
                    labels = alert.get('labels', {}) or {}
                    hostname = self._alert_hostname(labels)
                    host = self._match_alertx_host(host_lookup, hostname)
                    if not host:
                        continue

                    if self._alert_disabled(labels):
                        continue

                    rule_state = str(alert.get('state') or rule.get('state') or '').lower()
                    if rule_state not in ['firing', 'active']:
                        continue

                    status = self._alertx_status_from_alert(labels)
                    self._set_worst_alertx_status(alertx_state, host, status)

        return alertx_state


    def _alert_hostname(self, labels):
        """
        Return the best hostname label from a Prometheus alert.
        """
        for key in ['hostname', 'nodename', 'node', 'host', 'instance']:
            value = labels.get(key)
            if value:
                return str(value)
        return None


    def _hostname_keys(self, hostname):
        """
        Build matching keys for FQDN/short-name/host:port variants.
        """
        if not hostname:
            return set()

        host = str(hostname).strip().lower()
        if not host:
            return set()

        if host.count(':') == 1:
            host = host.rsplit(':', 1)[0]

        keys = {host}
        if '.' in host:
            keys.add(host.split('.', 1)[0])
        return keys


    def _match_alertx_host(self, host_lookup, alert_hostname):
        for key in self._hostname_keys(alert_hostname):
            if key in host_lookup:
                return host_lookup[key]
        return None


    def _alert_disabled(self, labels):
        disabled = str(labels.get('disabled', 'false')).strip().lower()
        return disabled in ['1', 'yes', 'true', 'on']


    def _alertx_status_from_alert(self, labels):
        alertname = str(labels.get('alertname', '')).strip().lower()
        category = str(labels.get('category', 'other')).strip().lower()
        nhc = str(labels.get('nhc', 'false')).strip().lower() in ['1', 'yes', 'true', 'on']

        if alertname == 'serverdown' or category == 'down':
            return 'DOWN'

        return 'FAIL + NHC' if nhc else 'FAIL'


    def _set_worst_alertx_status(self, alertx_state, host, status):
        current = alertx_state.get(host, 'OK')
        if ALERTX_STATUS_PRIORITY.get(status, 0) > ALERTX_STATUS_PRIORITY.get(current, 0):
            alertx_state[host] = status


    def _lookup_alertx_status(self, alertx_state, hostname, node):
        for key in [hostname, node]:
            if key in alertx_state:
                return alertx_state[key]

        lookup = {}
        for host in alertx_state:
            for key in self._hostname_keys(host):
                lookup[key] = host

        for key in self._hostname_keys(hostname) | self._hostname_keys(node):
            if key in lookup:
                return alertx_state[lookup[key]]

        return 'N/A'


    def _merge_ipmi_payload(self, nodes, payload, response):
        """
        Merge a Luna control/status payload into response without nested expensive logic.
        """
        control = payload.get('control', {}) if isinstance(payload, dict) else {}
        failed = control.get('failed', {}) or {}
        power = control.get('power', {}) or {}

        power_off = power.get('off', {}) or {}
        power_on = power.get('on', {}) or {}
        power_ok = power.get('ok', {}) or {}

        for node in nodes:
            if node in failed:
                response[node] = failed[node]
            elif node in power_off:
                response[node] = 'OFF'
            elif node in power_on or node in power_ok:
                response[node] = 'ON'
        return response


    def get_ipmi_state(self, nodes, show_message=True):
        """
        Check IPMI State through one bulk Luna request and bounded polling.
        """
        if show_message:
            msg = f'Wait, Fetching IPMI Status of Nodes with {self.daemon} ...\n'
            sys.stdout.write(colored(msg, 'yellow'))
        response = {node: None for node in nodes}

        if not nodes:
            return response

        node_hostlist = collect_hostlist(nodes)
        if not node_hostlist:
            return response

        ipmi_url = f'{self.daemon}/control/action/power/_status'
        payload = {'control': {'power': {'status': {'hostlist': node_hostlist}}}}
        ipmi_response = self.post_data(ipmi_url, True, payload)

        if not ipmi_response or ipmi_response.status_code != 200:
            code = ipmi_response.status_code if ipmi_response else 'NO RESPONSE'
            content = ipmi_response.content if ipmi_response else ''
            error = f'Control is not working as expected ==> {ipmi_url}\n'
            error = f'{error}HTTP ERROR ==> {code}\n'
            error = f'{error}RESPONSE ==> {content}'
            self.exit_lcluster(error)

        http_response = ipmi_response.json()
        self._merge_ipmi_payload(nodes, http_response, response)
        request_id = http_response.get('request_id')
        if not request_id:
            return response

        ipmi_status_url = f'{self.daemon}/control/status/{request_id}'
        deadline = monotonic() + self.ipmi_max_wait

        while monotonic() < deadline:
            if all(value is not None for value in response.values()):
                return response

            sleep(self.ipmi_poll_interval)
            ipmi_status_response = self.get_data_real(ipmi_status_url, True)
            if not ipmi_status_response:
                return response

            if ipmi_status_response.status_code == 404:
                return response

            if ipmi_status_response.status_code != 200:
                sys.stderr.write('Something is wrong with IPMI Service\n')
                return response

            ipmi_status = ipmi_status_response.json()
            self._merge_ipmi_payload(nodes, ipmi_status, response)

        sys.stderr.write(
            colored(
                f'WARNING :: IPMI status polling timed out after {self.ipmi_max_wait:.0f}s. '\
                'Returning the status collected so far.\n',
                'yellow'
            )
        )
        return response


    def call_slurm(self, nodes=None):
        """
        Call Slurm REST if available, otherwise fall back to scontrol/sinfo.
        """
        nodes = nodes or []
        backend = self.choose_slurm()

        if backend['type'] == 'api':
            return self.slurm_api_state(nodes, backend)
        if backend['type'] == 'scontrol':
            return self.slurm_scontrol_state(nodes)
        if backend['type'] == 'sinfo':
            return self.slurm_sinfo_state(nodes)

        return {node: 'SLURM N/A' for node in nodes}


    def choose_slurm(self):
        """
        Decide which Slurm source to use.
        """
        if self.slurm_backend:
            return self.slurm_backend

        rest_backend = self.detect_slurm_rest()
        if rest_backend:
            self.slurm_backend = rest_backend
            sys.stdout.write(colored(f'Using Slurm REST backend: {rest_backend["label"]}\n', 'blue'))
            return self.slurm_backend

        if shutil.which('scontrol'):
            self.slurm_backend = {'type': 'scontrol', 'label': 'scontrol'}
            sys.stdout.write(colored('Using Slurm command backend: scontrol\n', 'blue'))
            return self.slurm_backend

        if shutil.which('sinfo'):
            self.slurm_backend = {'type': 'sinfo', 'label': 'sinfo'}
            sys.stdout.write(colored('Using Slurm command backend: sinfo\n', 'blue'))
            return self.slurm_backend

        self.slurm_backend = {'type': 'none', 'label': 'not available'}
        sys.stderr.write(colored('WARNING :: slurmrestd, scontrol and sinfo are not available.\n', 'yellow'))
        return self.slurm_backend


    def detect_slurm_rest(self):
        """
        Detect a usable slurmrestd endpoint.
        """
        if requests_unixsocket:
            for socket_path in self._discover_slurmrestd_sockets():
                backend = self._probe_slurm_unix_socket(socket_path)
                if backend:
                    return backend
        elif shutil.which('curl'):
            for socket_path in self._discover_slurmrestd_sockets():
                backend = self._probe_slurm_unix_socket_with_curl(socket_path)
                if backend:
                    return backend

        ports = self._detect_slurmrestd_ports()
        if self._is_slurmrestd_installed():
            for port in SLURM_FALLBACK_PORTS:
                if port not in ports:
                    ports.append(port)

        hosts = ['127.0.0.1', 'localhost']
        if self.daemon_host and self.daemon_host not in hosts:
            hosts.append(self.daemon_host)

        for port in ports:
            for host in hosts:
                for scheme in ['http', 'https']:
                    base = f'{scheme}://{host}:{port}'
                    backend = self._probe_slurm_http(base, f'{host}:{port}')
                    if backend:
                        return backend

        return None


    def _is_slurmrestd_installed(self):
        if shutil.which('slurmrestd'):
            return True
        return_code, stdout, _, _ = self.run_cmd(
            'systemctl list-unit-files slurmrestd.service slurmrestd.socket --no-legend 2>/dev/null',
            timeout=3
        )
        return return_code == 0 and 'slurmrestd' in stdout


    def _unique_existing_paths(self, paths):
        """
        Return unique existing filesystem paths, preserving order.
        """
        response = []
        seen = set()
        for item in paths:
            if not item:
                continue
            path = str(item).strip()
            if not path or path in seen:
                continue
            seen.add(path)
            if Path(path).exists():
                response.append(path)
        return response


    def _discover_slurmrestd_sockets(self):
        """
        Discover local slurmrestd UNIX sockets without requiring environment variables.
        """
        candidates = list(SLURM_UNIX_SOCKET_CANDIDATES)

        for unit in ['slurmrestd.socket', 'slurmrestd.service']:
            return_code, stdout, _, _ = self.run_cmd(
                f'systemctl show {unit} --property=Listen --property=ExecStart --value 2>/dev/null',
                timeout=3
            )
            if return_code == 0 and stdout:
                candidates.extend(self._extract_socket_paths(stdout))

        return_code, stdout, _, _ = self.run_cmd('ss -H -lxnp 2>/dev/null', timeout=3)
        if return_code == 0 and stdout:
            for line in stdout.splitlines():
                if 'slurmrestd' in line or 'slurm' in line:
                    candidates.extend(self._extract_socket_paths(line))

        for directory in ['/run/slurmrestd', '/var/run/slurmrestd']:
            directory_path = Path(directory)
            if directory_path.is_dir():
                for child in directory_path.iterdir():
                    if 'slurmrest' in child.name and child.suffix in ['.sock', '.socket', '']:
                        candidates.append(str(child))

        return self._unique_existing_paths(candidates)


    def _extract_socket_paths(self, text):
        """
        Extract absolute socket-looking paths from systemd/ss output.
        """
        paths = []
        for match in re.findall(r'(/[A-Za-z0-9_./-]*slurmrest[A-Za-z0-9_./-]*(?:\.sock(?:et)?|\.socket)?)', text):
            paths.append(match)
        return paths


    def _detect_slurmrestd_ports(self):
        """
        Return TCP ports where slurmrestd appears to be listening.
        """
        ports = []

        return_code, stdout, _, _ = self.run_cmd('ss -H -ltnp 2>/dev/null', timeout=3)
        if return_code == 0:
            for line in stdout.splitlines():
                if 'slurmrestd' not in line:
                    continue
                matches = re.findall(r':(\d+)\b', line)
                for match in matches:
                    port = int(match)
                    if port not in ports:
                        ports.append(port)

        return_code, stdout, _, _ = self.run_cmd(
            'systemctl show slurmrestd.service --property=ExecStart --value 2>/dev/null',
            timeout=3
        )
        if return_code == 0 and stdout:
            for match in re.findall(r':(\d+)\b', stdout):
                port = int(match)
                if port not in ports:
                    ports.append(port)

        return ports


    def _slurm_resource_urls(self, base, resource):
        """
        Build candidate Slurm REST URLs for a resource such as diag or nodes.
        """
        base = str(base).rstrip('/')
        if re.search(r'/slurm/v0\.0\.\d+/(diag|nodes)/?$', base):
            return [re.sub(r'/(diag|nodes)/?$', f'/{resource}', base)]
        if re.search(r'/slurm/v0\.0\.\d+/?$', base):
            return [f'{base}/{resource}']
        return [f'{base}/slurm/{version}/{resource}' for version in SLURM_API_VERSIONS]

    def _probe_slurm_http(self, base_url, label):
        for headers in self._slurm_header_candidates(try_token=True):
            for diag_url in self._slurm_resource_urls(base_url, 'diag'):
                data = self._slurm_get_json(self.session, diag_url, headers)
                if self._looks_like_slurm_diag_response(data):
                    return {
                        'type': 'api',
                        'transport': 'http',
                        'url': re.sub(r'/diag/?$', '/nodes', diag_url),
                        'diag_url': diag_url,
                        'headers': headers,
                        'label': label,
                    }

        for headers in self._slurm_header_candidates(try_token=True):
            for nodes_url in self._slurm_resource_urls(base_url, 'nodes'):
                data = self._slurm_get_json(self.session, nodes_url, headers)
                if self._looks_like_slurm_nodes_response(data):
                    return {
                        'type': 'api',
                        'transport': 'http',
                        'url': nodes_url,
                        'headers': headers,
                        'label': label,
                    }
        return None


    def _probe_slurm_unix_socket(self, socket_path):
        if not requests_unixsocket:
            return None

        session = requests_unixsocket.Session()
        encoded_socket = quote(socket_path, safe='')
        base = f'http+unix://{encoded_socket}'

        for headers in self._slurm_header_candidates(no_auth_first=True, try_token=True):
            for diag_url in self._slurm_resource_urls(base, 'diag'):
                data = self._slurm_get_json(session, diag_url, headers)
                if self._looks_like_slurm_diag_response(data):
                    return {
                        'type': 'api',
                        'transport': 'unix',
                        'url': re.sub(r'/diag/?$', '/nodes', diag_url),
                        'diag_url': diag_url,
                        'headers': headers,
                        'socket_path': socket_path,
                        'label': f'unix socket {socket_path}',
                    }

        for headers in self._slurm_header_candidates(no_auth_first=True, try_token=True):
            for nodes_url in self._slurm_resource_urls(base, 'nodes'):
                data = self._slurm_get_json(session, nodes_url, headers)
                if self._looks_like_slurm_nodes_response(data):
                    return {
                        'type': 'api',
                        'transport': 'unix',
                        'url': nodes_url,
                        'headers': headers,
                        'socket_path': socket_path,
                        'label': f'unix socket {socket_path}',
                    }
        return None


    def _probe_slurm_unix_socket_with_curl(self, socket_path):
        """
        Fallback detector for systems where requests_unixsocket is missing.
        """
        for diag_path in [f'/slurm/{version}/diag' for version in SLURM_API_VERSIONS]:
            data = self._curl_unix_json(socket_path, diag_path)
            if self._looks_like_slurm_diag_response(data):
                version = diag_path.split('/')[2]
                return {
                    'type': 'api',
                    'transport': 'curl_unix',
                    'url': f'/slurm/{version}/nodes',
                    'diag_url': diag_path,
                    'headers': {'Accept': 'application/json'},
                    'socket_path': socket_path,
                    'label': f'unix socket {socket_path} via curl',
                }
        return None


    def _slurm_header_candidates(self, no_auth_first=True, try_token=False):
        """
        Yield header candidates lazily.
        """
        base_headers = {'Accept': 'application/json'}
        if no_auth_first:
            yield base_headers.copy()

        if try_token:
            username, token = self.get_slurm_rest_auth(lazy=False)
            if username and token:
                auth_headers = base_headers.copy()
                auth_headers['X-SLURM-USER-NAME'] = username
                auth_headers['X-SLURM-USER-TOKEN'] = token
                yield auth_headers

        if not no_auth_first:
            yield base_headers.copy()


    def _slurm_headers(self):
        """
        Backwards-compatible helper: return the strongest available Slurm REST headers.
        """
        candidates = list(self._slurm_header_candidates(try_token=True))
        return candidates[-1] if candidates else {'Accept': 'application/json'}


    def get_slurm_rest_auth(self, lazy=False):
        """
        Get Slurm REST username/token.
        """
        if self.slurm_auth is not None:
            return self.slurm_auth

        username = getpass.getuser()
        token = None

        if lazy:
            return username, token

        if shutil.which('scontrol'):
            for cmd in [
                f'scontrol token username={shlex.quote(username)} lifespan=600',
                'scontrol token lifespan=600',
            ]:
                return_code, stdout, stderr, _ = self.run_cmd(cmd, timeout=5)
                if return_code != 0:
                    continue
                output = f'{stdout}\n{stderr}'
                match = re.search(r'SLURM_JWT=([^\s]+)', output)
                if match:
                    token = match.group(1)
                    break

        self.slurm_auth = (username, token)
        return self.slurm_auth


    def _slurm_get_json(self, session, url, headers):
        try:
            call = session.get(
                url,
                headers=headers,
                timeout=self.slurm_timeout,
                verify=self.slurm_verify,
            )
            if call.status_code != 200:
                return None
            return call.json()
        except Exception:
            return None


    def _curl_unix_json(self, socket_path, path):
        """
        GET a Slurm REST path over a UNIX socket using curl.
        """
        cmd = (
            f'curl -sS --max-time {int(self.slurm_timeout)} '
            f'--unix-socket {shlex.quote(socket_path)} '
            f'{shlex.quote("http://slurm" + path)}'
        )
        return_code, stdout, _, _ = self.run_cmd(cmd, timeout=int(self.slurm_timeout) + 2)
        if return_code != 0 or not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None


    def _looks_like_slurm_diag_response(self, data):
        if not isinstance(data, dict):
            return False
        meta = data.get('meta', {})
        plugin = meta.get('plugin', {}) if isinstance(meta, dict) else {}
        plugin_type = plugin.get('type') if isinstance(plugin, dict) else ''
        return (
            isinstance(data.get('statistics'), dict) or
            plugin_type == 'openapi/slurmctld' or
            'Slurm OpenAPI' in str(plugin.get('name', ''))
        )


    def _looks_like_slurm_nodes_response(self, data):
        return isinstance(data, dict) and isinstance(data.get('nodes'), list)


    def _normalise_slurm_state(self, state):
        if state is None:
            return False
        if isinstance(state, list):
            state = '+'.join(str(item) for item in state)
        elif isinstance(state, dict):
            state = state.get('state') or state.get('name') or json.dumps(state)
        state = str(state).strip()
        if not state:
            return False
        return state.lower()


    def slurm_api_state(self, nodes, backend):
        """
        Fetch Slurm node state once through REST, then map by node name.
        """
        response = {node: False for node in nodes}

        if backend.get('transport') == 'curl_unix':
            data = self._curl_unix_json(backend['socket_path'], backend['url'])
        else:
            session = self.session
            if backend.get('transport') == 'unix' and requests_unixsocket:
                session = requests_unixsocket.Session()
            data = self._slurm_get_json(session, backend['url'], backend.get('headers', {}))

        if not self._looks_like_slurm_nodes_response(data):
            sys.stderr.write(colored('WARNING :: Slurm REST failed after detection. Falling back to scontrol.\n', 'yellow'))
            if shutil.which('scontrol'):
                return self.slurm_scontrol_state(nodes)
            if shutil.which('sinfo'):
                return self.slurm_sinfo_state(nodes)
            return response

        by_name = {}
        for item in data.get('nodes', []):
            if not isinstance(item, dict):
                continue
            name = item.get('name') or item.get('hostname')
            if name:
                by_name[name] = self._normalise_slurm_state(item.get('state'))

        for node in nodes:
            response[node] = by_name.get(node, False)
        return response


    def slurm_scontrol_state(self, nodes):
        """
        Fetch Slurm node state using scontrol. Prefer JSON when supported, otherwise parse `scontrol show nodes -o`.
        """
        response = {node: False for node in nodes}
        wanted = set(nodes)

        return_code, stdout, _, _ = self.run_cmd('scontrol show nodes --json', timeout=30)
        if return_code == 0 and stdout.strip().startswith('{'):
            try:
                data = json.loads(stdout)
                for item in data.get('nodes', []):
                    name = item.get('name') or item.get('hostname')
                    if name in wanted:
                        response[name] = self._normalise_slurm_state(item.get('state'))
                return response
            except json.JSONDecodeError:
                pass

        return_code, stdout, _, _ = self.run_cmd('scontrol show nodes -o', timeout=30)
        if return_code == 0 and stdout:
            for line in stdout.splitlines():
                name_match = re.search(r'\bNodeName=(\S+)', line)
                state_match = re.search(r'\bState=(\S+)', line)
                if not name_match or not state_match:
                    continue
                name = name_match.group(1)
                state = self._normalise_slurm_state(state_match.group(1))
                if name in wanted:
                    response[name] = state
            return response

        if shutil.which('sinfo'):
            return self.slurm_sinfo_state(nodes)

        return response


    def slurm_sinfo_state(self, nodes):
        """
        Final Slurm command fallback. One sinfo call, one dictionary lookup per node.
        """
        response = {node: False for node in nodes}
        return_code, stdout, _, _ = self.run_cmd('sinfo -N -h -o "%N|%T"', timeout=30)
        if return_code != 0 or not stdout:
            return response

        by_name = {}
        for line in stdout.splitlines():
            if '|' not in line:
                continue
            name, state = line.split('|', 1)
            by_name[name.strip()] = self._normalise_slurm_state(state)

        for node in nodes:
            response[node] = by_name.get(node, False)
        return response


    def loader(self, message=None, stop_event=None):
        """
        Terminal loader shown while a slow fetch is running.
        """
        animation = [
            f"[=       ] {message}",
            f"[===     ] {message}",
            f"[====    ] {message}",
            f"[=====   ] {message}",
            f"[======  ] {message}",
            f"[======= ] {message}",
            f"[========] {message}",
            f"[ =======] {message}",
            f"[  ======] {message}",
            f"[   =====] {message}",
            f"[    ====] {message}",
            f"[     ===] {message}",
            f"[      ==] {message}",
            f"[       =] {message}",
            f"[        ] {message}",
            f"[        ] {message}",
        ]

        if stop_event is None:
            stop_event = threading.Event()

        index = 0
        try:
            while not stop_event.is_set():
                sys.stdout.write(colored(animation[index % len(animation)], 'yellow') + '\r')
                sys.stdout.flush()
                sleep(0.1)
                index += 1
        except KeyboardInterrupt:
            stop_event.set()
            return False
        return True


    def _clear_loader_line(self):
        """
        Clear the current terminal line used by the loader.
        """
        sys.stdout.write('\r' + (' ' * 240) + '\r')
        sys.stdout.flush()


    def _run_with_loader(self, message, function, *args, **kwargs):
        """
        Run a blocking function while showing the loader.
        """
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self.loader,
            args=(message, stop_event),
            daemon=True,
        )
        thread.start()
        try:
            return function(*args, **kwargs)
        finally:
            stop_event.set()
            thread.join(timeout=1)
            self._clear_loader_line()


    def _chunks(self, items, size):
        """
        Yield a list in fixed-size chunks.
        """
        size = int(size) if str(size).isdigit() else DEFAULT_OUTPUT_CHUNK_SIZE
        size = max(size, 1)
        for index in range(0, len(items), size):
            yield items[index:index + size]


    def _visible_len(self, text):
        """
        Return visible terminal length, ignoring ANSI colour escape sequences.
        """
        return len(ANSI_ESCAPE_RE.sub('', str(text)))


    def _ansi_center(self, text, width):
        """
        Centre a possibly-coloured string inside a fixed visible width.
        """
        value = str(text)
        visible = self._visible_len(value)
        padding = max(width - visible, 0)
        left = padding // 2
        right = padding - left
        return f"{' ' * left}{value}{' ' * right}"


    def _ansi_ljust(self, text, width):
        """
        Left-align a possibly-coloured string inside a fixed visible width.
        """
        value = str(text)
        padding = max(width - self._visible_len(value), 0)
        return f"{value}{' ' * padding}"


    def _table_widths(self, nodes, node_hostname, node_status, alertx_state, slurm_state):
        """
        Calculate stable table widths before streaming begins.
        """
        headers = ['#', 'Node Name', 'Hostname', 'AlertX', 'IPMI', 'Luna', 'SLURM']

        raw_rows = []
        for index, node in enumerate(nodes, start=1):
            hostname = node_hostname.get(node) or node
            raw_rows.append([
                index,
                node,
                hostname,
                self._lookup_alertx_status(alertx_state, hostname, node),
                'NOT RESPONDING',
                node_status.get(node) or 'N/A',
                slurm_state.get(node) or 'N/A',
            ])

        width_values = [headers]
        width_values.extend(raw_rows)
        width_values.append(['#', 'Node Name', 'Hostname', 'FAIL + NHC', 'NOT RESPONDING', 'WARNING', 'DOWN+NOT_RESPONDING'])

        widths = []
        for column_index in range(len(headers)):
            measured_width = max(
                self._visible_len(row[column_index])
                for row in width_values
            )
            max_width = TABLE_COLUMN_MAX_WIDTHS[column_index]
            widths.append(min(measured_width, max_width))
        return widths


    def _table_border(self, widths):
        return '+' + '+'.join('-' * (width + 2) for width in widths) + '+'


    def _wrap_cell(self, value, width):
        """
        Wrap a possibly-coloured cell to the target visible width.
        """
        value = str(value)
        plain = ANSI_ESCAPE_RE.sub('', value)

        prefix_match = re.match(r'^((?:\x1b\[[0-9;]*m)+)', value)
        prefix = prefix_match.group(1) if prefix_match else ''
        suffix = '\033[0m' if prefix and value.endswith('\033[0m') else ''

        wrapped = textwrap.wrap(
            plain,
            width=max(int(width), 1),
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=False,
        )
        if not wrapped:
            wrapped = ['']

        if prefix:
            return [f'{prefix}{line}{suffix}' if line else '' for line in wrapped]
        return wrapped


    def _table_row_lines(self, values, widths):
        """
        Return one or more printable table lines for a logical row.
        """
        wrapped_cells = [
            self._wrap_cell(value, width)
            for value, width in zip(values, widths)
        ]
        row_height = max(len(cell) for cell in wrapped_cells) if wrapped_cells else 1

        lines = []
        for line_index in range(row_height):
            cells = []
            for cell_lines, width in zip(wrapped_cells, widths):
                cell_value = cell_lines[line_index] if line_index < len(cell_lines) else ''
                cells.append(self._ansi_center(cell_value, width))
            lines.append('| ' + ' | '.join(cells) + ' |')
        return lines


    def _table_row(self, values, widths):
        return self._table_row_lines(values, widths)[0]


    def _stream_table_start(self, widths):
        """
        Print table title and header once.
        """
        border = self._table_border(widths)
        title = colored('<< Health & Status of Nodes >>', 'cyan', attrs=['bold'])
        title_width = len(border) - 4
        headers = [
            colored('#', 'yellow', attrs=['bold']),
            colored('Node Name', 'yellow', attrs=['bold']),
            colored('Hostname', 'yellow', attrs=['bold']),
            colored('AlertX', 'yellow', attrs=['bold']),
            colored('IPMI', 'yellow', attrs=['bold']),
            colored('Luna', 'yellow', attrs=['bold']),
            colored('SLURM', 'yellow', attrs=['bold']),
        ]

        print(border)
        print(f"| {self._ansi_center(title, title_width)} |")
        print(border)
        print(self._table_row(headers, widths))
        print(border)


    def _stream_table_rows(self, rows, widths):
        """
        Print a batch of already-coloured rows.
        """
        for row in rows:
            for line in self._table_row_lines(row, widths):
                print(line)
        sys.stdout.flush()


    def _stream_table_finish(self, widths):
        """
        Print the final bottom border.
        """
        print(self._table_border(widths))
        sys.stdout.flush()


    def get_colored(self, text=None):
        """
        Apply terminal colour to a table value.
        """
        raw = text
        value = str(text).lower() if text is not None else ''

        if raw is True or raw in ['PASS', 'ON', 'OK']:
            return colored(raw, 'green')
        if raw in ['OFF', 'WARNING', 'FAIL']:
            return colored(raw, 'yellow')
        if raw == 'FAIL + NHC':
            return colored(raw, 'red')
        if value == 'down' or value.startswith('down') or value in ['fail', 'failed', 'not responding']:
            return colored(str(raw).upper(), 'red')
        if value.startswith('idle') or value == 'allocated':
            return colored(str(raw).upper(), 'green')
        if any(flag in value for flag in ['drain', 'maint', 'mixed', 'alloc', 'comp', 'reserved']):
            return colored(str(raw).upper(), 'yellow')
        if raw is False or raw is None or raw == 'N/A':
            return colored('N/A', 'light_blue')
        return colored(raw, 'light_blue')


    def show_table(self, rows=None):
        """
        Show health/status table.
        """
        self.table.title = colored('<< Health & Status of Nodes >>', 'cyan', attrs=['bold'])
        fields = ['#', 'Node Name', 'Hostname', 'AlertX', 'IPMI', 'Luna', 'SLURM']
        self.table.field_names = [colored(each, 'yellow', attrs=['bold']) for each in fields]
        self.table.add_rows(rows or [])
        print(self.table)
        return True


def main():
    """
    Main entry point.
    """
    try:
        return LCluster().health_checkup()
    except KeyboardInterrupt:
        sys.stderr.write("\nKeyboard Interrupted.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
