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



__author__      = 'Antoine Schonewille'
__copyright__   = 'Copyright 2025, Luna2 Project'
__license__     = 'GPL'
__version__     = '2.1'
__maintainer__  = 'Dev-team'
__email__       = 'antoine.schonewille@clustervision.com'
__status__      = 'Development'

#VERSION: 0.2.5

import os
import getpass
import sys
from builtins import dict
import re
import json
from time import sleep
from configparser import RawConfigParser
import requests
from requests import Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry

from utils.utils.log import Log
from utils.utils.ini import Ini
from utils.utils.token import Token

logger = Log.init_log(log_file='/var/log/luna/lpower.log',log_level='info')
CONF = Ini.read_ini(ini_file='/trinity/local/luna/utils/config/luna.ini')

urllib3.disable_warnings()
session = Session()
retries = Retry(
    total = 10,
    backoff_factor = 0.3,
    status_forcelist = [502, 503, 504],
    allowed_methods = {'GET', 'POST'}
)
session.mount('https://', HTTPAdapter(max_retries=retries))

# ============================================================================

def main(argv):
    """
    The main method to initiate the script.
    """
    command = sys.argv
    command[0] = 'lpower'
    command = ' '.join(command)
    logger.info(f'User {getpass.getuser()} ran => {command}')

    NODES = None
    GROUP = None
    RACK = None
    ACTION = None
    SUBSYSTEM = None
    if (len(argv) == 0):
        call_help()
        sys.exit()
    while len(argv)>0:
        item = argv.pop(0)
        if (item == "-h" or item == "--help"):
            call_help()
            exit()
        elif (item == "-g" or item == "--group"):
            GROUP=argv.pop(0)
        elif (item == "-r" or item == "--rack"):
            RACK=argv.pop(0)
        elif (item in ['status','on','off','reset','cycle']):
            ACTION=item
            SUBSYSTEM='power'
        elif (item in ['identify','noidentify']):
            ACTION=item
            SUBSYSTEM='chassis'
        elif item and not NODES:
            NODES=item
        else:
            print("Error: Invalid options used.")
            call_help()
            sys.exit()
    if (NODES is None and GROUP is None and RACK is None) or (ACTION is None):
        print("Error: Instruction incomplete. Nodes and Task expected.")
        call_help()
        sys.exit()
    handleRequest(nodes=NODES,group=GROUP,rack=RACK,subsystem=SUBSYSTEM,action=ACTION)
    sys.exit()

# ============================================================================

def call_help():
    """
    This method will provide a Help Menu.
    """
    print("""
usage: lpower [-h] [--rack|-r RACKNAME] [--group|-g GROUP]
              [hosts] {status,on,off,reset,cycle,identify,noidentify}

BMC power management.

positional arguments:
  hosts                     Host list. Any combination of: 
                               node[x-y],
                               nodex,nodey,...
                               nodex
  {status,on,off,reset,cycle,identify,noidentify}
                            Action

optional arguments:
  -h, --help                show this help message and exit
  -g GROUP, --group GROUP   perform the action on nodes of the group
  -r RACK, --rack RACK      perform the action on nodes inside the rack
    """)

# ----------------------------------------------------------------------------

def get_group_nodes(group=None):
    if group:
        CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])
        RET = {'400': 'invalid request', '404': 'group name invalid', '401': 'action not authorized', '503': 'service not available'}
        headers = {'x-access-tokens': CONF['TOKEN']}
        try:
            r = session.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/group/{group}/_member', stream=True, headers=headers, timeout=30, verify=CONF["VERIFY_CERTIFICATE"])
            status_code=str(r.status_code)
            if (status_code == "200"):
                if (r.text):
                    DATA=json.loads(r.text)
                    try:
                        nodelist=DATA['config']['group'][group]['members']
                        nodes=','.join(nodelist)
                        return nodes
                    except Exception as exp:
                        print(f'ERROR :: returned unrecognized format while fetching nodes in group')
                        sys.exit(3)
            elif (status_code in RET):
                print(f"ERROR :: {group} failed: {RET[status_code]}")
                sys.exit(3)
            else:
                # when we don't know how to handle the returned data
                print(f"ERROR :: [{status_code}]: {r.text}")
                sys.exit(3)
        except requests.exceptions.SSLError as ssl_loop_error:
            print(f'ERROR :: {ssl_loop_error}')
            sys.exit(3)
        except Exception as exp:
            print(f'ERROR :: {exp}')
            sys.exit(3)

def get_rack_nodes(rack=None):
    if rack:
        CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])
        RET = {'400': 'invalid request', '404': 'rack name invalid', '401': 'action not authorized', '503': 'service not available'}
        headers = {'x-access-tokens': CONF['TOKEN']}
        try:
            r = session.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/rack/{rack}', stream=True, headers=headers, timeout=30, verify=CONF["VERIFY_CERTIFICATE"])
            status_code=str(r.status_code)
            if (status_code == "200"):
                if (r.text):
                    DATA=json.loads(r.text)
                    try:
                        devicelist=DATA['config']['rack'][rack]['devices']
                        nodelist=[]
                        for device in devicelist:
                            if device['type'] == 'node':
                                nodelist.append(device['name'])
                        nodes=','.join(nodelist)
                        return nodes
                    except Exception as exp:
                        print(f'ERROR :: returned unrecognized format while fetching nodes in rack')
                        sys.exit(3)
            elif (status_code in RET):
                print(f"ERROR :: {rack} failed: {RET[status_code]}")
                sys.exit(3)
            else:
                # when we don't know how to handle the returned data
                print(f"ERROR :: [{status_code}]: {r.text}")
                sys.exit(3)
        except requests.exceptions.SSLError as ssl_loop_error:
            print(f'ERROR :: {ssl_loop_error}')
            sys.exit(3)
        except Exception as exp:
            print(f'ERROR :: {exp}')
            sys.exit(3)


