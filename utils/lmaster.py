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

from imports.Log import Log
from imports.Ini import Ini
from imports.Token import Token

logger = Log.init_log(log_file='/var/log/luna/lmaster.log',log_level='info')
CONF = Ini.read_ini(ini_file='/trinity/local/luna/utils/config/luna.ini')

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


def handleRequest(nodes=None,groups=None,interface=None,action=None):
    if (True):
        CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])

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

