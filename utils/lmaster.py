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

import sys
from builtins import dict
import requests
import re
import json
from time import sleep

CONF={}
requests.packages.urllib3.disable_warnings()

# ============================================================================

def main(argv):
    for i in range(0, len(argv)):
        if (argv[i] == "-h" or argv[i] == "--help"):
            callHelp()
            exit()
        elif (argv[i] == "-s" or argv[i] == "--set"):
            ACTION="set"
        elif (argv[i] == "-w" or argv[i] == "--who"):
            ACTION="who"
        else:
            ACTION="status"
    if (len(argv) == 0):
        ACTION="status"
    handleRequest(action=ACTION)
    exit()

# ============================================================================

def callHelp():
    print ("""
usage: lmaster [-h|-s|-w]

Gets Luna2 master state of controller, based on cli luna.ini config

optional arguments:
  -h, --help            show this help message and exit
  -s, --set             sets master state
  -w, --who             tells who of the controllers is master
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
        CONF["VERIFY_CERTIFICATE"] = True if CONF["VERIFY_CERTIFICATE"].lower() in ['y', 'yes', 'true']  else False
    except:
        print("Error: /trinity/local/luna/cli/config/luna.ini Does not exist and i cannot continue.")
        exit(1)

    if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF)):
        print("Error: username/password/endpoint not found in config file. i cannot continue.")
        exit(2)

# ----------------------------------------------------------------------------

def getToken():
    if (('USERNAME' not in CONF) or ('PASSWORD' not in CONF) or ('ENDPOINT' not in CONF)):
        readConfigFile()

    RET={'401': 'invalid credentials', '400': 'bad request'}

    token_credentials = {'username': CONF['USERNAME'],'password': CONF['PASSWORD']}
    try:
        x = requests.post(CONF["PROTOCOL"]+'://'+CONF["ENDPOINT"]+'/token', json = token_credentials, verify=CONF["VERIFY_CERTIFICATE"])
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

def handleRequest(nodes=None,groups=None,interface=None,action=None):
    if (True):
        if ('TOKEN' not in CONF):
            getToken()
        if ('ENDPOINT' not in CONF):
            readConfigFile()

        RET={'400': 'invalid request', '404': 'node list invalid', '401': 'action not authorized', '503': 'service not available'}

        headers = {'x-access-tokens': CONF['TOKEN']}

        url_string = None
        if (action == "set"):
            url_string = f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/ha/master/_set'
        elif (action == "who"):
            url_string = f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/ha/master/_who'
        elif (action == "status"):
            url_string = f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/ha/master'
        if url_string:
            try:
                r = requests.get(url_string,headers=headers,verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
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
    else:
        print("Error: not enough parameters to run with")

# ----------------------------------------------------------------------------

# hidden at the bottom; the call for the main function...
main(sys.argv[1:])

