#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sensu Monitoring System Class Trinity Project
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2022, Luna2 Project [CLI]"
__license__     = "GPL"
__version__     = "2.0"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Development"


import json
import requests

class Sensu(object):
    """
    Sensu Class responsible to all activities
    related to the Sensu Monitoring System.
    """

    def __init__(self):
        """
        Default variables should be here
        before calling the any method.
        """
        self.monitor = 'monitoring.clustervision.com'
        self.server_hostname = 'mgm-sensu-01.taurusgroup.one'
        self.server_ip = '192.168.160.55'
        self.server_port = 4567
        self.server_url = f'http://{self.server_ip}:{self.server_port}/'


    def get_data(self, uri=None):
        """
        This method is based on REST API's GET method.
        It will fetch the records from Sensu via REST API's.
        """
        response = False
        self.server_url = f'{self.server_url}{uri}'
        call = requests.get(url=self.server_url, timeout=5)
        print(f'Response Content => {call.content}, and HTTP Code {call.status_code}')
        if call:
            response = call.json()
        return response


    def post_data(self, uri=None, data=None):
        """
        This method is based on REST API's POST method.
        It will post data to Sensu via REST API's.
        """
        response = False
        headers = {'Content-Type': 'application/json'}
        self.server_url = f'{self.server_url}{uri}'
        call = requests.post(url=self.server_url, data=json.dumps(data), headers=headers, timeout=5)
        print(f'Response Content => {call.content}, and HTTP Code {call.status_code}')
        if call:
            response = call.json()
        return response


    def delete_data(self, uri=None):
        """
        This method is based on REST API's DELETE method.
        It will delete data on Sensu via REST API's.
        """
        response = False
        headers = {'Content-Type': 'application/json'}
        self.server_url = f'{self.server_url}{uri}'
        call = requests.post(url=self.server_url, headers=headers, timeout=5)
        print(f'Response Content => {call.content}, and HTTP Code {call.status_code}')
        if call:
            response = call.json()
        return response


    def clients_get(self, name=None):
        """
        This method GET the Sensu client registry.
        """
        response = False
        uri = 'clients'
        if name:
            uri = f'{uri}/{name}'
        return response


    def clients_post(self, name=None):
        """
        This method POST to the Sensu client registry.
        """
        response = False
        uri = 'clients'
        if name:
            uri = f'{uri}/{name}'
        return response


    def clients_delete(self, name=None):
        """
        This method DELETE the Sensu client registry.
        """
        response = False
        uri = 'clients'
        if name:
            uri = f'{uri}/{name}'
        return response


    def checks_get(self, name=None):
        """
        This method GET the subscription check.
        """
        response = False
        uri = 'checks'
        if name:
            uri = f'{uri}/{name}'
        return response


    def checks_post_request(self, data=None):
        """
        This method POST to the Sensu client registry.
        """
        response = False
        uri = 'request'
        print(uri)
        print(data)
        return response


    def checks_delete(self, name=None):
        """
        This method DELETE the subscription check.
        """
        response = False
        uri = 'checks'
        if name:
            uri = f'{uri}/{name}'
        return response
