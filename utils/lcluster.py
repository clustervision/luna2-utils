#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
lcluster Utility for Trinity Project
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2022, Luna2 Project [UTILITY]"
__license__     = "GPL"
__version__     = "2.0"
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
import jwt
import urllib3
from prettytable import PrettyTable
from termcolor import colored
import requests_unixsocket

TOKEN_FILE = '/tmp/token.txt'
INI_FILE = '/trinity/local/luna/config/luna.ini'


class LCluster():
    """
    LCluster Class responsible to all Monitoring activities.
    """

    def __init__(self):
        """
        Default variables should be here before calling the any method.
        """
        self.errors = []
        self.username, self.password, self.daemon, self.secret_key, self.protocol, self.security = None, None, None, None, None, ''
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
                    sensu_url = self.daemon.split(':')
                    self.sensu_url = f"http://{sensu_url[0]}:3001/events"
                    self.slurm_url = f"http://{sensu_url[0]}:6802/slurm/v0.0.38/nodes"
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


    def token(self):
        """
        This method will fetch a valid token for further use.
        """
        data = {'username': self.username, 'password': self.password}
        daemon_url = f'{self.daemon}/token'
        try:
            call = requests.post(url=daemon_url, json=data, timeout=5, verify=self.security)
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
        sensu_data = self.get_data(self.sensu_url)
        slurm = self.choose_slurm()
        if get_node_list:
            response, nodes = [], []
            node_status, sensu_state = {}, {}
            for node in get_node_list['config']['node']:
                nodes.append(node)
                if 'hostname' in get_node_list['config']['node'][node]:
                    sensu_state[get_node_list['config']['node'][node]['hostname']] = False
                else:
                    sensu_state[node] = False
                node_status[node] = get_node_list['config']['node'][node]['status']
            ipmi_state = self.get_ipmi_state(nodes)
            slurm_state = self.call_slurm(slurm, nodes)
            sensu_state = self.check_sensu(sensu_state, sensu_data)
            count = 1
            for node, _ in ipmi_state.items():
                response.append([
                    self.get_colored(count),
                    self.get_colored(node),
                    self.get_colored(ipmi_state[node]),
                    self.get_colored(node_status[node]),
                    self.get_colored(slurm_state[node]),
                    self.get_colored(sensu_state[get_node_list['config']['node'][node]['hostname']])
                    ]
                )
                count = count + 1
            self.show_table(response)
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
                request_id = http_response['control']['request_id']
                ipmi_status_url = f'{self.daemon}/control/status/{request_id}'

                for node in nodes:
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
                                else:
                                    response[node] = None
                            return get_status_ipmi(ipmi_status_url, response)
                        else:
                            return response
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
                    response = requests.post(url=url, headers=headers, json=payload, timeout=5, verify=self.security)
                else:
                    response = requests.post(url=url, headers=headers, timeout=5, verify=self.security)
            else:
                response = requests.get(url=url , timeout=5, verify=self.security)
        except requests.exceptions.Timeout:
            self.exit_lcluster(f'Timeout on {url}.')
        except requests.exceptions.TooManyRedirects:
            self.exit_lcluster(f'Too Many Redirects on {url}.')
        # except requests.exceptions.RequestException:
        #     self.exit_lcluster(f'Request Exception on {url}.')
        return response


    def get_data_real(self, url=None, daemon=False, payload=None):
        """
        This method will make a get request and fetch the data
        accordingly.
        """
        try:
            if daemon:
                headers = {'x-access-tokens': self.get_token()}
                if payload:
                    call = requests.get(url=url, headers=headers, json=payload, timeout=5, verify=self.security)
                else:
                    call = requests.get(url=url, headers=headers, timeout=5, verify=self.security)
            else:
                call = requests.get(url=url , timeout=5, verify=self.security)
            # response = call.json()
            response = call
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
        This method will make a get request and fetch the data
        accordingly.
        """
        try:
            if daemon:
                headers = {'x-access-tokens': self.get_token()}
                if payload:
                    call = requests.get(url=url, headers=headers, json=payload, timeout=5, verify=self.security)
                else:
                    call = requests.get(url=url, headers=headers, timeout=5, verify=self.security)
            else:
                call = requests.get(url=url , timeout=5, verify=self.security)
            response = call.json()
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
        This method will fetch a records from
        the Luna 2 Daemon Database
        """
        if text is True or text in ['PASS', 'ON']:
            text = colored(text, 'green')
        elif text is False or text in ['CRITICAL', 'Sensu Down'] or text is None:
            text = colored(text, 'red')
        elif text in ['OFF', 'WARNING']:
            text = colored(text, 'yellow')
        else:
            text = colored(text, 'blue')
        return text


    def show_table(self, rows=None):
        """
        This method will fetch a records from
        the Luna 2 Daemon Database
        """
        self.table.title = colored('<< Health & Status of Nodes >>', 'cyan', attrs=['bold'])
        fields = ['S. No.', 'Node', 'IPMI', 'Luna', 'SLURM', 'Sensu']
        field = []
        for each in fields:
            field.append(colored(each, 'yellow', attrs=['bold']))
        self.table.field_names = field
        self.table.add_rows(rows)
        print(self.table)
        return True


    def node_status(self, node):
        """
        This method return the status of a node.
        """
        response = False
        monitor_url = f'{self.daemon}/monitor/status/{node}'
        data = self.get_data(monitor_url, True)
        if data:
            response = data['monitor']['status'][node]['state']
        return response


    def check_sensu(self, nodelist=None, sensu_data=None):
        """
        This method will filter the correct status for the Sensu
        for a node.
        """
        if sensu_data:
            for node in nodelist:
                for data in sensu_data:
                    if node in data['client']['name']:
                        if data['check']['status'] == 1:
                            nodelist[node] = "WARNING"
                        elif data['check']['status'] == 2:
                            nodelist[node] = "CRITICAL"
                        else:
                            nodelist[node] = "UNKNOWN"
                    else:
                        nodelist[node] = "PASS"
        else:
            for node in nodelist:
                nodelist[node] = "PASS"
        return nodelist


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
        for node in nodes:
            response[node] = False
        try:
            session = requests_unixsocket.Session()
            socket_cmd = 'http+unix://%2Fvar%2Flib%2Fslurmrestd.socket/slurm/v0.0.38/nodes'
            slurm_call = session.get(socket_cmd)
            slurm_response = slurm_call.json()
        except Exception as exp:
            print(f"UNIX SOCKET Exception while calling Slurm {exp}")
            try:
                headers = {'X-SLURM-USER-NAME': 'USERNAME', 'X-SLURM-USER-TOKEN': 'TOKEN'}
                call_slurm = requests.get(url=self.slurm_url, headers=headers, timeout=5, verify=self.security)
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
                print(f'Request Exception on {self.slurm_url}.')
                for node in nodes:
                    response[node] = "SLURM Down"
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
