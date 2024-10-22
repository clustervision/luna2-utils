
import os, sys
import logging

class Log:
    """
    This Log Class is responsible to start the Logger depend on the Level.
    """
    __logger = None

    @classmethod
    def init_log(cls, log_file=None, log_level=None, log_name=None):
        """
        Input - log_level
        Process - Validate the Log Level, Set it to INFO if not correct.
        Output - Logger Object.
        """
        if not log_file:
            sys.stderr.write('ERROR :: log file not defined.\n')
            sys.exit(1)
        log_path=os.path.dirname(log_file)
        if not os.path.exists(log_path):
            try:
                os.makedirs(log_path)
            except PermissionError:
                sys.stderr.write('ERROR :: no permission to write to log file.\n')
        if not log_name:
            log_name, *_=os.path.basename(log_file).split('.')

        levels = {'NOTSET': 0, 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
        log_level = levels[log_level.upper()]
        thread_level = '[%(levelname)s]:[%(asctime)s]:[%(threadName)s]:'
        message = '[%(filename)s:%(funcName)s@%(lineno)d] - %(message)s'
        log_format = f'{thread_level}{message}'
        try:
            logging.basicConfig(filename=log_file, format=log_format, filemode='a', level=log_level)
            cls.__logger = logging.getLogger(log_name)
            cls.__logger.setLevel(log_level)
            if log_level == 10:
                formatter = logging.Formatter(log_format)
                console = logging.StreamHandler(sys.stdout)
                console.setLevel(log_level)
                console.setFormatter(formatter)
                cls.__logger.addHandler(console)
            levels = {0:'NOTSET', 10: 'DEBUG', 20: 'INFO', 30: 'WARNING', 40:'ERROR', 50:'CRITICAL'}
            # cls.__logger.info(f'####### Luna Logging Level IsSet To [{levels[log_level]}] ########')
            return cls.__logger
        except PermissionError:
            sys.stderr.write('ERROR :: Run this tool as a super user.\n')
            sys.exit(1)


    @classmethod
    def get_logger(cls):
        """
        Input - None
        Output - Logger Object.
        """
        return cls.__logger


    @classmethod
    def set_logger(cls, log_level=None):
        """
        Input - None
        Process - Update the existing Log Level
        Output - Logger Object.
        """
        levels = {'NOTSET': 0, 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
        log_level = levels[log_level.upper()]
        cls.__logger.setLevel(log_level)
        return cls.__logger


    @classmethod
    def check_loglevel(cls):
        """
        Input - None
        Process - Update the existing Log Level
        Output - Logger Object.
        """
        return logging.root.level

