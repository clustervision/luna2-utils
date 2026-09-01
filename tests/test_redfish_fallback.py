"""Pins the Redfish capability probe: a BMC whose Redfish declares IPMI as its
only serial console transport (AMI MegaRAC / GIGABYTE R181 class) must raise
RedfishConsoleIsIpmi — the signal lconsole uses to fall back to IPMI SOL —
instead of the generic 'firmware may not support' error.

Run directly: python3 tests/test_redfish_fallback.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.lconsole import RedfishSolBackend, RedfishConsoleIsIpmi


class MegaRacFake(RedfishSolBackend):
    """Serves the responses read off node002's BMC: Systems/Self exists but has
    no SerialConsole.SSH; Managers/Self carries ConnectTypesSupported=[IPMI]."""

    def _redfish_get(self, path):
        if path == '/redfish/v1/Systems/1':
            import requests
            resp = type('R', (), {'status_code': 404})()
            raise requests.HTTPError(response=resp)
        if path.startswith('/redfish/v1/Systems/'):
            return {'Id': 'Self'}  # no SerialConsole at all on the System
        if path == '/redfish/v1/Managers/Self':
            return {'SerialConsole': {'ServiceEnabled': True,
                                      'MaxConcurrentSessions': 1,
                                      'ConnectTypesSupported': ['IPMI']}}
        import requests
        resp = type('R', (), {'status_code': 404})()
        raise requests.HTTPError(response=resp)


class SshCapableFake(RedfishSolBackend):
    """An OpenBMC-style box: SerialConsole.SSH present and enabled."""

    def _redfish_get(self, path):
        return {'SerialConsole': {'SSH': {'ServiceEnabled': True, 'Port': 2200}}}


class ExoticIdFake(RedfishSolBackend):
    """A vendor whose System id matches no guessed path — only the collection
    walk can find it. Pins the hybrid discovery."""

    def _redfish_get(self, path):
        if path == '/redfish/v1/Systems':
            return {'Members': [{'@odata.id': '/redfish/v1/Systems/XZ99-0001'}]}
        if path == '/redfish/v1/Systems/XZ99-0001':
            return {'SerialConsole': {'SSH': {'ServiceEnabled': True, 'Port': 2222}}}
        import requests
        resp = type('R', (), {'status_code': 404})()
        raise requests.HTTPError(response=resp)


bmcsetup = {'username': 'admin', 'password': 'x'}

# MegaRAC: must raise the dedicated fallback signal, not the generic error
b = MegaRacFake('node002', '10.148.0.2', bmcsetup)
try:
    b._discover_ssh_port()
    raise AssertionError('expected RedfishConsoleIsIpmi')
except RedfishConsoleIsIpmi as exc:
    assert 'IPMI' in str(exc)

# SSH-capable: discovery returns the port, no fallback
s = SshCapableFake('nodeX', '10.148.0.9', bmcsetup)
assert s._discover_ssh_port() == 2200

# exotic-id BMC: found via the Systems collection walk, no guessing involved
e = ExoticIdFake('nodeY', '10.148.0.10', bmcsetup)
assert e._discover_system_path() == '/redfish/v1/Systems/XZ99-0001'
assert e._discover_ssh_port() == 2222

# manual override always wins
o = ExoticIdFake('nodeY', '10.148.0.10', bmcsetup, system_path='/redfish/v1/Systems/Forced')
assert o._discover_system_path() == '/redfish/v1/Systems/Forced'

# credentials: a redfishsetup account wins over bmcsetup; endpoint honours
# scheme/port/verify; absence of redfishsetup falls back to bmcsetup untouched
rf = {'scheme': 'https', 'port': 8443, 'verify': True,
      'accounts': [{'name': 'hw', 'username': 'operator', 'password': 'secret',
                    'role': 'Operator'}]}
c = SshCapableFake('nodeZ', '10.148.0.11', bmcsetup, redfishsetup=rf)
assert (c._rf_user, c._rf_pass) == ('operator', 'secret')
assert c._rf_base == 'https://10.148.0.11:8443' and c._rf_verify is True

d = SshCapableFake('nodeZ', '10.148.0.11', bmcsetup)
assert (d._rf_user, d._rf_pass) == ('admin', 'x')
assert d._rf_base == 'https://10.148.0.11' and d._rf_verify is False

print('OK: Redfish capability probe — IPMI-only BMC signals fallback, SSH BMC discovers port')
