#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

import sys
import subprocess
from threading import Timer
from termcolor import colored
import platform

class Diagnosis():
    """
    Diagnosis Class responsible to check the things related to trinity.
    """

    def __init__(self):
        """
        Default variables should be here before calling the any method.
        """
        self.errors = []
        self.os_info = {}


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
            self.exit_diagnosis(f'{platform_name} is not yet supported by Trinity, contact us @clustervision.')
        return self.os_info

    def trinity_status(self, command=None):
        """
        This method will retrieve the value from the INI
        """
        self.platform_info()
        # print(self.platform_info())
        output = ''
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) as process:
            output = process.communicate()
            output = output[0].decode("utf-8")  if output else ''
            exit_code = process.wait()
        # print(output)
        # print(exit_code)
        # print(platform.uname())
        return output
        

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
    return LCluster().health_checkup()


if __name__ == "__main__":
    # Dictionary of desired result
    # Check System
    # Correct command
    # Run commands retrive result
    # show resutl

    response = {
        "Trinity Core": {"chronyd": None, "named": None, "dhcpd": None, "mariadb": None, "nfs-server": None, "nginx": None},
        "Luna": {"luna2-daemon": None, "ltorrent": None},
        "LDAP": {"slapd": None, "sssd": None, "nslcd": None, },
        "Slurm": {"slurmctld": None, },
        "Monitoring core": {"influxdb": None, "telegraf": None, "grafana-server": None, "sensu-server": None, "sensu-api": None, "rabbitmq-server": None},
        "Trinity OOD": {"httpd": None}
        }
    

    # diagnosis = Diagnosis()
    for key, value in response.items():
        # print(key)
        for service, val in value.items():
            resp = Diagnosis().trinity_status(f"systemctl status {service}.service | grep Active: | cut -c 14-")
            print(resp)
    # Diagnosis().trinity_status("systemctl status luna2-daemon.service | grep Active: | cut -c cut -c 14-")
