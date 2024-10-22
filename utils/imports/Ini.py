
import os, sys
from configparser import RawConfigParser

class Ini:
    """
    This Log Class is responsible for reading and parsing the luna.ini file.
    """
    __logger = None

    @classmethod
    def get_option(ini, parser=None, errors=None,  section=None, option=None):
        """
        This method will retrieve the value from the INI
        """
        response = False
        if parser.has_option(section, option):
            response = parser.get(section, option)
        else:
            errors.append(f'{option} is not found in section {section}')
        return response, errors


    @classmethod
    def read_ini(ini, ini_file=None):
        CONF, errors = {}, []
        username, password, daemon, secret_key, protocol, security = None, None, None, None, None, ''
        file_check = os.path.isfile(ini_file)
        read_check = os.access(ini_file, os.R_OK)
        if file_check and read_check:
            configparser = RawConfigParser()
            configparser.read(ini_file)
            if configparser.has_section('API'):
                for item in ['username','password','protocol','endpoint']:
                    CONF[item.upper()], errors = ini.get_option(configparser, errors,  'API', item.upper())
                secret_key, _ = ini.get_option(configparser, errors,  'API', 'SECRET_KEY')
                security, _ = ini.get_option(configparser, errors,  'API', 'VERIFY_CERTIFICATE')
                CONF["VERIFY_CERTIFICATE"] = True if security.lower() in ['y', 'yes', 'true']  else False
            else:
                errors.append(f'API section is not found in {ini_file}.')
        else:
            errors.append(f'{ini_file} is not found on this machine.')
        if errors:
            sys.stderr.write('You need to fix following errors...\n')
            num = 1
            for error in errors:
                sys.stderr.write(f'{num}. {error}\n')
            sys.exit(1)
        return CONF
