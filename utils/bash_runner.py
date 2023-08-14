#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
lcluster Utility for Trinity Project
"""
__author__      = "Sumit Sharma"
__copyright__   = "Copyright 2022, Luna2 Project [UTILITY]"
__license__     = "GPL"
__version__     = "2.0"
__maintainer__  = "Sumit Sharma"
__email__       = "sumit.sharma@clustervision.com"
__status__      = "Development"


import os
import subprocess

def _run(bash_script):
    """
    This method will run the lchroot.
    """
    return subprocess.call(bash_script, shell=True)

def lchroot():
    """
    This method will pass the path of lchroot for pip installation.
    """
    dir_path = os.path.dirname(os.path.realpath(__file__))
    lchroot = f'sh {dir_path}/lchroot'
    return _run(lchroot)
