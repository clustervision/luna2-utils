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

import sys
from builtins import dict
import requests
import re
import json
from time import sleep

CONF={}

# ============================================================================

def main(argv):
    NODES=None
    GROUPS=None
    INTERFACES=None
    ACTION=None
    SUBSYSTEM=None
    for i in range(0, len(argv)):
        if (argv[i] == "-h" or argv[i] == "--help"):
            callHelp()
            exit()
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
            callHelp()
            exit()
    if (len(argv) == 0):
        callHelp()
        exit()
    if ((NODES is None) or (ACTION is None)):
        print("Error: Instruction incomplete. Nodes and Task expected.")
        callHelp()
        exit()
    handleRequest(nodes=NODES,groups=GROUPS,subsystem=SUBSYSTEM,action=ACTION)
    exit()

# ============================================================================

def callHelp():
    print ("""
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

def readConfigFile():
    read=0
    api = re.compile("^(\[API\])")
    regex = re.compile("^(.[^=]+)\s+?=\s+?(.*)$")
    try:
        with open("/trinity/local/luna/cli/config/luna.ini") as f:
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
        print("Error: /trinity/local/luna/cli/config/luna.ini Does not exist and i cannot continue.")
        exit(1)

    if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF) or ('PROTOCOL' not in CONF)):
        print("Error: username/password/endpoint not found in config file. i cannot continue.")
        exit(2)

# ----------------------------------------------------------------------------

def getToken():
    if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF) or ('PROTOCOL' not in CONF)):
        readConfigFile()

    RET={'401': 'invalid credentials', '400': 'bad request'}

    token_credentials = {'username': CONF['USERNAME'],'password': CONF['PASSWORD']}
    try:
        x = requests.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/token', json = token_credentials)
        if (str(x.status_code) in RET):
            print("Error: "+RET[str(x.status_code)])
            exit(4)
        DATA=json.loads(x.text)
        if (not 'token' in DATA):
            print("Error: i did not receive a token. i cannot continue.")
            exit(5)
        CONF["TOKEN"]=DATA["token"]
    except requests.exceptions.HTTPError as err:
        print("Error: trouble getting my token: "+str(err))
        exit(3)
    except requests.exceptions.ConnectionError as err:
        print("Error: trouble getting my token: "+str(err))
        exit(3)
    except requests.exceptions.Timeout as err:
        print("Error: trouble getting my token: "+str(err))
        exit(3)
#    Below commented out as this catch all will also catch legit responses for e.g. 401 and 404
#    except:
#        print("Error: trouble getting my token for unknown reasons.")
#        exit(3)
    

# ----------------------------------------------------------------------------

def handleRequest(nodes=None,groups=None,interface=None,subsystem=None,action=None):
    if ((not nodes is None) and (not action is None)):
        if ('TOKEN' not in CONF):
            getToken()
        if ('ENDPOINT' not in CONF):
            readConfigFile()

        RET={'400': 'invalid request', '404': 'node list invalid', '401': 'action not authorized', '503': 'service not available'}

        headers = {'x-access-tokens': CONF['TOKEN']}

        regex = re.compile("^([a-zA-Z0-9_]+)$")
        result = regex.match(nodes)
        DATA = ''

        # single node query we do with GET
        if (result and nodes == result.group(1)):
            try:
                r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/action/{subsystem}/{nodes}/_{action}',headers=headers)
                status_code=str(r.status_code)
                if (r.text):
                    DATA=json.loads(r.text)
                if (status_code == "204"):
                    print(nodes+": "+action)
                elif (status_code in RET):
                    print(nodes+": failed: "+RET[status_code])
                elif('control' in DATA):
                    print(nodes+": "+str(DATA['control']['status'] or 'no results returned'))
                else:
                    # when we don't know how to handle the returned data
                    print("["+status_code+'] ::: '+r.text)
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
                r = requests.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/action/{subsystem}/_{action}', json=body, headers=headers)
                status_code=str(r.status_code)
                if (status_code in RET):
                    print(nodes+": failed: "+RET[status_code])
                elif (r.text):
                    DATA=json.loads(r.text)
                    request_id=handleResults(DATA=DATA,subsystem=subsystem,action=action)
                    # ------------- loop to keep polling for updates. -----------------------------------------------
                    while r.status_code == 200:
                        sleep(2)
                        r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/control/status/{request_id}',headers=headers)
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
                for cat in DATA[control][subsystem].keys():
                    if cat == 'ok':
                        for node in DATA[control][subsystem][cat]:
                            print(f"{node}: {subsystem} {action}")
                    else:
                        for node in DATA[control][subsystem][cat]:
                            print(f"{node}: {cat}")
    return request_id

# ----------------------------------------------------------------------------

# hidden at the bottom; the call for the main function...
main(sys.argv[1:])


