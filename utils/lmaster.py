#!/trinity/local/python/bin/python3
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

from utils.utils.log import Log
from utils.utils.ini import Ini
from utils.utils.token import Token

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
        elif (argv[i] == "-a" or argv[i] == "--all"):
            ACTION="all"
        else:
            ACTION="who"
    if (len(argv) == 0):
        ACTION="who"
    handleRequest(action=ACTION)
    exit()

# ============================================================================

def callHelp():
    print ("""
usage: lmaster [-h|-s|-w|-a]

Gets Luna2 master state of controller, based on utils luna.ini config

optional arguments:
  -h, --help            show this help message and exit
  -s, --set             sets master state for controller configured as endpoint in luna.ini
                        in most cases it's the controller where this command is invoked
  -w, --who             tells who of the controllers is master
  -a, --all             returns current HA values of all controllers
    """)


# ----------------------------------------------------------------------------

def handleRequest(action=None):
    CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])

    RET={'400': 'invalid request', '404': 'invalid url or API endpoint', '401': 'action not authorized', '503': 'service not available'}
    headers = {'x-access-tokens': CONF['TOKEN']}

    url_string = None
    if (action == "set"):
        url_string = f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/ha/master/_set'
    elif (action == "who" or action == "all"):
        url_string = f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/ha/controllers'
    elif (action == "master"):
        url_string = f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/ha/master'
    if url_string:
        try:
            r = requests.get(url_string,headers=headers,verify=CONF["VERIFY_CERTIFICATE"])
            status_code=str(r.status_code)
            message=None
            if status_code in RET:
                if (action == "who" and retry()):
                    handleRequest(action="master")
                    return
                else:
                    print(f'[{status_code}] ::: {RET[status_code]}')
                    exit(1)
            if len(r.text) == 0:
                print(f'[{status_code}] ::: Nothing received from controller')
                exit(1)
            if isinstance(r.text, str):
                DATA=json.loads(r.text)
                if 'message' in DATA:
                    message=DATA['message']
            host,*_ = CONF["ENDPOINT"].split(':')
            print(f"Configured endpoint is {host}")
            if (action == "set"):
                print (message or r.text)
            elif (action == "master"):
                if message:
                    print(f'{host} is the master')
                else:
                    print(f'{host} is not the master')
            elif (action == "who"):
                if isinstance(message, dict):
                    if len(message.keys()) < 2:
                        print("Cluster not configured for HA")
                        exit(0)
                    MASTERFOUND=False
                    for controller in message.keys():
                        if 'comment' in message[controller]:
                            print(f"{controller}: {message[controller]['comment']}")
                        elif 'ha' in message[controller] and 'master' in message[controller]['ha']:
                            if message[controller]['ha']['master']:
                                print(f"{controller} is the master")
                                MASTERFOUND=True
                    if not MASTERFOUND:
                        print(f"No master configured or using a shadow controller as endpoint")
            elif (action == "all"):
                if isinstance(message, dict):
                    if len(message.keys()) < 2:
                        print("Cluster not configured for HA")
                        exit(0)
                    maxlength=0
                    for controller in message.keys():
                        if len(controller) > maxlength:
                            maxlength=len(controller)
                    for controller in message.keys():
                        print(f"{controller}:".ljust(maxlength+3), end=" ")
                        if 'comment' in message[controller]:
                            print(f"{controller}: {message[controller]['comment']}".ljust(30), end=" ")
                        if 'ha' in message[controller]:
                            for item in ['enabled','master','insync','syncimages','overrule']:
                                if item in message[controller]['ha']:
                                    print(f"{item}: {message[controller]['ha'][item]}".ljust(len(item)+8), end=" ")
                        if 'config' in message[controller]:
                            for item in ['shadow','beacon']:
                                if item in message[controller]['config']:
                                    print(f"{item}: {message[controller]['config'][item]}".ljust(len(item)+8), end=" ")
                        print("")
                else:
                    print (message or r.text)
        except requests.exceptions.HTTPError as err:
            print("Error: trouble getting results: "+str(err))
            exit(3)
        except requests.exceptions.ConnectionError as err:
            print("Error: trouble getting results: "+str(err))
            exit(3)
        except requests.exceptions.Timeout as err:
            print("Error: trouble getting results: "+str(err))
            exit(3)
        except Exception as exp:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            print(f"{exp}, {exc_type}, in {exc_tb.tb_lineno}")

RETRY=True
def retry():
    global RETRY
    if RETRY:
        RETRY=False
        return True
    return False

# ----------------------------------------------------------------------------

# hidden at the bottom; the call for the main function...
main(sys.argv[1:])

