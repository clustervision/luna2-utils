#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Setup file, will build the pip package for the project.
"""

__author__      = 'Sumit Sharma'
__copyright__   = 'Copyright 2022, Luna2 Project[UTILS]'
__license__     = 'GPL'
__version__     = '2.0'
__maintainer__  = 'Sumit Sharma'
__email__       = 'sumit.sharma@clustervision.com'
__status__      = 'Development'

from setuptools import setup, find_packages

PRE = "{Personal-Access-Token-Name}:{Personal-Access-Token}"

try: # for pip >= 10
    from pip._internal.req import parse_requirements
    install_requirements = list(parse_requirements('requirements.txt', session='hack'))
    requirements = [str(ir.requirement) for ir in install_requirements]
except ImportError: # for pip <= 9.0.3
    from pip.req import parse_requirements
    install_requirements = parse_requirements('requirements.txt', session='hack')
    requirements = [str(ir.req) for ir in install_requirements]


def new_version():
    """
    This Method will create a New version and update the Version file.
    """
    version = "0.0.0"
    with open('VERSION.txt', 'r', encoding='utf-8') as ver:
        version = ver.read()
    return version


setup(
	name = "luna2utils",
	version = new_version(),
	description = "Luna 2 Utils are the basic utilities",
	long_description = "Luna 2 Utils includes lchroot, lcluster, lpower, and slurm utilities.\
        With Luna 2 Utils, user can easily manage the power, cluster, etc. in Luna. ",
	author = "Sumit Sharma",
	author_email = "sumit.sharma@clustervision.com",
	maintainer = "Sumit Sharma",
	maintainer_email = "sumit.sharma@clustervision.com",
	url = "https://gitlab.taurusgroup.one/clustervision/luna2-utils.git",
	download_url = f"https://{PRE}@gitlab.taurusgroup.one/api/v4/projects/14/packages/pypi/simple",
	packages = find_packages(),
	license = "MIT",
	keywords = [
        "luna", "utils", "lchroot", "bootutil", "lcluster", "lpower", "slurm", "Trinity",
        "ClusterVision", "Sumit", "Sumit Sharma"
    ],
    scripts = [
        'usr/sbin/bootutil',
        'usr/sbin/lchroot',
        'usr/sbin/lcluster',
        'usr/sbin/lpower',
        'usr/sbin/slurm'
    ],
	install_requires = requirements,
	dependency_links = [],
	package_data = {
        "luna2web": [
            "*", "*.bootutil", "*.ini", "*.lchroot", "*.lcluster", "*.lpower", "*.slurm"]
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
