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
This file is the entry point for provisioning
"""

__author__      = 'Diego Sonaglia'
__copyright__   = 'Copyright 2023, Luna2 Project [UTILITY]'
__license__     = 'GPL'
__version__     = '2.0'
__maintainer__  = 'Dev-team'
__email__       = 'diego.sonaglia@clustervision.com'
__status__      = 'Development'


import re
import sys
import json
import argparse
from configparser import ConfigParser

import jwt
import requests


TOKEN = None
LUNA_CONFIG_PATH = '/trinity/local/luna/utils/config/luna.ini'
requests.packages.urllib3.disable_warnings()

# PENDING DIEGO 15 AUG 2023 -> need to test if sel commands on the backend work
def print_info(msg):
    """Print an info message"""
    print(f'\033[1mINFO\033[0m: {msg}')

def print_error(msg):
    """Print an error message"""
    print(f'\033[91mERROR\033[0m: {msg}')

def print_fatal(msg):
    """Print a fatal error message"""
    print(f'\033[91mFATAL\033[0m: {msg}')


def get_token(settings):
    """

    This method will fetch a valid token for further use.

    """
    # If there is a token check that is valid and return it
    if TOKEN is not None:
        try:
            # Try to decode the token to check if it is still valid
            jwt.decode(TOKEN, settings['API']['SECRET_KEY'], algorithms=['HS256'])
            return TOKEN
        except jwt.exceptions.ExpiredSignatureError:
            # If the token is expired is ok, we fetch a new one
            pass

    # Otherwise just fetch a new one
    data = {'username': settings['API']['USERNAME'], 'password': settings['API']['PASSWORD']}
    daemon_url = f"{settings['API']['PROTOCOL']}://{settings['API']['ENDPOINT']}/token"
    response = requests.post(
        daemon_url,
        json=data,
        stream=True,
        timeout=3,
        verify=(settings['API']['VERIFY_CERTIFICATE'].lower() == 'true'))
    token = response.json()['token']
    return token


class CLI():
    '''
    Send SEL commands to luna cluster nodes
    '''

    def __init__(self) -> None:
        self._configs = ConfigParser()
        self._configs.read(LUNA_CONFIG_PATH)
        self._token = get_token(self._configs)

    def list(self, node):
        '''
        list all the SEL entries for one node

        :param node: the node to run the command on
        '''
        is_single_node = re.compile("^([a-zA-Z0-9_]+)$").match(node)
        if (is_single_node):
            endpoint = f'{self._configs["API"]["PROTOCOL"]}://{self._configs["API"]["ENDPOINT"]}/control/action/sel/{node}/_list'
            resp = self._send_request(endpoint, 'GET', None)
        else:
            print_error('The list command can only be run on a single node, hostlist not supported')
            sys.exit(1)
        try:
            data = json.loads(resp.text)
            message = data['control']['sel']
            message = message.replace(';;','\n')
            print(message)
        except:
            print(resp.text)


    def clear(self, nodes):
        '''
        clear all the SEL entries for one or more nodes

        :param nodes: the node(s) to run the command on
        '''
        is_single_node = re.compile("^([a-zA-Z0-9_]+)$").match(nodes)
        if (is_single_node):
            endpoint = f'{self._configs["API"]["PROTOCOL"]}://{self._configs["API"]["ENDPOINT"]}/control/action/sel/{nodes}/_clear'
            method = 'GET'
            data = None
        else:
            endpoint = f'{self._configs["API"]["PROTOCOL"]}://{self._configs["API"]["ENDPOINT"]}/control/action/sel/_clear'
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
            resp = requests.get(endpoint,
                                headers=headers,
                                stream=True,
                                timeout=30,
                                verify=(self._configs['API']['VERIFY_CERTIFICATE'].lower() == 'true'))
        elif method == 'POST':
            resp = requests.post(endpoint,
                                headers=headers,
                                stream=True,
                                timeout=30,
                                verify=(self._configs['API']['VERIFY_CERTIFICATE'].lower() == 'true'), json=data)
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
    usage = '%(prog)s {list,clear} <host|hostlist>'
    parser = argparse.ArgumentParser(description='Luna SEL commands', usage=usage)
    
    subparsers = parser.add_subparsers(help='sub-command help', dest='command')

    parser_list = subparsers.add_parser('list', help='list all the SEL entries for one node')
    parser_list.add_argument('node', help='the node to run the command on')

    parser_clear = subparsers.add_parser('clear', help='clear all the SEL entries for one or more nodes')
    parser_clear.add_argument('nodes', help='the node(s) to run the command on')

    args = parser.parse_args()
    cli = CLI()

    if args.command == 'list':
        cli.list(args.node)
    elif args.command == 'clear':
        cli.clear(args.nodes)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
