#!/trinity/local/python/bin/python3
import sys
import os
import base64
import json
import requests

httpuser = ""
httppassword = ""
host = ""

def redfish_get(url):
    # Get JSON data at the specified Redfish URL and return this data in the form
    # of a Python dictionary
    global httpuser, httppassword

    auth = "Basic " + base64.b64encode((httpuser + ":" + httppassword).encode()).decode()
    headers = {"Authorization": auth}
    response = requests.get(url, headers=headers, verify=False)
    redfish_data = json.loads(response.text)
    return redfish_data

def redfish_patch(url, etag, json_data):
    # Update JSON data at the specified Redfish URL and return the response code and body
    global httpuser, httppassword

    auth = "Basic " + base64.b64encode((httpuser + ":" + httppassword).encode()).decode()
    headers = {"Authorization": auth, "Content-Type": "application/json", "If-Match": etag}
    response = requests.patch(url, headers=headers, data=json_data, verify=False)
    return [response.status_code, response.text]

def get_bootoption_urls(host, system):
    # Get a list of Boot Options URLs for the specified system
    bootoptionsurl = f"{host}/redfish/v1/Systems/{system}/BootOptions"
    bootoptions = redfish_get(bootoptionsurl)
    members = bootoptions["Members"]
    bootoption_urls = [m["@odata.id"] for m in members]
    return bootoption_urls

def get_system_urls(host):
    # Get a list of system URLs for the specified host
    systemsurl = f"{host}/redfish/v1/Systems"
    systems = redfish_get(systemsurl)
    members = systems["Members"]
    system_urls = [m["@odata.id"] for m in members]
    return system_urls

def get_boot_option_id_name_desc(url):
    # Get the ID, Name and Description of the Boot Option at the specified URL
    global host

    bootoption = redfish_get(f"{host}{url}")
    id = bootoption["Id"]
    name = bootoption["Name"]
    desc = bootoption["Description"]
    return [id, name, desc]

def get_bootorder(system):
    # Get the boot order of the specified system
    systemdata = redfish_get(f"{host}/redfish/v1/Systems/{system}")
    bootdata = systemdata["Boot"]
    bootorder = bootdata["BootOrder"]
    return bootorder

def get_system_etag(system):
    # Get the etag of the specified system
    systemdata = redfish_get(f"{host}/redfish/v1/Systems/{system}")
    etag = systemdata["@odata.etag"]
    return etag

def set_bootorder(system, etag, bootorder):
    # Set the boot order of the specified system
    url = f"{host}/redfish/v1/Systems/{system}"
    json_data = {
        "BootOrder" : bootorder
    }
    json_data = {
        "Boot" : json_data
    }
    jsonstr = json.dumps(json_data)
    result = redfish_patch(url, etag, jsonstr)
    return result


mode = ''
display_help = False
desired_bootorder = ''

if len(sys.argv) > 1:
    state = 'option'
    for i in range(1, len(sys.argv)):
        arg = sys.argv[i]
        if state == 'option':
            if arg in ['-U', '--user']:
                state = 'user'
            elif arg in ['-P', '--password']:
                state = 'password'
            elif arg in ['-H', '--host']:
                state = 'host'
            elif arg == 'list':
                mode = 'list'
            elif arg == 'get':
                mode = 'get'
            elif arg == 'set':
                mode = 'set'
                state = 'set'
        elif state == 'user':
            httpuser = arg
            state = 'option'
        elif state == 'password':
            httppassword = arg
            state = 'option'
        elif state == 'host':
            host = arg
            state = 'option'
        elif state == 'set':
            desired_bootorder = arg.split()
            state = 'option'
else:
    display_help = True

if not sys.argv[1:] or mode == "":
    print("Please specify a mode on the command line.", file=sys.stderr)
    display_help = True
if not host:
    print("Please specify a host on the command line.", file=sys.stderr)
    display_help = True
if not httpuser:
    print("Please specify an HTTP user on the command line.", file=sys.stderr)
    display_help = True
if not httppassword:
    print("Please specify an HTTP password on the command line.", file=sys.stderr)
    display_help = True
if display_help:
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

sy_urls = get_system_urls(host)
if len(sy_urls) == 1:
    system = os.path.basename(sy_urls[0])
bo_urls = get_bootoption_urls(host, system)

if mode == "list":
    print("Available boot devices:")
    print("")
    print("ID    |Name            |Desc")
    print("------+----------------+------------------------------------------------------")

bootoption = {}
for bo_url in bo_urls:
    id, name, desc = get_boot_option_id_name_desc(bo_url)
    bootoption[name] = desc
    if mode == "list":
        print(f"{i:<6}|{name:<16}|{desc:<58}")

#Retrieve and output the current boot order

if mode == "get":
    print("Current boot order:")
    bootorder = get_bootorder(system)
    i = 1
    for bo in bootorder:
        print(f"{i} - {bo} {bootoption[bo]}")
        i += 1

#Set the boot order

if mode == "set":
    etag = get_system_etag(system)
    ncode, code = set_bootorder(system, etag, desired_bootorder)
    if ncode>=200 and ncode<300:
        print(f'Set boot order to "{desired_bootorder}" successful! (HTTP-result: {code})')
    else:
        print(f'Set boot order to "{desired_bootorder}" failed! (HTTP-result: {code})')
