#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This file is the entry point for provisioning
"""

__author__      = 'Antoine Schonewille'
__copyright__   = 'Copyright 2023, Luna2 Project'
__license__     = 'GPL'
__version__     = '2.0'
__maintainer__  = 'Dev-team'
__email__       = 'antoine.schonewille@clustervision.com'
__status__      = 'Development'

#VERSION: 0.2.4

import os
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

# ============================================================================

def main(argv):
    """
    The main method to initiate the script.
    """
    NODES = None
    GROUPS = None
    INTERFACES = None
    ACTION = None
    SUBSYSTEM = None
    for i in range(0, len(argv)):
        if (argv[i] == "-h" or argv[i] == "--help"):
            call_help()
            sys.exit()
        elif (argv[i] == "-g" or argv[i] == "--groups"):
            i+=1
            GROUPS=argv[i]
        elif (argv[i] and not NODES):
            NODES=argv[i]
        elif (argv[i] in ['status','on','off','reset','cycle']):
            ACTION=argv[i]
            SUBSYSTEM='power'
        elif (argv[i] in ['identify','noidentify']):
            ACTION=argv[i]
            SUBSYSTEM='chassis'
        else:
            print("Error: Invalid options used.")
            call_help()
            sys.exit()
    if (len(argv) == 0):
        call_help()
        sys.exit()
    if ((NODES is None) or (ACTION is None)):
        print("Error: Instruction incomplete. Nodes and Task expected.")
        call_help()
        sys.exit()
    handleRequest(nodes=NODES,groups=GROUPS,subsystem=SUBSYSTEM,action=ACTION)
    sys.exit()

# ============================================================================

def call_help():
    """
    This method will provide a Help Menu.
    """
    print("""
usage: lpower [-h] [--interface INTERFACE]
              [hosts] {status,on,off,reset,cycle,identify,noidentify}

BMC power management.

positional arguments:
  hosts                 Host list
  {status,on,off,reset,cycle,identify,noidentify}
                        Action

optional arguments:
  -h, --help            show this help message and exit
  --interface INTERFACE, -i INTERFACE
                        Interface to use instead of "BMC"
    """)


# ----------------------------------------------------------------------------
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

def getToken():
    """
    This method will retrieve the token.
    """
    if not all(key in CONF for key in field_check):
        read_ini()

    RET = {'401': 'invalid credentials', '400': 'bad request'}

    token_credentials = {'username': CONF['USERNAME'], 'password': CONF['PASSWORD']}
    try:
        x = session.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/token', json=token_credentials, stream=True, timeout=5, verify=CONF["VERIFY_CERTIFICATE"])
        if (str(x.status_code) in RET):
            print("Error: "+RET[str(x.status_code)])
            sys.exit(4)
        DATA = json.loads(x.text)
        if (not 'token' in DATA):
            print("Error: i did not receive a token. i cannot continue.")
            sys.exit(5)
        CONF["TOKEN"]=DATA["token"]
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
#    Below commented out as this catch all will also catch legit responses for e.g. 401 and 404
#    except:
#        print("Error: trouble getting my token for unknown reasons.")
#        exit(3)
    

# ----------------------------------------------------------------------------

def handleRequest(nodes=None ,groups=None, interface=None, subsystem=None, action=None):
    if ((not nodes is None) and (not action is None)):
        if ('TOKEN' not in CONF):
            getToken()
        if ('ENDPOINT' not in CONF):
            read_ini()

        RET = {'400': 'invalid request', '404': 'node list invalid', '401': 'action not authorized', '503': 'service not available'}

        headers = {'x-access-tokens': CONF['TOKEN']}

        regex = re.compile("^([a-zA-Z0-9_]+)$")
        result = regex.match(nodes)
        DATA = ''

        # single node query we do with GET
        if (result and nodes == result.group(1)):
            try:
                r = session.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/action/{subsystem}/{nodes}/_{action}', stream=True, headers=headers, timeout=5, verify=CONF["VERIFY_CERTIFICATE"])
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
                    print("["+status_code+'] ::: '+r.text)
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
                r = session.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/action/{subsystem}/_{action}', stream=True, headers=headers, json=body, timeout=5, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if (status_code in RET):
                    print(nodes+": failed: "+RET[status_code])
                elif (r.text):
                    DATA=json.loads(r.text)
                    request_id=handleResults(DATA=DATA,subsystem=subsystem,action=action)
                    # ------------- loop to keep polling for updates. -----------------------------------------------
                    while r.status_code == 200:
                        sleep(2)
                        r = session.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/status/{request_id}', stream=True, headers=headers, timeout=5, verify=CONF["VERIFY_CERTIFICATE"])
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
        for control in DATA.keys():
            if 'request_id' in DATA[control]:
                request_id=str(DATA[control]['request_id'])
                #print(f"Request_id: [{request_id}]")
                #next
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
