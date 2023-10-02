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
Trinity Diagnosis Utility to check the status of all service and modules.
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2022, Luna2 Project [UTILITY]"
__license__     = "GPL"
__version__     = "2.0"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Development"


import os
import sys
import platform
import subprocess
from termcolor import colored


class Diagnosis():
    """
    Diagnosis Class responsible to check the things related to trinity.
    """

    def __init__(self):
        """
        Default variables should be here before calling the any method.
        """
        self.os_info = {}
        self.controller = False


    def check_controller(self):
        """
        This method will return the platform information.
        """
        self.controller = os.path.isfile('/etc/systemd/system/luna2-daemon.service')
        return self.controller


    def platform_info(self):
        """
        This method will return the platform information.
        """
        platform_name = platform.system().lower()
        if 'linux' in platform_name:
            with open("/etc/os-release", 'r', encoding='utf-8') as file:
                for line in file:
                    key,value = line.rstrip().split("=")
                    self.os_info[key.lower()] = value.strip('"')
        else:
            self.exit_diagnosis(f'{platform_name} is not yet supported by Trinity.')
        return self.os_info


    def trinity_status(self, command=None):
        """
        This method will retrieve the value from the INI
        """
        self.platform_info()
        self.check_controller()
        if self.controller is False:
            self.exit_diagnosis('TRIX Diagnosis is only Available from the Controller OR Luna2 Daemon is not present.')
        response = ''
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) as process:
            output, error = process.communicate()
            output = output.decode("utf-8")  if output else ''
            error = error.decode("utf-8")  if error else ''
            response = output if output else error
            response = response.replace('\n', '')
            response = response.replace('Active:', '')
            response = response.replace('inactive', colored('inactive', 'red', attrs=['bold', 'dark']))
            response = response.replace('(dead)', colored('(dead)', 'red', attrs=['bold']))
            if 'active (running)' in response:
                response = response.replace('active', colored('active', 'green', attrs=['bold', 'dark']))
                response = response.replace('(running)', colored('(running)', 'green', attrs=['bold']))
            if '(exited)' in response:
                response = response.replace('active', colored('active', 'green', attrs=['bold', 'dark']))
                response = response.replace('(exited)', colored('(exited)', 'green', attrs=['bold']))
            response = response.replace('could not be found', colored('could not be found', 'yellow', attrs=['bold']))
            response = response.strip()

        return response


    def execute(self, command=None):
        """
        This method will retrieve the value from the INI
        """
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) as process:
            output = process.communicate()
            output = output[0].decode("utf-8")  if output else ''
            exit_code = process.wait()
        return output, exit_code

    def exit_diagnosis(self, message=None):
        """
        This method will exit from the script with the message
        """
        sys.stderr.write(colored(f'ERROR :: {message}\n', 'red', attrs=['bold']))
        sys.exit(1)


def main():
    """
    This main method will initiate the script for pip installation.
    """
    response = {
        "Trinity Core": {"chronyd": None, "named": None, "dhcpd": None, "mariadb": None, "nfs-server": None, "nginx": None},
        "Luna": {"luna2-daemon": None, "transmission-daemon": None},
        "LDAP": {"slapd": None, "sssd": None, "nslcd": None, },
        "Slurm": {"slurmctld": None, },
        "Monitoring core": {"influxdb": None, "telegraf": None, "grafana-server": None, "sensu-server": None, "sensu-api": None, "rabbitmq-server": None},
        "Trinity OOD": {"httpd": None}
    }

    for key, value in response.items():
        for service, val in value.items():
            response[key][service] = Diagnosis().trinity_status(f"systemctl status {service}.service | grep Active:")
    for key, value in response.items():
        print(colored(key, 'white', attrs=['bold']))
        for service, val in value.items():
            print(f'\t{service}: {val}')
        print('\n')


if __name__ == "__main__":
    main()
