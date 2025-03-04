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
lcluster Utility for Trinity Project
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2025, Luna2 Project [UTILITY]"
__license__     = "GPL"
__version__     = "2.1"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Development"

import os
import sys
from time import sleep
from configparser import RawConfigParser
import subprocess as sp
from threading import Timer
from hostlist import collect_hostlist
import requests
from requests import Session
from requests.adapters import HTTPAdapter
import jwt
import urllib3
from urllib3.util import Retry
from prettytable import PrettyTable
from termcolor import colored
import requests_unixsocket

TOKEN_FILE = '/trinity/local/luna/utils/config/token.txt'
INI_FILE = '/trinity/local/luna/utils/config/luna.ini'


class LCluster():
    """
    LCluster Class responsible to all Monitoring activities.
    """

    def __init__(self):
        """
        Default variables should be here before calling the any method.
        """
        self.errors = []
        self.username = None
        self.password = None
        self.daemon = None
        self.secret_key = None
        self.protocol = None
        self.security = ''
        self.table = PrettyTable()
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
                self.security = True if self.security.lower() in ['y', 'yes', 'true']  else False
                if ':' in self.daemon:
                    hostname, port =  self.daemon.split(':')
                    self.slurm_url = f"http://{hostname}:6802/slurm/v0.0.38/nodes"
                self.daemon = f'{self.protocol}://{self.daemon}'
            else:
                self.errors.append(f'API section is not found in {INI_FILE}.')
        else:
            self.errors.append(f'{INI_FILE} is not found on this machine.')
        if self.errors:
            sys.stderr.write('You need to fix following errors...\n')
            num = 1
            for error in self.errors:
                sys.stderr.write(f'{num}. {error}\n')
            sys.exit(1)

        urllib3.disable_warnings()
        self.session = Session()
        self.retries = Retry(
            total= 60,
            backoff_factor=0.1,
            status_forcelist=[502, 503, 504],
            allowed_methods={'GET', 'POST'},
        )
        self.session.mount('https://', HTTPAdapter(max_retries=self.retries))
        self.daemon_validation()


    def get_option(self, parser=None, section=None, option=None):
        """
        This method will retrieve the value from the INI
        """
        response = False
        if parser.has_option(section, option):
            response = parser.get(section, option)
        else:
            self.errors.append(f'{option} is not found in {section} section in {INI_FILE}.')
        return response


    def exit_lcluster(self, message=None):
        """
        This method will exit from the script with the message
        """
        sys.stderr.write(colored(f'ERROR :: {message}\n', 'red', attrs=['bold']))
        sys.exit(1)


    def daemon_validation(self):
        """
        This method will fetch a valid token for further use.
        """
        check = False
        daemon_url = f'{self.daemon}/version'
        try:
            requests.get(url=daemon_url, timeout=2, verify=False)
        except requests.exceptions.SSLError as ssl_loop_error:
            check = True
            self.exit_lcluster(ssl_loop_error)
        except requests.exceptions.ConnectionError as conn_error:
            check = True
            self.exit_lcluster(conn_error)
        except requests.exceptions.ReadTimeout as time_error:
            check = True
            self.exit_lcluster(time_error)
        if check is True:
            exception = f'ERROR :: Unable to reach {daemon_url} Try again or check the config'
            self.exit_lcluster(exception)
        return check


    def token(self):
        """
        This method will fetch a valid token for further use.
        """
        data = {'username': self.username, 'password': self.password}
        daemon_url = f'{self.daemon}/token'
        try:
            
            call = self.session.post(
                url=daemon_url,
                json=data,
                stream=True,
                timeout=5,
                verify=self.security
            )
            if call.content:
                data = call.json()
                if 'token' in data:
                    response = data['token']
                    with open(TOKEN_FILE, 'w', encoding='utf-8') as file_data:
                        file_data.write(response)
                elif 'message' in data:
                    self.exit_lcluster(f'ERROR :: {data["token"]}.')
            else:
                error = f'ERROR :: Received Nothing {self.daemon}.'
                error = f'{error}ERROR :: HTTP Code {call.status_code}.'
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
        This method will fetch a valid token for further use.
        """
        response = False
        if os.path.isfile(TOKEN_FILE):
            with open(TOKEN_FILE, 'r', encoding='utf-8') as token:
                token_data = token.read()
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


    def health_checkup(self):
        """
        Method to call the class for further operations.
        """
        node_url = f'{self.daemon}/config/node'
        get_node_list = self.get_data(node_url, True)
        slurm = self.choose_slurm()
        if get_node_list:
            response, nodes = [], []
            node_status = {}
            if 'config' in get_node_list:
                for node in get_node_list['config']['node']:
                    nodes.append(node)
                    node_status[node] = get_node_list['config']['node'][node]['status']
                ipmi_state = self.get_ipmi_state(nodes)
                slurm_state = self.call_slurm(slurm, nodes)

                count = 1
                for node, _ in ipmi_state.items():
                    response.append([
                        self.get_colored(count),
                        self.get_colored(node),
                        self.get_colored(ipmi_state[node]),
                        self.get_colored(node_status[node]),
                        self.get_colored(slurm_state[node]),
                        ]
                    )
                    count = count + 1
                self.show_table(response)
            else:
                self.exit_lcluster(f'No Nodes available with {self.daemon}')
        else:
            self.exit_lcluster(f'No Nodes available with {self.daemon}')


    def get_ipmi_state(self, nodes):
        """
        Check IPMI State
        """
        msg = f'Wait, Fetching IMPI Status of Nodes with {self.daemon} ...\n'
        response = {}
        sys.stdout.write(colored(msg, 'yellow'))
        node_hostlist = collect_hostlist(nodes)
        if node_hostlist:
            ipmi_url = f'{self.daemon}/control/action/power/_status'
            payload = {'control': {'power': {'status': {'hostlist': node_hostlist}}}}
            ipmi_response = self.post_data(ipmi_url, True, payload)
            if ipmi_response.status_code == 200:
                http_response = ipmi_response.json()
                if 'request_id' in http_response:
                    request_id = http_response['request_id']
                ipmi_status_url = f'{self.daemon}/control/status/{request_id}'
                for node in nodes:
                    if 'failed' in http_response['control']:
                        if node in http_response['control']['failed'].keys():
                            response[node] = http_response['control']['failed'][node]
                        elif node in http_response['control']['power']['off'].keys():
                            response[node] = 'OFF'
                        elif node in http_response['control']['power']['on'].keys():
                            response[node] = 'ON'
                        elif node in http_response['control']['power']['ok'].keys():
                            response[node] = 'ON'
                        else:
                            response[node] = None
                    else:
                        response[node] = None

                def get_status_ipmi(ipmi_status_url, response):
                    sleep(2)
                    ipmi_status_response = self.get_data_real(ipmi_status_url, True)
                    if ipmi_status_response.status_code == 200:
                        ipmi_status = ipmi_status_response.json()
                        if 'power' in ipmi_status['control']:
                            for node in nodes:
                                if node in ipmi_status['control']['failed'].keys():
                                    response[node] = ipmi_status['control']['failed'][node]
                                elif node in ipmi_status['control']['power']['off'].keys():
                                    response[node] = 'OFF'
                                elif node in ipmi_status['control']['power']['on'].keys():
                                    response[node] = 'ON'
                                elif node in ipmi_status['control']['power']['ok'].keys():
                                    response[node] = 'ON'
                                # else:
                                #     response[node] = None
                            return get_status_ipmi(ipmi_status_url, response)
                        else:
                            return get_status_ipmi(ipmi_status_url, response)
                    elif ipmi_status_response.status_code == 404:
                        return response
                    else:
                        sys.stderr.write('Something is wrong with IPMI Service\n')
                        return response
                response = get_status_ipmi(ipmi_status_url, response)
            else:
                error = f'Control is not working as expected ==> {ipmi_url}\n'
                error = f'{error}HTTP ERROR ==> {ipmi_response.status_code}\n'
                error = f'{error}RESPONSE ==> {ipmi_response.content}'
                self.exit_lcluster(error)
        return response


    def post_data(self, url=None, daemon=False, payload=None):
        """
        This method will make a get request and fetch the data accordingly.
        """
        response = None
        try:
            if daemon:
                headers = {'x-access-tokens': self.get_token()}
                if payload:
                    response = self.session.post(
                        url=url,
                        json=payload,
                        stream=True,
                        headers=headers,
                        timeout=5,
                        verify=self.security
                    )
                else:
                    response = self.session.post(
                        url=url,
                        stream=True,
                        headers=headers,
                        timeout=5,
                        verify=self.security
                    )
            else:
                response = self.session.post(url=url, stream=True, timeout=5, verify=self.security)
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
        # except requests.exceptions.RequestException:
        #     self.exit_lcluster(f'Request Exception on {url}.')
        return response


    def get_data_real(self, url=None, daemon=False, payload=None):
        """
        This method will make a get request and fetch the data accordingly.
        """
        try:
            if daemon:
                headers = {'x-access-tokens': self.get_token()}
                if payload:
                    call = self.session.get(
                        url=url,
                        json=payload,
                        stream=True,
                        headers=headers,
                        timeout=5,
                        verify=self.security
                    )
                else:
                    call = self.session.get(
                        url=url,
                        stream=True,
                        headers=headers,
                        timeout=5,
                        verify=self.security
                    )
            else:
                call = self.session.get(url=url, stream=True, timeout=5, verify=self.security)
            response = call
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
            response = None
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
            response = None
        except requests.exceptions.RequestException:
            self.exit_lcluster(f'Request Exception on {url}.')
            response = None
        return response


    def get_data(self, url=None, daemon=False, payload=None):
        """
        This method will make a get request and fetch the data accordingly.
        """
        try:
            if daemon:
                headers = {'x-access-tokens': self.get_token()}
                if payload:
                    call = self.session.get(
                        url=url,
                        json=payload,
                        stream=True,
                        headers=headers,
                        timeout=5,
                        verify=self.security
                    )
                else:
                    call = self.session.get(
                        url=url,
                        stream=True,
                        headers=headers,
                        timeout=5,
                        verify=self.security
                    )
            else:
                call = self.session.get(url=url, stream=True, timeout=5, verify=self.security)
            response = call.json()
        except requests.exceptions.SSLError as ssl_loop_error:
            self.exit_lcluster(f'ERROR :: {ssl_loop_error}')
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
            response = None
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
            response = None
        except requests.exceptions.RequestException:
            self.exit_lcluster(f'Request Exception on {url}.')
            response = None
        return response


    def get_colored(self, text=None):
        """
        This method will fetch a records from the Luna 2 Daemon Database
        """
        if text is True or text in ['PASS', 'ON']:
            text = colored(text, 'green')
        elif text in ['OFF', 'WARNING']:
            text = colored(text, 'yellow')
        elif text == 'down*':
            text = colored('DOWN', 'red')
        elif text == 'idle':
            text = colored('IDLE', 'green')
        else:
            text = colored(text, 'light_blue')
        return text


    def show_table(self, rows=None):
        """
        This method will fetch a records from
        the Luna 2 Daemon Database
        """
        self.table.title = colored('<< Health & Status of Nodes >>', 'cyan', attrs=['bold'])
        fields = ['#', 'Node', 'IPMI', 'Luna', 'SLURM']
        field = []
        for each in fields:
            field.append(colored(each, 'yellow', attrs=['bold']))
        self.table.field_names = field
        self.table.add_rows(rows)
        print(self.table)
        return True

    def choose_slurm(self):
        """
        This method find out slurm version and decide, if use slurm cmd or api
        """
        response = 'cmd'
        cmd = '/usr/sbin/slurmd -V | cut -d " " -f2'
        return_code, stdout, stderr, exception= self.run_cmd(cmd)

        if not return_code and not stderr and not exception:
            stdout = str(stdout).replace("b", '')
            stdout = stdout.replace("'", '')
            stdout = stdout.replace("\\n", '')
            stdout = stdout.replace(".", '')
            stdout = stdout[:4]
            if int(stdout) >= 2022:
                response = 'api'
        return response


    def call_slurm(self, state=None, nodes=None):
        """
        This method will call the slurm cmd or api method depends on the state.
        """
        if 'api' in state:
            response = self.slurm_api_state(nodes)
        elif 'cmd' in state:
            response = self.slurm_cmd_state(nodes)
        else:
            response = False
        return response


    def slurm_cmd_state(self, nodes):
        """
        This method fetch slurm state via sinfo command and filter the actual state
        """
        response = {}
        for node in nodes:
            response[node] = False
        cmd = 'sinfo -N -o "%N %6T"'
        return_code, stdout, _, _ = self.run_cmd(cmd)
        if not return_code:
            stdout = str(stdout).replace("b", '')
            stdout = stdout.replace("'", '')
            stdout = stdout.split('\\n')
            for node in nodes:
                for res in stdout:
                    if node in res:
                        res = res.replace(node, '')
                        res = res.replace(' ', '')
                        response[node] = res
        return response


    def slurm_api_state(self, nodes):
        """
        This method find out the slurm state for all nodes from slurm api.
        """
        response, slurm_response = {}, {}
        socket_cmd = 'http+unix://%2Fvar%2Flib%2Fslurmrestd.socket/slurm/v0.0.38/nodes'
        for node in nodes:
            response[node] = False
        try:
            session = requests_unixsocket.Session()
            slurm_call = session.get(socket_cmd)
            slurm_response = slurm_call.json()
        except Exception as exp:
            # print(f"WARNING :: While Calling Slurm API Socket {socket_cmd}")
            # print(f"           UNIX SOCKET Exception happen {exp}")
            try:
                headers = {'X-SLURM-USER-NAME': 'USERNAME', 'X-SLURM-USER-TOKEN': 'TOKEN'}
                call_slurm = requests.get(
                    url=self.slurm_url,
                    headers=headers,
                    timeout=5,
                    verify=self.security
                )
                slurm_response = call_slurm.json()
            except requests.exceptions.Timeout:
                print(f'Timeout on {self.slurm_url}.')
                for node in nodes:
                    response[node] = "TIME OUT"
            except requests.exceptions.TooManyRedirects:
                print(f'Too Many Redirects on {self.slurm_url}.')
                for node in nodes:
                    response[node] = "SLURM Redirect"
            except requests.exceptions.RequestException:
                # print(f'WARNING :: Request Exception on {self.slurm_url}.')
                response = self.slurm_cmd_state(nodes)
        if slurm_response:
            for node in nodes:
                for state in slurm_response['nodes']:
                    if node == state['name']:
                        response[node] = state['state']
        return response


    def run_cmd(self, cmd=None, timeout=30):
        """
        Returns 'return_code', 'stdout', 'stderr', 'exception'
        Where 'exception' is a content of Python exception if any
        """
        return_code = 255
        stdout, stderr, exception = "", "", ""
        try:
            proc = sp.Popen(cmd, shell=True, stdout=sp.PIPE, stderr=sp.PIPE)
            timer = Timer(timeout, lambda p: p.kill(), [proc])
            try:
                timer.start()
                stdout, stderr = proc.communicate()
            except sp.TimeoutExpired as exp:
                print(f"Subprocess Timeout executing {exp}")
            finally:
                timer.cancel()
            proc.wait()
            return_code = proc.returncode
        except sp.SubprocessError as exp:
            print(f"Subprocess Error executing {cmd} Exception is {exp}")
            exception = exp
        return return_code, stdout, stderr, exception


def main():
    """
    This main method will initiate the script for pip installation.
    """
    return LCluster().health_checkup()


if __name__ == "__main__":
    LCluster().health_checkup()
