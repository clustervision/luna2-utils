#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

"""
This file is the entry point for provisioning
"""

__author__      = 'Diego Sonaglia'
__copyright__   = 'Copyright 2023, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '2.0'
__maintainer__  = 'Dev-team'
__email__       = 'diego.sonaglia@clustervision.com'
__status__      = 'Development'


import os
import sys
import re
import json 
import fire
from configparser import RawConfigParser
import requests
from requests import Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry


urllib3.disable_warnings()
session = Session()
retries = Retry(
    total = 60,
    backoff_factor = 0.1,
    status_forcelist = [502, 503, 504],
    allowed_methods = {'GET', 'POST'}
)
session.mount('https://', HTTPAdapter(max_retries=retries))
field_check = ['USERNAME', 'PASSWORD', 'ENDPOINT', 'PROTOCOL', 'VERIFY_CERTIFICATE']
INI_FILE = '/trinity/local/luna/utils/config/luna.ini'
CONF = {}

# PENDING DIEGO 15 AUG 2023 -> need to test if sel commands on the backend work

def get_option(parser=None, errors=None,  section=None, option=None):
    """
    This method will retrieve the value from the INI
    """
    response = False
    if parser.has_option(section, option):
        response = parser.get(section, option)
    else:
        errors.append(f'{option} is not found in {section} section in {INI_FILE}.')
    return response, errors


def read_ini():

    errors = []
    username, password, daemon, secret_key, protocol, security = None, None, None, None, None, ''
    file_check = os.path.isfile(INI_FILE)
    read_check = os.access(INI_FILE, os.R_OK)
    if file_check and read_check:
        configparser = RawConfigParser()
        configparser.read(INI_FILE)
        if configparser.has_section('API'):
            CONF['USERNAME'], errors = get_option(configparser, errors,  'API', 'USERNAME')
            CONF['PASSWORD'], errors = get_option(configparser, errors,  'API', 'PASSWORD')
            secret_key, errors = get_option(configparser, errors,  'API', 'SECRET_KEY')
            CONF['PROTOCOL'], errors = get_option(configparser, errors,  'API', 'PROTOCOL')
            CONF['ENDPOINT'], errors = get_option(configparser, errors,  'API', 'ENDPOINT')
            security, errors = get_option(configparser, errors,  'API', 'VERIFY_CERTIFICATE')
            CONF["VERIFY_CERTIFICATE"] = True if security.lower() in ['y', 'yes', 'true']  else False
        else:
            errors.append(f'API section is not found in {INI_FILE}.')
    else:
        errors.append(f'{INI_FILE} is not found on this machine.')
    if errors:
        sys.stderr.write('You need to fix following errors...\n')
        num = 1
        for error in errors:
            sys.stderr.write(f'{num}. {error}\n')
        sys.exit(1)
    return CONF
# ----------------------------------------------------------------------------


def get_token(CONF):
    """
    This method will retrieve the token.
    """
    field_check = ['USERNAME', 'PASSWORD', 'ENDPOINT', 'PROTOCOL', 'USERNAME', 'VERIFY_CERTIFICATE']
    if not all(key in CONF for key in field_check):
    # if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF)):
        read_ini()
    response = {'401': 'invalid credentials', '400': 'bad request'}
    token_credentials = {'username': CONF['USERNAME'], 'password': CONF['PASSWORD']}

    try:
        x = session.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/token', json=token_credentials, stream=True, timeout=5, verify=CONF["VERIFY_CERTIFICATE"])
        if (str(x.status_code) in response):
            print("Error: "+response[str(x.status_code)])
            sys.exit(4)
        DATA=json.loads(x.text)
        if (not 'token' in DATA):
            print("Error: i did not receive a token. i cannot continue.")
            sys.exit(5)
        return DATA['token']
    except requests.exceptions.SSLError as ssl_loop_error:
        print(f'ERROR :: {ssl_loop_error}')
        sys.exit(3)
    except requests.exceptions.HTTPError as err:
        print("Error: trouble getting my token: "+str(err))
        sys.exit(3)
    except requests.exceptions.ConnectionError as err:
        print("Error: trouble getting my token: "+str(err))
        sys.exit(3)
    except requests.exceptions.Timeout as err:
        print("Error: trouble getting my token: "+str(err))
        sys.exit(3)


def print_info(msg):
    """
    Print an info message
    """
    print(f'\033[1mINFO\033[0m: {msg}')


def print_error(msg):
    """
    Print an error message
    """
    print(f'\033[91mERROR\033[0m: {msg}')


class CLI():
    '''
    Send SEL commands to luna cluster nodes
    '''

    def __init__(self) -> None:
        self._configs = read_ini()
        self._token = get_token(self._configs)


    def list(self, node):
        '''
        list all the SEL entries for one node

        :param node: the node to run the command on
        '''
        endpoint = f'{self._configs["PROTOCOL"]}://{self._configs["ENDPOINT"]}/control/action/sel/{node}/_list'
        resp = self._send_request(endpoint, 'GET', None)
        print(resp.text.replace(";;","\n"))


    def clear(self, nodes):
        '''
        clear all the SEL entries for one or more nodes

        :param nodes: the node(s) to run the command on
        '''
        is_single_node = re.compile("^([a-zA-Z0-9_]+)$").match(nodes)
        if (is_single_node):
            endpoint = f'{self._configs["PROTOCOL"]}://{self._configs["ENDPOINT"]}/control/action/sel/{nodes}/_clear'
            method = 'GET'
            data = None
        else:
            endpoint = f'{self._configs["PROTOCOL"]}://{self._configs["ENDPOINT"]}/control/action/sel/_clear'
            method = 'POST'
            data = {'control': { 'sel': { 'clear': { 'hostlist': nodes } } } }
        resp = self._send_request(endpoint, method, data)
        print(resp.text)


    def _send_request(self, endpoint, method, data):
        """
        This method will send the request.
        """
        headers = {'x-access-tokens': f'{self._token}'}
        valid_codes = [200, 201, 202, 204]

        if method == 'GET':
            resp = session.get(endpoint, headers=headers, stream=True, timeout=5, verify=self._configs["VERIFY_CERTIFICATE"])
        elif method == 'POST':
            resp = session.post(endpoint, headers=headers, json=data, stream=True, timeout=5, verify=self._configs["VERIFY_CERTIFICATE"])
        else:
            raise ValueError(f'Invalid method: {method}')
        if resp.status_code not in valid_codes:
            print_error(f'Failed to run command: {resp.text}')
            sys.exit(1)

        return resp


def main():
    """
    The Main method to initiate the script.
    """
    fire.Fire(CLI)


if __name__ == "__main__":
    main()
