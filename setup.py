#!/usr/bin/env python3

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


# -*- coding: utf-8 -*-

"""
Setup file, will build the pip package for the project.
"""

__author__      = 'Sumit Sharma'
__copyright__   = 'Copyright 2025, Luna2 Project[UTILS]'
__license__     = 'GPL'
__version__     = '2.1'
__maintainer__  = 'Sumit Sharma'
__email__       = 'sumit.sharma@clustervision.com'
__status__      = 'Development'

from time import time
from setuptools import setup, find_packages

PRE = "{Personal-Access-Token-Name}:{Personal-Access-Token}"

def get_requirements():
    """
    This Method will read the requirements.txt file and return the list of requirements.
    """

    # for pip <= 9.0.3
    try: 
        from pip.req import parse_requirements
        install_requirements = parse_requirements('requirements.txt', session='hack')
        return  [str(ir.req) for ir in install_requirements]
    except ImportError: 
        pass

    # for pip >= 10 AND pip <= 23.1
    try:
        from pip._internal.req import parse_requirements
        install_requirements = list(parse_requirements('requirements.txt', session='hack'))
        return [str(ir.requirement) for ir in install_requirements]
    except ImportError: 
        pass

    # anything else
    with open('requirements.txt', 'r', encoding='utf-8') as f:
        requirements = f.read().splitlines()
    return requirements



def new_version():
    """
    This Method will create a New version and update the Version file.
    """
    time_now = int(time())
    version = f'2.1.{time_now}'
    with open('utils/VERSION.txt', 'w', encoding='utf-8') as ver:
        ver.write(version)
    return version


setup(
    name = "luna2-utils",
    version = new_version(),
    description = "Luna 2 Utils are the basic utilities",
    long_description = "Luna 2 Utils includes lchroot, lcluster, lpower, and slurm utilities.\
        With Luna 2 Utils, user can easily manage the power, cluster, etc. in Luna. ",
    author = "ClusterVision development team",
    author_email = "support@clustervision.com",
    maintainer = "ClusterVision development team",
    maintainer_email = "support@clustervision.com",
    url = "https://gitlab.taurusgroup.one/clustervision/luna2-utils.git",
    download_url = f"https://{PRE}@gitlab.taurusgroup.one/api/v4/projects/14/packages/pypi/simple",
    # packages = find_packages(),
    packages = ['utils'],
    license = "MIT",
    keywords = [
        "luna", "utils", "lchroot", "bootutil", "lcluster", "lpower", "slurm", "Trinity",
        "ClusterVision", "Sumit", "Sumit Sharma"
    ],
    entry_points={
        'console_scripts': [
            'bootutil = utils:bootutil',
            'lchroot = utils.bash_runner:lchroot',
            'lpower = utils.lpower:main',
            'lcluster = utils.lcluster:main',
            'lslurm = utils.lslurm:main',
            'lnode = utils.lnode:main',
            'trix-diag = utils.trinity_diagnosis:main'
        ]
    },
    install_requires = get_requirements(),
    dependency_links = [],
    package_data = {
        "utils": ["*", "*.tclsh", "*.ini", "*.lchroot"]
    },
    data_files = [],
    zip_safe = False,
    include_package_data = True,
    classifiers = [
        'Development Status :: Beta',
        'Environment :: Luna 2 Command Line Utilities',
        'Intended Audience :: System Administrators',
        'License :: MIT',
        'Operating System :: RockyLinux :: CentOS :: RedHat',
        'Programming Language :: Python',
        'Topic :: Trinity :: Luna'
    ],
    platforms = [
        'RockyLinux',
        'CentOS',
        'RedHat'
    ]
)
# python setup.py sdist bdist_wheel
