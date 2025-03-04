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

__author__      = 'Antoine Schonewille'
__copyright__   = 'Copyright 2025, Luna2 Project'
__license__     = 'GPL'
__version__     = '2.1'
__maintainer__  = 'Dev-team'
__email__       = 'antoine.schonewille@clustervision.com'
__status__      = 'Development'

#VERSION: 0.1

import os
import getpass
import sys
from builtins import dict
import re
import json
from time import sleep, time
import subprocess
import shutil
import requests
from requests import Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry
from utils.utils.log import Log
from utils.utils.ini import Ini
from utils.utils.token import Token

TMP_DIR='/tmp'

urllib3.disable_warnings()
session = Session()
retries = Retry(
    total = 10,
    backoff_factor = 0.3,
    status_forcelist = [502, 503, 504],
    allowed_methods = {'GET', 'POST'}
)
session.mount('https://', HTTPAdapter(max_retries=retries))

logger = Log.init_log(log_file='/var/log/luna/lexport.log',log_level='info')
CONF = Ini.read_ini(ini_file='/trinity/local/luna/utils/config/luna.ini')

# ============================================================================

def main(argv):
    """
    The main method to initiate the script.
    """
    command = sys.argv
    command[0] = 'lexport'
    command = ' '.join(command)
    logger.info(f'User {getpass.getuser()} ran => {command}')

    ACTION=None
    WHAT=None
    FILE=None
    IMAGEPATH=None
    IMAGENAME=None
    MATTHEW=None
    FORCE=False
    if (len(argv) == 0):
        call_help()
        exit()
    while len(argv)>0:
        item = argv.pop(0)
        if (item == "-h" or item == "--help"):
            call_help()
            exit()
        elif (item == "-c" or item == "--cluster"):
            WHAT='cluster'
        elif (item == "-o" or item == "--osimage"):
            WHAT='image'
        elif (item == "-e" or item == "--export"):
            ACTION='export'
        elif (item == "-i" or item == "--import"):
            ACTION='import'
        elif (item == "-f" or item == "--force"):
            FORCE=True
        elif (item == "-p" or item == "--path"):
            IMAGEPATH=argv.pop(0)
        elif (item == "-m" or item == "--matthew"):
            MATTHEW=argv.pop(0)
        elif (item == "-n" or item == "--name"):
            IMAGENAME=argv.pop(0)
        elif (item[0] == "-"):
            print("ERROR :: Invalid options used.")
            call_help()
            exit()
        else:
            FILE=item
    if ((WHAT is None) or (ACTION is None)):
        print("ERROR :: Instruction incomplete. Required options or flags missing.")
        call_help()
        exit()
    if WHAT == 'cluster':
        handleClusterRequest(action=ACTION,file=FILE,force=FORCE)
    elif WHAT == 'image':
        handleImageRequest(action=ACTION,file=FILE,name=IMAGENAME,path=IMAGEPATH,config_file=MATTHEW,force=FORCE)
    exit()

# ============================================================================

def call_help():
    """
    This method will provide a Help Menu.
    """
    print("""
usage: lexport <-c|-o> <-e|-i> [file]

Luna configuration im/exporter.

positional arguments:
  -c, --cluster         cluster level.
  -o, --osimage         osimage level.
  -e, --export          exports configuration.
  -i, --import          imports configuration/data.

optional arguments:
  file                  use file for imports and exports. mandatory when importing.
                        when exporting osimage and no file given, it will render
                        a file based on cluster name, osimage name and date.
                        without --force it will warn if a file will be overwritten.
  -n, --name            used only in combination with osimage operations.
  -m, --matthew         use an external config file during osimage operations, Matthew mode. 
                        used for osimage imports and exports. handle with care.
  -h, --help            show this help message and exit.
  -f, --force           do not warn, do not ask, just do it.

examples:
  lexport -c -e /tmp/cluster-config.dat     exports all cluster configuration to /tmp/cluster-config.dat
  lexport -c -e                             exports all cluster configuration and prints to STDOUT
  lexport -c -i /tmp/cluster-config.dat     imports all cluster configuration from /tmp/cluster-config.dat
  lexport -o -e -n compute /tmp/compute.tar exports compute osimage to compute.tar with embedded configuration
  lexport -o -i /tmp/compute.tar            imports compute.tar with embedded configuration
  lexport -o -i /tmp/compute.tar -p /trinity/images/compute_2    
                                            imports compute.tar, using embedded configuration but
                                            overrides path to /trinity/images/compute_2

    """)


# ----------------------------------------------------------------------------

