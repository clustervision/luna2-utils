#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Utilities Class for the Health Checker
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2022, Luna2 Project [CLI]"
__license__     = "GPL"
__version__     = "2.0"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Development"

from configparser import RawConfigParser
import json
import os
import requests
import subprocess as sp
from threading import Timer

class Utils(object):
    """
    All kind of REST Call methods.
    """

    def __init__(self):
        """
        Constructor - Before calling any REST API
        it will fetch the credentials and endpoint url
        from luna.ini from Luna 2 Daemon.
        """
        # self.logger = Log.get_logger()
        self.username, self.password, self.daemon = '', '', ''
        ini_file = '/trinity/local/luna/config/luna.ini'
        file_check = os.path.isfile(ini_file)
        read_check = os.access(ini_file, os.R_OK)
        # print(f'INI File => {ini_file} READ Check is {read_check}')
        if file_check and read_check:
            configparser = RawConfigParser()
            configparser.read(ini_file)
            if configparser.has_option('API', 'USERNAME'):
                self.username = configparser.get('API', 'USERNAME')
            if configparser.has_option('API', 'PASSWORD'):
                self.password = configparser.get('API', 'PASSWORD')
            if configparser.has_option('API', 'ENDPOINT'):
                self.daemon = configparser.get('API', 'ENDPOINT')
            # print(f'INI File Username => {self.username}, Password {self.password} and Endpoint {self.daemon}')
        else:
            print(f'{ini_file} is not found on this machine.')


    def get_token(self):
        """
        This method will fetch a valid token
        for further use.
        """
        data = {}
        response = False
        data['username'] = self.username
        data['password'] = self.password
        daemon_url = f'http://{self.daemon}/token'
        print(f'Token URL => {daemon_url}')
        call = requests.post(url = daemon_url, json=data, timeout=5)
        print(f'Response Content => {call.content}, and HTTP Code {call.status_code}')
        data = call.json()
        if 'token' in data:
            response = data['token']
        return response


    def get_data(self, route=None, uri=None):
        """
        This method is based on REST API's GET method.
        It will fetch the records from Luna 2 Daemon
        via REST API's.
        """
        response = False
        headers = {'x-access-tokens': self.get_token()}
        daemon_url = f'http://{self.daemon}/{route}'
        if uri:
            daemon_url = f'{daemon_url}/{uri}'
        print(f'RAW URL => {daemon_url}')
        call = requests.get(url=daemon_url, headers=headers, timeout=5)
        if call:
            response = call.json()
        return response


    def run_cmd(self, cmd=None, timeout=30):
        """
        Returns 'rc', 'stdout', 'stderr', 'exception'
        Where 'exception' is a content of Python exception if any
        """
        rc = 255
        stdout, stderr, exception = "", "", ""
        try:
            proc = sp.Popen(
                cmd, shell=True,
                stdout=sp.PIPE, stderr=sp.PIPE
            )
            timer = Timer(
                timeout, lambda p: p.kill(), [proc]
            )
            try:
                timer.start()
                stdout, stderr = proc.communicate()
            except:
                print(f"Timeout executing {cmd}")
                # log.debug("Timeout executing '{}'".format(cmd))
            finally:
                timer.cancel()

            proc.wait()
            rc = proc.returncode
        except Exception as e:
            exception = e
        return rc, stdout, stderr, exception
