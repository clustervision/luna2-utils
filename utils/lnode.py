#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This file is the entry point for provisioning
"""

__author__      = 'Diego Sonaglia'
__copyright__   = 'Copyright 2023, Luna2 Project'
__license__     = 'GPL'
__version__     = '2.0'
__maintainer__  = 'Dev-team'
__email__       = 'diego.sonaglia@clustervision.com'
__status__      = 'Development'


import re
import fire
import json 
import requests


# ----------------------------------------------------------------------------

def readConfigFile():
    read=0
    api = re.compile("^(\[API\])")
    regex = re.compile("^(.[^=]+)\s+?=\s+?(.*)$")
    CONF = {}
    try:
        with open("/trinity/local/luna/config/luna.ini") as f:
            for line in f:
                if (not read):
                    result = api.match(line)
                    if (result):
                        read=1
                else:
                    result = regex.match(line)
                    if (result):
                        CONF[result.group(1)]=result.group(2)
    except:
        print("Error: /trinity/local/luna/config/luna.ini Does not exist and i cannot continue.")
        exit(1)

    if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF)):
        print("Error: username/password/endpoint not found in config file. i cannot continue.")
        exit(2)

    return CONF
# ----------------------------------------------------------------------------

def getToken(CONF):
    if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF)):
        readConfigFile()

    RET={'401': 'invalid credentials', '400': 'bad request'}

    token_credentials = {'username': CONF['USERNAME'],'password': CONF['PASSWORD']}
    try:
        x = requests.post('http://'+CONF["ENDPOINT"]+'/token', json = token_credentials)
        if (str(x.status_code) in RET):
            print("Error: "+RET[str(x.status_code)])
            exit(4)
        DATA=json.loads(x.text)
        if (not 'token' in DATA):
            print("Error: i did not receive a token. i cannot continue.")
            exit(5)
        return DATA['token']
    except requests.exceptions.HTTPError as err:
        print("Error: trouble getting my token: "+str(err))
        exit(3)
    except requests.exceptions.ConnectionError as err:
        print("Error: trouble getting my token: "+str(err))
        exit(3)
    except requests.exceptions.Timeout as err:
        print("Error: trouble getting my token: "+str(err))
        exit(3)

def print_info(msg):
    """Print an info message"""
    print(f'\033[1mINFO\033[0m: {msg}')

def print_error(msg):
    """Print an error message"""
    print(f'\033[91mERROR\033[0m: {msg}')


class CLI():
    '''
    Send SEL commands to luna cluster nodes
    '''

    def __init__(self) -> None:
        self._configs = readConfigFile()
        self._token = getToken(self._configs)

    def list(self, node):
        '''
        list all the SEL entries for one node

        :param node: the node to run the command on
        '''
        endpoint = f'http://{self._configs["ENDPOINT"]}/control/action/sel/{node}/_list'
        resp = self._send_request(endpoint, 'GET', None)
        print(resp.text)

    def clear(self, nodes):
        '''
        clear all the SEL entries for one or more nodes

        :param nodes: the node(s) to run the command on
        '''
        is_single_node = re.compile("^([a-zA-Z0-9_]+)$").match(nodes)
        if (is_single_node):
            endpoint = f'http://{self._configs["ENDPOINT"]}/control/action/sel/{nodes}/_clear'
            method = 'GET'
            data = None
        else:
            endpoint = f'http://{self._configs["ENDPOINT"]}/control/action/sel/_clear'
            method = 'POST'
            data = {'control': { 'sel': { 'clear': { 'hostlist': nodes } } } }
        resp = self._send_request(endpoint, method, data)
        print(resp.text)

    def _send_request(self, endpoint, method, data):
        headers = {'x-access-tokens': f'{self._token}'}
        valid_codes = [200, 201, 202, 204]

        if method == 'GET':
            resp = requests.get(endpoint, headers=headers)
        elif method == 'POST':
            resp = requests.post(endpoint, headers=headers, json=data)
        else:
            raise ValueError(f'Invalid method: {method}')
        
        
        if resp.status_code not in valid_codes:
            print_error(f'Failed to run command: {resp.text}')
            exit(1)

        return resp

def main():
    fire.Fire(CLI)