def handleClusterRequest(action=None,file=None,force=False):
    if (action and action == 'export') or (action and action == 'import' and file):
        CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])

        RET={'400': 'invalid request', '404': 'unhandled request', '401': 'action not authorized', '503': 'service not available'}
        headers = {'x-access-tokens': CONF['TOKEN']}

        if action == 'export':
            try:
                r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/cluster/export',headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if status_code == '200':
                    if file:
                        if os.path.exists(file) and not force:
                            print(f"STOP :: {file} already exists. Please use another name or use --force to override")
                            logger.error(f"CONFIG: STOP :: {file} already exists.")
                            exit(1)
                        with open(file,'w', encoding = "utf-8") as file:
                            file.write(r.text)
                    else:
                        print (r.text)
                    exit(0)
                else:
                    print(f"ERROR :: trouble processing request: {r.text}")
                    logger.error(f"CONFIG: export error processing request: {r.text}")
                    exit(2)
            except requests.exceptions.HTTPError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.ConnectionError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.Timeout as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
        elif action == 'import':
            try:
                if not os.path.exists(file):
                    print(f"STOP :: {file} does not exist")
                    logger.error(f"CONFIG: STOP :: {file} does not exist")
                    exit(1)
                with open(file,'r', encoding = "utf-8") as file:
                    data=file.read()
                    myjson=json.loads(data)
                r = requests.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/cluster/import', json=myjson, headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if status_code == '201':
                    print(f"finished importing configuration")
                    exit(0)
                print(f"importing failed with code {status_code}: {r.text}")
                logger.error(f"CONFIG: importing failed with code {status_code}: {r.text}")
   
            except requests.exceptions.HTTPError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.ConnectionError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.Timeout as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
    else:
        print("ERROR :: not enough parameters to run with")
        exit(2)

# ----------------------------------------------------------------------------

