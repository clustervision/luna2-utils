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


available_checks = {
    "health":   "Health",
    "ipmi":     "IPMI",
    "slurm":    "SLURM",
    "luna":     "Luna",
    "zabbix":   "Zabbix",
}

config_file = "/etc/trinity/trix-status.conf"

default_service_list = [
    'named', 'dhcpd', 'chronyd', 'sshd', 'fail2ban', 'firewalld', 'nginx',
    'lweb', 'ltorrent', 'mariadb', 'mongod', 'nfs', 'slapd', 'zabbix-server',
    'zabbix-agent', 'sssd', 'slurmctld', 'munge', 'rsyslog', 'slurmdbd',
    'snmptrapd'
]


class category:
    # red
    UNKN    = 0
    DOWN    = 1
    ERROR   = 2
    # yellow
    WARN    = 3
    BUSY    = 4
    # green
    GOOD    = 5
    PASSIVE = 6

class colors:
    NONE            = None
    RED             = "\033[31m"
    LIGHTRED        = "\033[91m"
    YELLOW          = "\033[33m"
    LIGHTYELLOW     = "\033[93m"
    CYAN            = "\033[36m"
    LIGHTCYAN       = "\033[96m"
    GREEN           = "\033[32m"
    LIGHTGREEN      = "\033[92m"
    DEFAULT         = "\033[39m"
    BGLIGHGRAY      = "\033[47m"
    BGBLACK         = "\033[40m"
    BGDEFAULT       = "\033[49m"

default_color_mapping = {
    category.UNKN:      'ERR',
    category.DOWN:      'ERR',
    category.ERROR:     'ERR',
    category.WARN:      'WARN',
    category.BUSY:      'WARN',
    category.GOOD:      'GOOD',
    category.PASSIVE:   'PASSIVE',
}

default_color_scheme = {
    'ERR':      colors.RED,
    'WARN':     colors.YELLOW,
    'GOOD':     colors.GREEN,
    'PASSIVE':  colors.CYAN,
}


