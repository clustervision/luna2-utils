# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-

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

import os
import sys
import json
import requests
import datetime
import requests_unixsocket

"""
Process:
squeue
    get jobs api result                     'jobs'
    count the jobs inside result                  "jobs": [{}]
    count the job_state inside jobs                           "job_state": "PENDING",
    partition name inside jobs                                 "partition": "defq",

sinfo_cpus
    get nodes api result                     'nodes'
    count the nodes inside result                  "nodes": [{}]
    count the cpus inside jobs                           "cpus": 1,
    count the alloc_cpus inside jobs                     "alloc_cpus": 0,
    count the idle_cpus inside jobs                      "idle_cpus": 1,

    cpus[]                  is cpu_total
    alloc_cpus[]            is total alloc_cpus
    idle_cpus[]             is total cpu_idle
    alloc_cpus - idle_cpus  is total cpu_other

    
sinfo_nodes
SAME As ----->>>> sinfo_cpus

"""

class Slurm():
    """
    Slurm Class responsible to provide all Slurm activities.
    """

    def __init__(self):
        """
        Default variables should be here before calling the any method.
        
        Expected Output:
        squeue,hostname=controller1.cluster,part=defq running=0,pending=11,suspended=0,cancelled=0,completing=0,completed=0,configuring=0,failed=0,timeout=0,preempted=0,node_fail=0 1683112549
        sinfo_cpus,hostname=controller1.cluster cpu_allocated=0,cpu_idle=1,cpu_other=1,cpu_total=2 1683112549
        sinfo_nodes,hostname=controller1.cluster nodes_allocated=0,nodes_idle=1,nodes_other=1,nodes_total=2 1683112549
        sdiag,hostname=controller1.cluster jobs_pending=11,jobs_running=0 1683112549
        """
        self.slurm_version = 'v0.0.38'
        self.job_id = None
        # self.node_name = None
        # self.partition_name = None
        # self.reservation_name = None
        self.request_time = datetime.datetime.now().strftime("%s")
        self.hostname = "hostname={}".format(os.uname().nodename)
        self.socket_cmd = 'http+unix://%2Fvar%2Flib%2Fslurmrestd.socket'
        self.line_protocol = None
        self.workload_url = "http://localhost:8086/write?db=slurm&precision=s"
        self.slurm_api = {
            'GET': {
                'jobs': f'/slurm/{self.slurm_version}/jobs',
                'nodes': f'/slurm/{self.slurm_version}/nodes'
            }
        }


    def get_statistics(self):
        """
        This is main method. It will provide all Slurm data.
        """
        partitions = []
        output = ""
        output_jobs = ['jobs']
        api_name = 'jobs'
        get_data_jobs = self.get_slurm_data(self.slurm_api['GET']['jobs'])
        if get_data_jobs:
            if 'jobs' in get_data_jobs:
                for jobs in get_data_jobs['jobs'].items():
                    partitions.append({'partition':jobs['partition'], 'state':jobs['job_state']})


        print('===============================================================')
        print(f'API Name: {api_name}')
        print(f'API: {self.slurm_api["GET"][api_name]}')
        print(f'Response: {get_data_jobs}')
        print('===============================================================\n\n\n')


        # output = ""
        # # output_jobs = ['jobs']
        # # api_name = 'jobs'
        # # get_data_jobs = self.get_slurm_data(self.slurm_api['GET'][api_name])
        # # print('===============================================================')
        # # print(f'API Name: {api_name}')
        # # print(f'API: {self.slurm_api["GET"][api_name]}')
        # # print(f'Response: {get_data_jobs}')
        # # print('===============================================================\n\n\n')

        # output_nodes = []
        # node_output = ""
        # api_name = 'nodes'
        # get_data_nodes = self.get_slurm_data(self.slurm_api['GET'][api_name])
        # print('===============================================================')
        # print(f'API Name: {api_name}')
        # print(f'API: {self.slurm_api["GET"][api_name]}')
        # print(f'Response: {get_data_nodes}')
        # print('===============================================================\n\n\n')
        # for nodes in get_data_nodes['nodes']:
        #     if 'hostname' in nodes.keys():
        #         tmp_node = []
        #         for key, value in nodes.items():
        #             if value:
        #                 tmp_node.append(f'{key}={value}')
        #         tmp_node = ','.join(tmp_node)
        #         node_output = f'hostname={nodes["hostname"]}'
        #         node_output = f'{node_output} {tmp_node}'
        #         tmp_node = []
        #     output_nodes.append(node_output)
        #     node_output = ""
        #     # print(f'tmp_node: {tmp_node}')
        #     # print(f'nodes: {nodes}')
        # print(f'output_nodes: {output_nodes}')
        # for each_node in output_nodes:
        #     output = f'{output}\n slurm_nodes.{each_node}'
        # print(f'output: {output}')
        # for name, api in self.slurm_api['GET'].items():
        #     get_data = self.get_slurm_data(api)
        #     print('===============================================================')
        #     print(f'API Name: {name}')
        #     print(f'API: {api}')
        #     print(f'Response: {get_data}')
        #     print('===============================================================\n\n\n')
        result = False
        if result:
            self.line_protocol += f'squeue,{self.hostname},part={} {} {self.request_time}\n' # queuename, kev.rstrip(',')
            self.line_protocol += f'sinfo_cpus,{self.hostname} cpu_allocated={},cpu_idle={},cpu_other={},cpu_total={} {self.request_time}\n' # cpu_a, cpu_i, cpu_o, cpu_t
            self.line_protocol += f'sinfo_nodes,{self.hostname} nodes_allocated={},nodes_idle={},nodes_other={},nodes_total={} {self.request_time}\n' # node_a, node_i, node_o, node_t
            self.line_protocol += f'sdiag,{self.hostname} jobs_pending={},jobs_running={} {self.request_time}\n' # jobs_pending,jobs_running
        return self.line_protocol


    def get_slurm_data(self, api=None):
        """
        This method call all the GET API of Slurm.
        """
        response = {}
        try:
            session = requests_unixsocket.Session()
            if api:
                self.socket_cmd = f'{self.socket_cmd}{api}'
            slurm_call = session.get(self.socket_cmd)
            response = slurm_call.json()
        except Exception as exp:
            sys.stderr.write(f"UNIX SOCKET Exception while calling Slurm {exp}\n")
        # return json.dumps(response)
        return response


    def post_statistics(self):
        """
        This method will post the Slurm Statistics to the Workload Management
        """
        response = False
        try:
            response = requests.post(self.workload_url, data=self.line_protocol, timeout=5)
            if response.status_code in [200, 201, 204]:
                sys.stdout.write(f'HTTP Code :: {response.status_code}\n')
                response = self.line_protocol
            else:
                sys.stderr.write(f'HTTP Code :: {response.status_code}\n')
        except requests.exceptions.ConnectionError:
            sys.stderr.write(f'Connection Error :: {self.workload_url}\n')
        return response


if __name__ == "__main__":
    slurm = Slurm()
    STATISTICS = slurm.get_statistics()
    if STATISTICS:
        RESPONSE = slurm.get_statistics()
        if RESPONSE:
            sys.stdout.write(RESPONSE)
            sys.exit(0)
        else:
            sys.stderr.write(f'Unable to POST date on :: {slurm.line_protocol}\n')
            sys.stderr.write(f'Unable to POST on :: {slurm.workload_url}\n')
            sys.exit(1)
    else:
        sys.stderr.write(f'Not able to find statistics with Slurm Version {slurm.slurm_version}\n')
        sys.exit(1)