# ----------------------------------------------------------------------------

def handleRequest(nodes=None, group=None, rack=None, subsystem=None, action=None):
    if group:
        group_nodes = get_group_nodes(group)
        if not nodes:
            nodes=group_nodes
        else:
            nodes+=','+group_nodes
    if rack:
        rack_nodes = get_rack_nodes(rack)
        if not nodes:
            nodes=rack_nodes
        else:
            nodes+=','+rack_nodes
    if ((not nodes is None) and (not action is None)):
        CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])

        RET = {'400': 'invalid request', '404': 'node list invalid', '401': 'action not authorized', '503': 'service not available'}
        headers = {'x-access-tokens': CONF['TOKEN']}

        regex = re.compile("^([a-zA-Z0-9_]+)$")
        result = regex.match(nodes)
        DATA = ''

        # single node query we do with GET
        if (result and nodes == result.group(1)):
            try:
                r = session.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/action/{subsystem}/{nodes}/_{action}', stream=True, headers=headers, timeout=30, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if (r.text):
                    DATA=json.loads(r.text)
                if (status_code == "204"):
                    print(nodes+": "+action)
                elif (status_code in RET):
                    print(nodes+": failed: "+RET[status_code])
                elif('control' in DATA):
                    print(nodes+": "+str(DATA['control']['power'] or 'no results returned'))
                else:
                    # when we don't know how to handle the returned data
                    print(f"ERROR :: [{status_code}]: {r.text}")
            except requests.exceptions.SSLError as ssl_loop_error:
                print(f'ERROR :: {ssl_loop_error}')
                sys.exit(3)
            except requests.exceptions.HTTPError as err:
                print("Error: trouble getting results: "+str(err))
                exit(3)
            except requests.exceptions.ConnectionError as err:
                print("Error: trouble getting results: "+str(err))
                exit(3)
            except requests.exceptions.Timeout as err:
                print("Error: trouble getting results: "+str(err))
                exit(3)
        # else, we have to work with a list. backend offloads this but we have to keep polling for updates
        else:
            try:
                body = {'control': { subsystem: { action: { 'hostlist': nodes } } } }
                r = session.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/action/{subsystem}/_{action}', stream=True, headers=headers, json=body, timeout=30, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if (status_code in RET):
                    print(nodes+": failed: "+RET[status_code])
                elif (r.text):
                    DATA=json.loads(r.text)
                    request_id=handleResults(DATA=DATA,subsystem=subsystem,action=action)
                    # ------------- loop to keep polling for updates. -----------------------------------------------
                    while r.status_code == 200:
                        sleep(2)
                        r = session.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/status/{request_id}', stream=True, headers=headers, timeout=30, verify=CONF["VERIFY_CERTIFICATE"])
                        status_code=str(r.status_code)
                        if (r.status_code!=200):
                            return
                        if (r.text):
                            #print(f"DEBUG: {r.text}")
                            DATA=json.loads(r.text)
                            handleResults(DATA=DATA,subsystem=subsystem,action=action)
                    # -----------------------------------------------------------------------------------------------
                else:
                    # when we don't know how to handle the returned data
                    print(status_code+' ::: '+r.text)
            except requests.exceptions.SSLError as ssl_loop_error:
                print(f'ERROR :: {ssl_loop_error}')
                sys.exit(3)
            except requests.exceptions.HTTPError as err:
                print("Error: trouble getting results: "+str(err))
                exit(3)
            except requests.exceptions.ConnectionError as err:
                print("Error: trouble getting results: "+str(err))
                exit(3)
            except requests.exceptions.Timeout as err:
                print("Error: trouble getting results: "+str(err))
                exit(3)
    else:
        print("Error: not enough parameters to run with")

# ----------------------------------------------------------------------------

def handleResults(DATA,request_id=None,subsystem=None,action=None):
    request_id=0
    if (type(DATA) is dict):
#        print(f"DEBUG: {DATA} {subsystem} {action}")
        if 'request_id' in DATA:
            request_id=str(DATA['request_id'])
        for control in DATA.keys():
            if 'request_id' in DATA[control]:
                request_id=str(DATA[control]['request_id'])
            if 'failed' in DATA[control]:
                for node in DATA[control]['failed'].keys():
                    print(f"{node}: {DATA[control]['failed'][node]}")
            if subsystem in DATA[control]:
                if 'request_id' in DATA[control][subsystem]:
                    request_id=str(DATA[control][subsystem]['request_id'])
                for cat in DATA[control][subsystem].keys():
                    if cat == 'ok':
                        for node in DATA[control][subsystem][cat]:
                            print(f"{node}: {subsystem} {action}")
                    elif cat != 'request_id':
                        for node in DATA[control][subsystem][cat]:
                            print(f"{node}: {cat}")
    return request_id

# ----------------------------------------------------------------------------

# hidden at the bottom; the call for the main function...
main(sys.argv[1:])