def handleImageRequest(action=None,file=None,name=None,path=None,config_file=None,force=False):
    if (action and action == 'export') or (action and action == 'import' and file):
        CONF['TOKEN']=Token.get_token(username=CONF['USERNAME'], password=CONF['PASSWORD'], protocol=CONF["PROTOCOL"], endpoint=CONF["ENDPOINT"], verify_certificate=CONF["VERIFY_CERTIFICATE"])
        
        RET={'400': 'invalid request', '404': 'unknown osimage', '401': 'action not authorized', '503': 'service not available'}
        headers = {'x-access-tokens': CONF['TOKEN']}

        if action == 'export':
            if not name:
                print("ERROR :: not enough parameters to run with. i need an osimage name.")
                exit(2)
            cluster_name = 'cluster'
            try:
                r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/cluster',headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if status_code == '200':
                    data=json.loads(r.text)
                    cluster_name = data['config']['cluster']['name'] or 'cluster'
                r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/osimage/{name}', headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                status_code=str(r.status_code)
                if status_code == '200':
                    data=json.loads(r.text)
                    config=data['config']['osimage'][name]
                    if file:
                        file = f"{file}.tar"
                    else:
                        epoch_time = int(time())
                        file = f"{cluster_name}-{name}-{epoch_time}.tar"
                    logger.info(f"EXPORT: config: {config}, file: {file}")
                    if os.path.exists(file) and not force:
                        print(f"STOP :: {file} already exists. Please use another name or use --force to override")
                        exit(1)
                    image_file=f'{cluster_name}-{name}.tar.bz2'
                    logger.info(f"EXPORT: image_file: {image_file}")
                    if os.path.exists(image_file):
                        os.remove(image_file)
                    if config['path']:
                        ret = pack_image(cluster=cluster_name, name=name, path=config['path'], image_file=image_file)
                    if not ret:
                        print("ERROR :: Encountered a problem exporting osimage")
                        logger.error(f"EXPORT: ERROR :: Encountered a problem exporting osimage")
                        exit(4)
                    if file:
                        raw_config=json.loads(r.text)
                        image_config=raw_config['config']['osimage'][name]
                        if 'assigned_tags' in image_config:
                            del image_config['assigned_tags']
                        if 'tag' in image_config:
                            del image_config['tag']
                        if config_file: # Matthew mode
                            if os.path.exists(config_file) and not force:
                                print(f"STOP :: {config_file} already exists. Please use another name or use --force to override")
                                logger.error(f"EXPORT: STOP :: {config_file} already exists")
                                exit(1)
                            with open(config_file,'w', encoding = "utf-8") as mfile:
                                mfile.write(json.dumps(image_config))
                        ret = merge(file=file, image_file=image_file, config=json.dumps(image_config))
                        if not ret:
                            print("ERROR :: Encountered a problem exporting osimage")
                            logger.error(f"EXPORT: ERROR :: Encountered a problem exporting osimage")
                            exit(4)
                        print(f"finished exporting to {file}")
                        exit(0)
                    else:
                        print(f"STOP :: i do not have a file to write to. not sure what went wrong")
                        logger.error(f"EXPORT: STOP :: i do not have a file to write to")
                        exit(1)
                    exit(0)
                else:
                    print(f"ERROR :: trouble processing request: {r.text}")
                    logger.error(f"Error processing request: {r.text}")
                    exit(2)
            except requests.exceptions.HTTPError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.ConnectionError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.Timeout as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
        elif action == 'import':
            try:
                if not os.path.exists(file):
                    print(f"STOP :: {file} does not exist")
                    exit(1)
                image_config, image_file=unmerge(file=file)
                if (not image_config) or (not image_file):
                    print(f"STOP :: cannot continue. Missing config and/or a path. Is this a valid exported osimage?")
                    logger.error(f"IMPORT: STOP :: cannot continue. Missing config and/or a path. Is this a valid exported osimage?")
                    exit(1)
                if config_file: # Matthew mode
                    with open(config_file,'r', encoding = "utf-8") as mfile:
                        data=mfile.read()
                        image_config=json.loads(data)
                if 'assigned_tags' in image_config:
                    del image_config['assigned_tags']
                logger.info(f"config: {image_config}, file: {image_file}")
                image_path=image_config['path']
                if path:
                    image_path=path
                    image_config['path']=path
                if len(image_path) < 2 and not force:
                    print(f"STOP :: path {image_path} does not feel correct. Please use another path or use --force to override")
                    logger.error(f"IMPORT: STOP :: path {image_path} does not feel correct")
                    exit(1)
                if os.path.exists(image_path) and not force:
                    print(f"STOP :: path {image_path} already exists. Please use another path or use --force to override")
                    logger.error(f"IMPORT: STOP :: path {image_path} already exists")
                    exit(1)
                if os.path.exists(image_path):
                    shutil.rmtree(image_path)
                os.makedirs(image_path)
                image_name=image_config['name']
                if name:
                    image_name=name
                    image_config['name']=name
                r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/osimage/{name}', headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                logger.info(f"image check: {r.status_code}")
                status_code=str(r.status_code)
                if status_code == '200' and not force:
                    print(f"STOP :: osimage {image_name} already exists. Please use another name or use --force to override")
                    logger.error(f"IMPORT: STOP :: osimage {image_name} already exists")
                    exit(1)
                r = requests.get(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/osimage/{image_name}/_delete', headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                logger.info(f"delete return: {r.status_code}")
                myjson={'config': {'osimage': {image_name: image_config}}}
                logger.info(f"myjson: {myjson}")
                r = requests.post(f'{CONF["PROTOCOL"]}://{CONF["ENDPOINT"]}/config/osimage/{image_name}', json=myjson, headers=headers, stream=True, timeout=10, verify=CONF["VERIFY_CERTIFICATE"])
                logger.info(f"add image returned: {r.status_code}")
                status_code=str(r.status_code)
                if status_code == '201':
                    ret=unpack_image(name=image_name,path=image_path,image_file=image_file)
                    if not ret:
                        logger.error(f"IMPORT: ERROR :: Encountered a problem exporting osimage")
                        print("ERROR :: Encountered a problem exporting osimage")
                        exit(4)
                else:
                    print(f"ERROR :: trouble processing request: {r.text}")
                    logger.error(f"IMPORT: ERROR :: Error processing request: {r.text}")
                    exit(2)
                print(f"finished importing {image_name}")
                exit(0)
   
            except requests.exceptions.HTTPError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.ConnectionError as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
                exit(3)
            except requests.exceptions.Timeout as err:
                print("ERROR :: trouble getting results: "+str(err))
                logger.error(f"trouble getting results: {err}")
    else:
        print("ERROR :: not enough parameters to run with")
        exit(2)

# ----------------------------------------------------------------------------

def pack_image(cluster='cluster',name=None,path=None,image_file=None):
    if name and path and image_file:
        epoch_time = int(time())
        packed_image_file = f"{TMP_DIR}/{image_file}"
        if os.path.exists('/usr/bin/lbzip2') and os.path.exists('/usr/bin/tar'):
            try:
                output = subprocess.check_output(
                    [
                        '/usr/bin/tar',
                        '-C', f"{path}",
                        '--one-file-system',
                        '--xattrs',
                        '--selinux',
                        '--acls',
                        '--ignore-failed-read',
                        '--exclude=/proc/*',
                        '--exclude=/dev/*',
                        '--exclude=/sys/*',
                        '--exclude=/tmp/*',
                        '--checkpoint=100000',
                        '--use-compress-program=/usr/bin/lbzip2',
                        '-c', '-f', packed_image_file, '.'
                    ],
                    stderr=subprocess.STDOUT,
                    universal_newlines=True)
            except subprocess.CalledProcessError as exc:
                if os.path.isfile(packed_image_file):
                    os.remove(packed_image_file)
                output=f"{exc.output}"
                outputs=output.split("\n")
                joined='. '.join(outputs[-5:])
                print(f"Tarring {name} failed with exit code {exc.returncode}: {joined}")
                logger.error(f"Tarring {name} failed with exit code {exc.returncode}: {joined}")
                return False
            #print(f"Tarring {name} successful.")
            return True
        print("tar and bzip2 need to be installed")
        logger.error(f"tar and/or bzip missing")
        return False
    print("Not enough parameters to work with. could not continue due to missing osimage name or path")
    logger.error(f"Not enough parameters to work with. could not continue due to missing osimage name or path")
    return False

def merge(file=None,image_file=None,tmp_dir=None,config=None):
    if file and image_file and config:
        with open(f"{TMP_DIR}/.config.dat",'w', encoding = "utf-8") as config_file:
            config_file.write(config)
        with open(f"{TMP_DIR}/.osimage.dat",'w', encoding = "utf-8") as osimage_file:
            osimage_file.write(f"{image_file}")
        if os.path.exists('/usr/bin/tar'):
            try:
                output = subprocess.check_output(
                    [
                        '/usr/bin/tar',
                        '-C', f"{TMP_DIR}/",
                        '-cf', f"{file}", 
                        '.config.dat',
                        '.osimage.dat',
                        f"{image_file}"
                    ],
                    stderr=subprocess.STDOUT,
                    universal_newlines=True)
            except subprocess.CalledProcessError as exc:
                output=f"{exc.output}"
                outputs=output.split("\n")
                joined='. '.join(outputs[-5:])
                print(f"Merging {file} failed with exit code {exc.returncode}: {joined}")
                logger.error(f"Merging {file} failed with exit code {exc.returncode}: {joined}")
                return False
            return True
        print("tar needs to be installed")
        return False
    return False

def unpack_image(name=None,path=None,image_file=None):
    if name and image_file:
        packed_image_file=f"{TMP_DIR}/{image_file}"
        if os.path.exists('/usr/bin/lbzip2') and os.path.exists('/usr/bin/tar'):
            try:
                my_proc = subprocess.Popen(
                    f"cd {path} && /usr/bin/lbzip2 -dc < {packed_image_file} | /usr/bin/tar xf -",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=True)
                output = my_proc.communicate()
                exit_code = my_proc.wait()
                logger.info(f"UNPACK_IMAGE: file: {packed_image_file}, exit_code: {exit_code}, output: {output}")
                if exit_code != 0:
                    print(f"Untarring failed: {output[1]}")
                    logger.error(f"Untarring failed: {output[1]}")
                    return False
            except subprocess.CalledProcessError as exc:
                output=f"{exc.output}"
                outputs=output.split("\n")
                joined='. '.join(outputs[-5:])
                print(f"Untarring {name} failed with exit code {exc.returncode}: {joined}")
                logger.error(f"Untarring {name} failed with exit code {exc.returncode}: {joined}")
                return False
            #print(f"Untarring {name} successful.")
            return True
        print("tar and bzip2 need to be installed")
        logger.error(f"tar and/or bzip missing")
        return False
    print("Not enough parameters to work with. could not continue due to missing osimage name or path")
    logger.error(f"Not enough parameters to work with. could not continue due to missing osimage name or path")
    return False

def unmerge(file=None):
    if file:
        config, image_file = None, None
        if os.path.exists('/usr/bin/tar'):
            try:
                output = subprocess.check_output(
                    [
                        '/usr/bin/tar', '-C', f"{TMP_DIR}",
                        '-xf', f"{file}",
                        '.config.dat',
                        '.osimage.dat'
                    ],
                    stderr=subprocess.STDOUT,
                    universal_newlines=True)
                with open(f"{TMP_DIR}/.config.dat",'r', encoding = "utf-8") as config_file:
                    data=config_file.read()
                    config=json.loads(data)
                with open(f"{TMP_DIR}/.osimage.dat",'r', encoding = "utf-8") as osimage_file:
                    image_file=osimage_file.read()
            except subprocess.CalledProcessError as exc:
                output=f"{exc.output}"
                outputs=output.split("\n")
                joined='. '.join(outputs[-5:])
                print(f"Unmerging {file} failed with exit code {exc.returncode}: {joined}")
                logger.error(f"Unmerging {file} failed with exit code {exc.returncode}: {joined}")
                return None, None
            return config, image_file
        print("tar needs to be installed")
        logger.error("Tar is missing")
        return False
    return None, None

# ----------------------------------------------------------------------------

# hidden at the bottom; the call for the main function...
main(sys.argv[1:])
