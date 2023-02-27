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


from health.utils import Utils
from health.config import category
# from nodestatus import NodeStatus

class Slurm(object):
    """
    Slurm Class will be helpful to retirve
    the sinfo and slurm state.
    """

    def __init__(self, node=None, statuses=None):

        self.node = node
        self.statuses = statuses

    def status(self):
        """
        This method will return the status
        of the Slurm.
        """
        self.answer = {
            'column': 'slurm',
            'status': 'UNKN',
            'category': category.UNKN,
            'history': [],
            'info': '',
            'details': ''
        }

        if (self.node is None
                or self.statuses is None
                or self.node not in self.statuses):
            return self.answer

        status = "/".join(self.statuses[self.node])
        self.answer['status'] = status

        idle_statuses = ["IDLE"]
        working_statuses = ["ALLOCATED", "ALLOCATED+", "COMPLETING", "MIXED", "RESERVED"]
        error_tags = ["*", "~", "#", "$", "@"]

        if status.upper() in idle_statuses:
            self.answer['category'] = category.GOOD

        if status.upper() in working_statuses:
            self.answer['category'] = category.BUSY

        if len(status) > 1 and status[-1] in error_tags:
            self.answer['category'] = category.ERROR

        return self.answer


    def get_sinfo(self):
        """
        Returns stdout for
        sinfo -N -o "%N %6T"
        """
        self.statuses = {}
        cmd = 'sinfo -N -o "%N %6T"'
        rc, stdout, _, _ = Utils().run_cmd(cmd)

        if rc:
            return self.statuses

        for line in stdout.split("\n"):
            line = line.split()
            if len(line) < 2:
                continue
            nodename = line[0]
            status = line[1]
            if nodename not in self.statuses:
                self.statuses[nodename] = set()
            self.statuses[nodename].add(status)

        return self.statuses
