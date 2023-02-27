'''
Created by ClusterVision <infonl@clustervision.com>
This file is part of trix-status tool
https://github.com/clustervision/trix-status
trix-status is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
trix-status is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
You should have received a copy of the GNU General Public License
along with slurm_health_checker.  If not, see <http://www.gnu.org/licenses/>.
'''


import subprocess as sp
from multiprocessing.pool import ThreadPool
import socket
import time
import hostlist

from health.config import category
# from utils import get_config
from health.utils import Utils


class HealthStatus(object):
    """
    Check Health of a Node
    """

    def __init__(self, node=None):
        """
        Constructor - Before calling any REST API
        it will fetch the credentials and endpoint url
        from luna.ini from Luna 2 Daemon.
        """
        self.timeout = 10
        self.node = node
        # self.cmd = Utils().run_cmd()

    def check_resolv(self):
        """
        Check Hostname resolv
        """
        # self.tagged_log_debug("Check if we can resolve hostname")
        self.answer['history'].append('resolve')
        rc, stdout, stdout_lines, stderr = Utils().run_cmd(f"host -W {self.timeout} {self.node}")
        # self.tagged_log_debug("Check resolve rc = {}".format(rc))
        if rc:
            self.answer['details'] = stdout
        return not rc


    def check_ping(self):
        """
        Check Machine Ping
        """
        # self.tagged_log_debug("Check if node is pingable")
        self.answer['history'].append('ping')
        rc, stdout, stdout_lines, stderr = Utils().run_cmd(f"ping -c1 -w{self.timeout} {self.node}")
        # self.tagged_log_debug("Check ping rc = {}".format(rc))
        if rc:
            if len(stdout_lines) > 1:
                self.answer['details'] = stdout_lines[-2]
        return not rc

    def check_ssh_port(self):
        """
        Check Machine SSH Port
        """
        # self.tagged_log_debug("Check if ssh port is open")
        self.answer['history'].append('ssh port')
        try:
            rc = 1
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            rc = sock.connect_ex((self.node, 22))
        except Exception as e:
            msg = (f"Exception on checking if ssh port is open on node: '{e}'")
            # self.tagged_log_debug(msg)
            rc = 1
        finally:
            sock.close()
        if rc:
            self.answer['details'] = "Port 22 is closed"
        # self.tagged_log_debug("Check ssh port rc = {}".format(rc))
        return not rc

    def check_ssh(self):
        """
        Check Machine SSH
        """
        # self.tagged_log_debug("Check if node available via ssh")
        self.answer['history'].append('ssh')
        rc, stdout, stdout_lines, stderr = Utils().run_cmd((f"ssh -o ConnectTimeout={self.timeout} -o StrictHostKeyChecking=no {self.node} uname"))
        # self.tagged_log_debug("Check ssh rc = {}".format(rc))
        return not rc

    def _discover_mountpoints(self):
        """
        Check Machine Mount Points
        """
        # get mountpoints
        cmd = ("ssh -o ConnectTimeout={self.timeout} -o StrictHostKeyChecking=no {self.node} " + "systemctl --type mount --all --no-legend")
        rc, stdout, stdout_lines, stderr = Utils().run_cmd(cmd)
        if rc:
            return []
        fs_mounts = []
        standart_mounts = ['-.mount', 'run-user-0.mount']
        for line in stdout_lines:
            line = line.split()
            fs, unit_name = line[4], line[0]
            if fs[0] == '/' and unit_name not in standart_mounts:
                fs_mounts.append(fs)
        return fs_mounts


    def _get_mountfs_from_confg(self):
        """
        Check Machine Mount Points from Config File, should be remove
        """
        config = get_config('health', {'mounts': []})
        fs_mounts = []
        config = config['mounts']
        for hostexpr, mps in config.items():
            hosts = hostlist.expand_hostlist(hostexpr)
            if self.node in hosts:
                fs_mounts.extend(mps)
        return fs_mounts


    def check_mounts(self):
        """
        Check Machine Mount Points
        """
        # self.tagged_log_debug("Check if node have healthy mountpoints")
        self.answer['history'].append('mounts')
        try:
            fs_mounts = self._get_mountfs_from_confg()
        except Exception:
            fs_mounts = []
        if not fs_mounts:
            fs_mounts = self._discover_mountpoints()
            # self.tagged_log_debug("Discovered mounts: {}".format(fs_mounts))
        if not fs_mounts:
            return False
        thread_pool = ThreadPool(10)
        # self.tagged_log_debug("Map check_mount workers to threads")
        workers_return = thread_pool.map(
            self.mount_worker,
            fs_mounts
        )
        # self.tagged_log_debug("Returned from mount workers: '{}'".format(workers_return) )
        broken_fs = []
        error = False
        for fs, details, status_ok in workers_return:
            if not status_ok:
                error = True
                broken_fs.append(fs)
        if broken_fs:
            self.answer['details'] = "FAIL:" + ",".join(broken_fs)
        return not error


    def mount_worker(self, fs):
        """
        Check Machine Mount works
        """
        ssh_cmd = (f"ssh -o ConnectTimeout={self.timeout} -o StrictHostKeyChecking=no {self.node} ")
        # self.tagged_log_debug("Mount worker is spawned for {}".format(fs))
        # check if fs mounted
        cmd = ssh_cmd + "cat /proc/mounts | grep -q '{}'".format(fs)
        rc, stdout, stdout_lines, stderr = Utils().run_cmd(cmd)
        if rc:
            return (fs, 'Not mounted', False)
        cmd = ssh_cmd + "stat -t {}".format(fs)
        # self.tagged_log_debug("Stat cmd: '{}'".format(cmd))
        stat_proc = sp.Popen(
            cmd, shell=True, stdout=sp.PIPE, stderr=sp.PIPE)
        i = 0
        while True:
            if stat_proc.poll() is None:
                time.sleep(1)
            else:
                break
            i += 1
            if i > self.timeout:
                stat_proc.kill()
                msg = 'Stat timeout for {}'.format(fs)
                # self.tagged_log_debug(msg)
                return (fs, 'Stat timeout', False)
        stdout, stderr = stat_proc.communicate()
        if stat_proc.returncode != 0:
            msg = f'Stat for {fs} returned non-zero code: { stat_proc.returncode}'
            # self.tagged_log_debug(msg)
            return (fs, "Stat rc = {}".format(stat_proc.returncode), False)
        return (fs, "", True)


    def status(self):
        """
        Check Machine State
        """
        # self.tagged_log_debug("Health checker started")
        self.answer = {
            'column': 'health',
            'status': 'UNKN',
            'category': category.UNKN,
            'history': [],
            'info': '',
            'details': ''
        }

        if not self.check_resolv():
            self.answer['info'] = self.answer['history'][-1]
            return self.answer
        self.answer['status'] = 'DOWN'
        if not self.check_ping():
            self.answer['info'] = self.answer['history'][-1]
            return self.answer
        if not self.check_ssh_port():
            self.answer['info'] = self.answer['history'][-1]
            return self.answer
        # self.answer['category'] = category.DOWN
        if not self.check_ssh():
            self.answer['info'] = self.answer['history'][-1]
            return self.answer
        self.answer['status'] = 'AVAIL'
        # self.answer['category'] = category.WARN
        if not self.check_mounts():
            self.answer['info'] = self.answer['history'][-1]
            self.answer['status'] = 'NO_FS'
            return self.answer
        self.answer['status'] = 'OK'
        # self.answer['category'] = category.GOOD
        return self.answer
