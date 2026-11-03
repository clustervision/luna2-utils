#!/usr/bin/env python3
"""Standalone check for lconsole's SOL recovery (v4.0, SOL-only)
(run: python3 tests/test_sol_retry.py).

Pins: a dead SOL is restarted on the sol_fail_grace cadence, a failed restart
re-arms and retries (unbounded), retry chatter goes quiet after two printed
attempts, and ipmitool's alarming teardown lines are rewritten to calm
wording.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.lconsole as lc

lc.time.sleep = lambda s: None          # no real jitter waits
lc.random.uniform = lambda a, b: 0.0

DETAILS = {'boot_ip': '10.0.0.2', 'bmc_ip': '10.9.0.2',
           'bmcsetup': {'username': 'u', 'password': 'p'}}


class FakeSol:
    def __init__(self, alive=True):
        self._alive = alive
    def start(self, winsize=None): pass
    def stop(self): pass
    def is_alive(self): return self._alive
    def exit_summary(self): return 'IPMI SOL exited (code 1)'
    def fileno(self): return -1
    def read(self): return b''


def make_app():
    app = lc.LConsole('node002', DETAILS)
    app.messages = []
    app._message = lambda m, _l=app.messages: _l.append(m)
    return app


def test():
    # 1. dead SOL -> announced, then rebuilt once the grace period elapses
    app = make_app()
    app.sol = FakeSol(alive=False)
    built = []
    app._build_sol = lambda: built.append(1) or FakeSol(alive=True)
    app._maybe_recover_sol()                   # first pass: arms sol_gone_at
    assert app.sol_gone_at is not None and not built
    assert any('restarting' in m for m in app.messages), app.messages
    app.sol_gone_at -= app.sol_fail_grace + 1  # grace elapsed
    app._maybe_recover_sol()
    assert built, 'dead SOL was not restarted'
    assert app.sol.is_alive()

    # 2. a healthy session clears the countdown instead of restarting
    app = make_app()
    app.sol = FakeSol(alive=True)
    app.sol_gone_at = time.monotonic() - 999
    app._build_sol = lambda: (_ for _ in ()).throw(AssertionError('must not rebuild'))
    app._maybe_recover_sol()
    assert app.sol_gone_at is None

    # 3. restart failure leaves sol None -> re-arms and retries, unbounded
    app = make_app()
    app.sol = None
    calls = []
    def boom():
        calls.append(1)
        raise RuntimeError('bmc dead')
    app._build_sol = boom
    for expected in (1, 2, 3):
        app._maybe_recover_sol()               # arms sol_gone_at
        app.sol_gone_at = time.monotonic() - app.sol_fail_grace - 1
        app._maybe_recover_sol()
        assert len(calls) == expected, (expected, calls)
    assert app.sol is None

    # 4. quiet after 2: printed retry lines stop, reconnected line still prints
    app = make_app()
    app.sol = None
    app._build_sol = lambda: FakeSol(alive=True)
    for _ in range(4):
        app._restart_sol('SOL is down')
    retry_prints = [m for m in app.messages if 'reconnecting SOL' in m]
    quiet_notice = [m for m in app.messages if 'retrying quietly' in m]
    ok_prints    = [m for m in app.messages if m == 'SOL reconnected.']
    assert len(retry_prints) == 2, retry_prints
    assert len(quiet_notice) == 1, quiet_notice
    assert len(ok_prints) == 4
    assert app._sol_reconnect_count == 4

    # 5b. post-reconnect probe: a "reconnected" session that delivers no
    # output within SOL_EXPECT_OUTPUT_SECS is reconnected again on its own,
    # bounded by MAX_SILENT_RECONNECTS; output would clear the probe
    app = make_app()
    app.sol = None
    app._build_sol = lambda: FakeSol(alive=True)
    app._restart_sol('SOL is down')            # arms the probe deadline
    assert app._sol_probe_deadline is not None
    rebuilds = []
    app._build_sol = lambda: rebuilds.append(1) or FakeSol(alive=True)
    for expected in range(1, lc.MAX_SILENT_RECONNECTS + 1):
        app._sol_probe_deadline = time.monotonic() - 1   # window expired, still silent
        app._maybe_recover_sol()
        assert len(rebuilds) == expected, (expected, rebuilds)
    app._sol_probe_deadline = time.monotonic() - 1       # budget exhausted
    app._maybe_recover_sol()
    assert len(rebuilds) == lc.MAX_SILENT_RECONNECTS
    assert any('idle console' in m for m in app.messages), app.messages

    # 5. NOISE rewrite on the ipmitool backend
    b = lc.IpmiSolBackend('node002', '10.9.0.2', DETAILS['bmcsetup'])
    b._banner_pending = False
    raw = (b'boot...\r\n'
           b'Error: No response to keepalive - Terminating session\r\n'
           b'Error: No response de-activating SOL payload\r\n')
    chunks = [raw]
    b._master_fd = 0
    orig_read = os.read
    os.read = lambda fd, n: chunks.pop() if chunks else (_ for _ in ()).throw(BlockingIOError())
    try:
        out = b.read()
    finally:
        os.read = orig_read
    assert b'No response to keepalive' not in out, out
    assert b'normal during a reboot' in out, out
    assert b'de-activating' not in out, out

    print('OK: SOL-only recovery — dead restart, healthy clears countdown, '
          'unbounded rearm, quiet-after, NOISE rewrite')


if __name__ == '__main__':
    test()
