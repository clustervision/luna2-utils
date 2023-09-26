#!/trinity/local/python/bin/python3
# -*- coding: utf-8 -*-

import sys
import subprocess
import os
import datetime
import platform
import pprint
import requests
import json

checktime = datetime.datetime.now().strftime("%s")
tagline = "hostname={}".format(os.uname().nodename)
lineprotocol = ""
url = "http://localhost:8086/write?db=slurm&precision=s"

slurm_output = {}
commandlist = {
    'resources': 'sinfo --all --long --json',
    'jobs': 'squeue -i 60 --all --long --json'
}

def exec_slurm(checkcommand,checkname):
    try:
        process_output = subprocess.check_output(checkcommand,shell=True,stderr=subprocess.STDOUT,universal_newlines=True)
    except subprocess.CalledProcessError as e:
        print("Failure to execute command" + e.output)
        sys.exit(2)
    return checkname, process_output


# Loop through the commands. Then update the dictionary as 'command' -> 'output'
for checkname, checkcommand in commandlist.items():
    process_output = exec_slurm(checkcommand,checkname)
    slurm_output.update([process_output])

# Loop through command and process the output.
for check, output in slurm_output.items():

    if check == "jobs":
        jobs = []
        output_dict = json.loads(output)

        possible_job_states = ["RUNNING", "PENDING", "SUSPENDED", "COMPLETING", "COMPLETED", "CONFIGURING", "CANCELLED", "FAILED" ]
        for output_dict_job in output_dict.get('jobs', []):

            # states = {k.lower():0 for k in possible_job_states}
            state = output_dict_job["job_state"]
            if state not in possible_job_states:
                state = "UNKNOWN"
            state = state.lower()

            job = {}
            job["job_id"] = output_dict_job["job_id"]
            job["partition"] = output_dict_job["partition"]
            job["state"] = state
            jobs.append(job)

        for job in jobs:
            tag_keys = {"partition": lambda x: f'"{x}"', "job_id":  lambda x: f'"{x}"', "state":  lambda x: f'"{x}"'}
            value_keys = {}
            tags, values = "", ""
            
            for k, v in job.items():
                if k in tag_keys:
                    tags += "{}={},".format(k, v)
                    values += "_{}={},".format(k, tag_keys[k](v))
                elif k in value_keys:
                    values += "{}={},".format(k, value_keys[k](v))
                else:
                    raise Exception("Unknown key: {}".format(k))

            tags = tags.rstrip(",")
            values = values.rstrip(",")

            line = "{},{} {} {}\n".format(check, tags, values, checktime)
            lineprotocol += line

        # job states are 
    if check == "resources":
        nodes = []
        # node states are allocated, completing, down, drained, draining, fail, failing, future, idle, maint, mixed, perfctrs, planned, power_down, power_up, reserved, and unknown
        possible_node_states = ["allocated", "fail", "idle", "mixed", "planned", "power_down", "power_up", "reserved", ]
        
        output_dict = json.loads(output)
       
        for output_dict_node in output_dict.get('nodes', []):
            output_dict_node_partitions = output_dict_node["partitions"]
            for output_dict_node_partition in output_dict_node_partitions:
                state = output_dict_node["state"]
                if state not in possible_node_states:
                    state = "unknown"

                node = {}
                node["partition"] = output_dict_node_partition
                node["hostname"] = output_dict_node["hostname"]
                node["cpus"] = output_dict_node["cpus"]
                node["alloc_cpus"] = output_dict_node["alloc_cpus"]
                node["memory"] = output_dict_node["real_memory"]
                node["alloc_memory"] = output_dict_node["alloc_memory"]
                node["state"] = state
                nodes.append(node)

        for node in nodes:
            tag_keys = {"partition": lambda x: f'"{x}"', "hostname":  lambda x: f'"{x}"', "state":  lambda x: f'"{x}"'}
            value_keys = {"cpus": lambda x: int(x), "alloc_cpus": lambda x: int(x), "memory": lambda x: int(x), "alloc_memory": lambda x: int(x)}
            tags, values = "", ""

            for k, v in node.items():
                if k in tag_keys:
                    tags += "{}={},".format(k, v)
                    values += "_{}={},".format(k, tag_keys[k](v))
                elif k in value_keys:
                    values += "{}={},".format(k, value_keys[k](v))
                else:
                    raise Exception("Unknown key: {}".format(k))
            
            tags = tags.rstrip(",")
            values = values.rstrip(",")

            line = "{},{} {} {}\n".format(check, tags, values, checktime)
            lineprotocol += line

print("start")
print(lineprotocol)
if lineprotocol:
    try:
         r = requests.post(url, data=lineprotocol)
    except requests.exceptions.ConnectionError:
        print("Database seems offline")
#  s
