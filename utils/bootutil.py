#!/trinity/local/python/bin/python3

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


import sys
import os
import base64
import json
import requests
import urllib3

urllib3.disable_warnings()

HTTP_USER = ""
HTTP_PASSWORD = ""
HOST = ""


def redfish_get(url):
    """
    Get JSON data at the specified Redfish URL and return this data in the form
    of a Python dictionary.
    """
    global HTTP_USER, HTTP_PASSWORD
    auth = "Basic " + base64.b64encode((HTTP_USER + ":" + HTTP_PASSWORD).encode()).decode()
    headers = {"Authorization": auth}
    response = requests.get(url, headers=headers, verify=False, timeout=5)
    redfish_data = json.loads(response.text)
    return redfish_data


def redfish_patch(url, etag, json_data):
    """
    Update JSON data at the specified Redfish URL and return the response code and body.
    """
    global HTTP_USER, HTTP_PASSWORD
    auth = "Basic " + base64.b64encode((HTTP_USER + ":" + HTTP_PASSWORD).encode()).decode()
    headers = {"Authorization": auth, "Content-Type": "application/json", "If-Match": etag}
    response = requests.patch(url, headers=headers, data=json_data, verify=False, timeout=5)
    return [response.status_code, response.text]


def get_bootoption_urls(host, system):
    """
    Get a list of Boot Options URLs for the specified system.
    """
    bootoptionsurl = f"{host}/redfish/v1/Systems/{system}/BootOptions"
    bootoptions = redfish_get(bootoptionsurl)
    members = bootoptions["Members"]
    bootoption_urls = [m["@odata.id"] for m in members]
    return bootoption_urls


def get_system_urls(host):
    """
    Get a list of system URLs for the specified host.
    """
    systems_url = f"{host}/redfish/v1/Systems"
    systems = redfish_get(systems_url)
    members = systems["Members"]
    system_urls = [m["@odata.id"] for m in members]
    return system_urls


def get_boot_option_id_name_desc(url):
    """
    Get the ID, Name and Description of the Boot Option at the specified URL.
    """
    global HOST
    bootoption = redfish_get(f"{HOST}{url}")
    boot_id = bootoption["Id"]
    name = bootoption["Name"]
    desc = bootoption["Description"]
    return [boot_id, name, desc]


def get_bootorder(system):
    """
    Get the boot order of the specified system.
    """
    system_data = redfish_get(f"{HOST}/redfish/v1/Systems/{system}")
    boot_data = system_data["Boot"]
    bootorder = boot_data["BootOrder"]
    return bootorder


def get_system_etag(system):
    """
    Get the etag of the specified system.
    """
    system_data = redfish_get(f"{HOST}/redfish/v1/Systems/{system}")
    etag = system_data["@odata.etag"]
    return etag


def set_bootorder(system, etag, bootorder):
    """
    Set the boot order of the specified system.
    """
    url = f"{HOST}/redfish/v1/Systems/{system}"
    json_data = {
        "BootOrder" : bootorder
    }
    json_data = {
        "Boot" : json_data
    }
    json_string = json.dumps(json_data)
    result = redfish_patch(url, etag, json_string)
    return result


MODE = ''
DISPLAY_HELP = False
DESIRED_BOOTORDER = ''

if len(sys.argv) > 1:
    STATE = 'option'
    for i in range(1, len(sys.argv)):
        arg = sys.argv[i]
        if STATE == 'option':
            if arg in ['-U', '--user']:
                STATE = 'user'
            elif arg in ['-P', '--password']:
                STATE = 'password'
            elif arg in ['-H', '--host']:
                STATE = 'host'
            elif arg == 'list':
                MODE = 'list'
            elif arg == 'get':
                MODE = 'get'
            elif arg == 'set':
                MODE = 'set'
                STATE = 'set'
        elif STATE == 'user':
            HTTP_USER = arg
            STATE = 'option'
        elif STATE == 'password':
            HTTP_PASSWORD = arg
            STATE = 'option'
        elif STATE == 'host':
            HOST = arg
            STATE = 'option'
        elif STATE == 'set':
            DESIRED_BOOTORDER = arg.split()
            STATE = 'option'
else:
    DISPLAY_HELP = True

if not sys.argv[1:] or MODE == "":
    print("Please specify a mode on the command line.", file=sys.stderr)
    DISPLAY_HELP = True
if not HOST:
    print("Please specify a host on the command line.", file=sys.stderr)
    DISPLAY_HELP = True
if not HTTP_USER:
    print("Please specify an HTTP user on the command line.", file=sys.stderr)
    DISPLAY_HELP = True
if not HTTP_PASSWORD:
    print("Please specify an HTTP password on the command line.", file=sys.stderr)
    DISPLAY_HELP = True
if DISPLAY_HELP:
    print("\nUsage: bootutil [options...] <mode>\n", file=sys.stderr)
    print("<mode> can be either:", file=sys.stderr)
    print("  list         -- list available boot options", file=sys.stderr)
    print("  get          -- get current boot order", file=sys.stderr)
    print("  set <order>  -- set current boot order\n", file=sys.stderr)
    print("Available [options...]:", file=sys.stderr)
    print(" -H, --host      -- Redfish host. Must include protocol, e.g. https://host",
          file=sys.stderr)
    print(" -U, --user      -- HTTP user name", file=sys.stderr)
    print(" -P, --password  -- HTTP user password", file=sys.stderr)
    sys.exit(1)

#Perform the requested task
#Parse system and boot option URLs based on host

sy_urls = get_system_urls(HOST)
if len(sy_urls) == 1:
    system = os.path.basename(sy_urls[0])
bo_urls = get_bootoption_urls(HOST, system)

if MODE == "list":
    print("Available boot devices:")
    print("")
    print("ID    |Name            |Desc")
    print("------+----------------+------------------------------------------------------")

bootoption = {}
for bo_url in bo_urls:
    id, name, desc = get_boot_option_id_name_desc(bo_url)
    bootoption[name] = desc
    if MODE == "list":
        print(f"{i:<6}|{name:<16}|{desc:<58}")

#Retrieve and output the current boot order

if MODE == "get":
    print("Current boot order:")
    bootorder = get_bootorder(system)
    i = 1
    for bo in bootorder:
        print(f"{i} - {bo} {bootoption[bo]}")
        i += 1

#Set the boot order

if MODE == "set":
    etag = get_system_etag(system)
    encode, code = set_bootorder(system, etag, DESIRED_BOOTORDER)
    if encode>=200 and encode<300:
        print(f'Set boot order to "{DESIRED_BOOTORDER}" successful! (HTTP-result: {code})')
    else:
        print(f'Set boot order to "{DESIRED_BOOTORDER}" failed! (HTTP-result: {code})')
