#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

"""
Slurm Utility for Trinity Project
"""

__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2022, Luna2 Project [UTILITY]"
__license__     = "GPL"
__version__     = "2.0"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Development"

from configparser import RawConfigParser
import os
import sys
import subprocess as sp
from threading import Timer
from datetime import datetime
import requests
import requests_unixsocket


class Slurm():
    """
    Slurm Class responsible to provide all Slurm activities.
    """

    def __init__(self):
        """
        Default variables should be here before calling the any method.
        """
        protocol, hostname, port, database = self.info()
        self.slurm_version = 'v0.0.38'
        self.request_time = int(datetime.now().timestamp())
        self.hostname = f'hostname={os.uname().nodename}'
        self.socket_cmd = 'http+unix://%2Fvar%2Flib%2Fslurmrestd.socket'
        self.line_protocol = ''
        self.workload_url = f'{protocol}://{hostname}:{port}/write?db={database}&precision=s'
        self.slurm_api = {
            'GET': {
                'jobs': f'/slurm/{self.slurm_version}/jobs',
                'nodes': f'/slurm/{self.slurm_version}/nodes',
                'diag': f'/slurm/{self.slurm_version}/diag'
            }
        }
        self.partition_job_states = ['RUNNING', 'PENDING', 'SUSPENDED', 'CANCELLED', 'COMPLETING',
                        'COMPLETED', 'CONFIGURING', 'FAILED', 'TIMEOUT', 'PREEMPTED', 'NODE_FAIL']


    def info(self):
        """
        This method is responsible to collect and provide the connection information to this script.
        """
        dir_path = os.path.dirname(os.path.realpath(__file__))
        slurm_config = f'{dir_path}/slurm.ini'
        protocol, hostname, port, database = False, False, False, False
        file_check = os.path.isfile(slurm_config)
        read_check = os.access(slurm_config, os.R_OK)
        if file_check and read_check:
            parser = RawConfigParser()
            parser.read(slurm_config)
            if parser.has_section('WORKLOAD'):
                protocol = self.get_option(slurm_config, parser, 'WORKLOAD', 'PROTOCOL')
                hostname = self.get_option(slurm_config, parser, 'WORKLOAD', 'HOSTNAME')
                port = self.get_option(slurm_config, parser, 'WORKLOAD', 'PORT')
                database = self.get_option(slurm_config, parser, 'WORKLOAD', 'DATABASE')
            else:
                sys.stdout.write(f'API section is not found in {slurm_config}.\n')
        else:
            sys.stdout.write(f'{slurm_config} is not found, Taking the default config.\n')
        if protocol is False:
            protocol = 'http'
            sys.stdout.write(f'Protocol is not found in {slurm_config}. Default is {protocol}.\n')
        if hostname is False:
            hostname = 'controller'
            sys.stdout.write(f'Hostname is not found in {slurm_config}. Default is {hostname}.\n')
        if port is False:
            port = '8086'
            sys.stdout.write(f'Port is not found in {slurm_config}. Default is {port}.\n')
        if database is False:
            database = 'slurm'
            sys.stdout.write(f'Database is not found in {slurm_config}. Default is {database}.\n')
        return protocol, hostname, port, database


    def get_option(self, slurm_config=None, parser=None, section=None, option=None):
        """
        This method will retrieve the value from the INI
        """
        response = False
        if parser.has_option(section, option):
            response = parser.get(section, option)
        else:
            sys.stdout.write(f'{option} is not found in {section} section in {slurm_config}.\n')
        return response


    def get_statistics(self):
        """
        This is main method. It will provide all Slurm data.
        """
        self.line_protocol = self.squeue_parse()
        self.line_protocol = self.node_cpu_parse()
        self.line_protocol = self.diag_parse()
        return self.line_protocol


    def squeue_parse(self):
        """
        This method parse all squeue data.
        """
        partitions = {}
        partition_result = []
        get_data_jobs = self.get_slurm_data(self.slurm_api['GET']['jobs'])
        if get_data_jobs:
            if 'jobs' in get_data_jobs:
                for jobs in get_data_jobs['jobs']:
                    if jobs['partition'] not in partitions.keys():
                        partitions[jobs['partition']] = [jobs['job_state']]
                    else:
                        partitions[jobs['partition']].append(jobs['job_state'])
                if partitions:
                    for name, values in partitions.items():
                        partitions[name] = {i:values.count(i) for i in values}
                    for name, values in partitions.items():
                        for key in self.partition_job_states:
                            if key not in values.keys():
                                partition_result.append(f'{key}=0')
                            else:
                                partition_result.append(f'{key}={values[key]}')
                        partition_result = ','.join(partition_result)
                        self.line_protocol += f'squeue,{self.hostname},part={name} {partition_result.lower()} {self.request_time}\n'
                        partition_result = []
        return self.line_protocol


    def node_cpu_parse(self):
        """
        This method parse all node and CPU related data.
        """
        cpu_total, alloc_cpus, idle_cpus = 0, 0, 0
        nodes_total, nodes_allocated, nodes_idle, nodes_other = 0, 0, 0, 0
        get_data_nodes = self.get_slurm_data(self.slurm_api['GET']['nodes'])
        if get_data_nodes:
            if 'nodes' in get_data_nodes:
                nodes_total = len(get_data_nodes['nodes'])
                for nodes in get_data_nodes['nodes']:
                    cpu_total = cpu_total + nodes['cpus']
                    alloc_cpus = alloc_cpus + nodes['alloc_cpus']
                    idle_cpus = idle_cpus + nodes['idle_cpus']
                    if 'idle' in nodes['state']:
                        nodes_idle = nodes_idle + 1
                    elif 'alloc' in nodes['state']:
                        nodes_allocated = nodes_allocated + 1
                    else:
                        nodes_other = nodes_other + 1
                cpu_other = cpu_total - (alloc_cpus + idle_cpus)
                cpu_data = f'cpu_allocated={alloc_cpus},cpu_idle={idle_cpus},'
                cpu_data += f'cpu_other={cpu_other},cpu_total={cpu_total}'
                self.line_protocol += f'sinfo_cpus,{self.hostname} {cpu_data} {self.request_time}\n'

                node_data = f'nodes_allocated={nodes_allocated},nodes_idle={nodes_idle},'
                node_data += f'nodes_other={nodes_other},nodes_total={nodes_total}'
                self.line_protocol += f'sinfo_nodes,{self.hostname} {node_data} {self.request_time}\n'
        return self.line_protocol


    def diag_parse(self):
        """
        This method parse all diagnostic data.
        """
        get_data_diag = self.get_slurm_data(self.slurm_api['GET']['diag'])
        if get_data_diag:
            if 'statistics' in get_data_diag:
                statistics = f"jobs_pending={get_data_diag['statistics']['jobs_pending']},"
                statistics += f"jobs_running={get_data_diag['statistics']['jobs_running']},"
                statistics += f"jobs_failed={get_data_diag['statistics']['jobs_failed']},"
                statistics += f"jobs_canceled={get_data_diag['statistics']['jobs_canceled']},"
                statistics += f"jobs_completed={get_data_diag['statistics']['jobs_completed']},"
                statistics += f"jobs_started={get_data_diag['statistics']['jobs_started']},"
                statistics += f"jobs_submitted={get_data_diag['statistics']['jobs_submitted']}"
                self.line_protocol += f'sdiag,{self.hostname} {statistics} {self.request_time}\n'
        return self.line_protocol


    def get_slurm_data(self, api=None):
        """
        This method call all the GET API of Slurm.
        """
        response = {}
        try:
            session = requests_unixsocket.Session()
            if api:
                response = session.get(f'{self.socket_cmd}{api}')
            response = response.json()
        except Exception as exp:
            self.run_sinfo()
            sys.exit(0)
            sys.stderr.write(f"UNIX SOCKET Exception while calling Slurm {exp}\n")
            sys.stderr.write(f"Slurm  is running by sinfo {exp}\n")
        return response



    def run_sinfo(self):
        """
        Returns 'return_code', 'stdout', 'stderr', 'exception'
        Where 'exception' is a content of Python exception if any
        """
        
        try:
            file_path = os.path.realpath(__file__)
            file_path = file_path.replace("slurm.py", "lsinfo.py")
            proc = sp.Popen(f"python3 {file_path}", shell=True, stdout=sp.PIPE, stderr=sp.PIPE)
            timer = Timer(10, lambda p: p.kill(), [proc])
            try:
                timer.start()
                stdout, stderr = proc.communicate()
                stdout = stdout.decode('ascii')
                print(stdout)
            except sp.TimeoutExpired as exp:
                print(f"Subprocess Timeout executing {exp}")
            finally:
                timer.cancel()
            proc.wait()
        except sp.SubprocessError as exp:
            print(f"Subprocess Error executing {cmd} Exception is {exp}")
        return True


    def post_statistics(self):
        """
        This method will post the Slurm Statistics to the Workload Management
        """
        response = None
        try:
            post_response = requests.post(self.workload_url, data=self.line_protocol, timeout=5)
            if post_response.status_code in [200, 201, 204]:
                sys.stdout.write(f'HTTP Code :: {post_response.status_code}\n')
                response = self.line_protocol
            else:
                sys.stderr.write(f'HTTP Code :: {post_response.status_code}\n')
                post_response = post_response.json()
                sys.stderr.write(f'HTTP ERROR :: {post_response["error"]}\n')
        except requests.exceptions.ConnectionError:
            sys.stderr.write(f'Connection Error :: {self.workload_url}\n')
        return response


