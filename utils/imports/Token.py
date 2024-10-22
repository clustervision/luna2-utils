
import os, sys
import requests
from requests import Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry
import json
 
urllib3.disable_warnings()
session = Session()
retries = Retry(
    total = 10,
    backoff_factor = 0.3,
    status_forcelist = [502, 503, 504],
    allowed_methods = {'GET', 'POST'}
)
session.mount('https://', HTTPAdapter(max_retries=retries))

class Token:

    @classmethod
    def get_token(token, username, password, protocol, endpoint, verify_certificate=True):
        """
        This method will retrieve the token.
        """

        RET = {'401': 'invalid credentials', '400': 'bad request'}

        token_credentials = {'username': username, 'password': password}
        try:
            x = session.post(f'{protocol}://{endpoint}/token', json=token_credentials, stream=True, timeout=10, verify=verify_certificate)
            if (str(x.status_code) in RET):
                print("ERROR :: "+RET[str(x.status_code)])
                sys.exit(4)
            DATA = json.loads(x.text)
            if (not 'token' in DATA):
                print("ERROR :: i did not receive a token. i cannot continue.")
                sys.exit(5)
            return DATA["token"]
        except requests.exceptions.SSLError as ssl_loop_error:
            print(f'ERROR :: {ssl_loop_error}')
            sys.exit(3)
        except requests.exceptions.HTTPError as err:
            print("ERROR :: trouble getting my token: "+str(err))
            sys.exit(3)
        except requests.exceptions.ConnectionError as err:
            print("ERROR :: trouble getting my token: "+str(err))
            sys.exit(3)
        except requests.exceptions.Timeout as err:
            print("ERROR :: trouble getting my token: "+str(err))
            sys.exit(3)
    #    Below commented out as this catch all will also catch legit responses for e.g. 401 and 404
    #    except:
    #        print("ERROR :: trouble getting my token for unknown reasons.")