def main():
    """
    This main method will initiate the script for pip installation.
    """
    slurm = Slurm()
    STATISTICS = slurm.get_statistics()
    if STATISTICS:
        RESPONSE = slurm.post_statistics()
        if RESPONSE:
            sys.stdout.write(RESPONSE)
            sys.exit(0)
        else:
            sys.stderr.write(f'Unable to POST on :: {slurm.workload_url}\n')
            sys.stderr.write('POST PAYLOAD --->>\n')
            sys.stderr.write(f'{slurm.line_protocol}\n')
            sys.exit(1)
    else:
        sys.stderr.write(f'Not able to find statistics with Slurm Version {slurm.slurm_version}\n')
        sys.exit(1)


if __name__ == "__main__":
    slurm = Slurm()
    STATISTICS = slurm.get_statistics()
    if STATISTICS:
        RESPONSE = slurm.post_statistics()
        if RESPONSE:
            sys.stdout.write(RESPONSE)
            sys.exit(0)
        else:
            sys.stderr.write(f'Unable to POST on :: {slurm.workload_url}\n')
            sys.stderr.write('POST PAYLOAD --->>\n')
            sys.stderr.write(f'{slurm.line_protocol}\n')
            sys.exit(1)
    else:
        sys.stderr.write(f'Not able to find statistics with Slurm Version {slurm.slurm_version}\n')
        sys.exit(1)

