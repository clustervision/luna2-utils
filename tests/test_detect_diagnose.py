"""Pins the pure parsing/analysis halves of --detect-port and --diagnose.

Run directly: python3 tests/test_detect_diagnose.py
The SSH/ipmitool halves are exercised live on the cluster, not here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.lconsole import (parse_spcr, parse_serial_driver,
                            analyze_kerneloptions, suggested_kerneloptions,
                            UART_ADDRESSES)


def fake_spcr(addr):
    raw = bytearray(80)
    raw[0:4] = b'SPCR'
    raw[44:52] = addr.to_bytes(8, 'little')
    return bytes(raw)


# --- SPCR ------------------------------------------------------------------
assert parse_spcr(fake_spcr(0x2F8)) == 0x2F8
assert parse_spcr(fake_spcr(0x3F8)) == 0x3F8
assert parse_spcr(b'short') is None
assert UART_ADDRESSES[0x2F8] == 'ttyS1'

# --- /proc/tty/driver/serial (real capture from a GIGABYTE R181 node) -------
SERIAL_TEXT = """serinfo:1.0 driver revision:
0: uart:16550A port:000003F8 irq:4 tx:0 rx:0
1: uart:16550A port:000002F8 irq:3 tx:1199907 rx:1171973 oe:227 bo:314571 RTS|CTS|DTR|DSR|CD
2: uart:unknown port:000003E8 irq:4
3: uart:unknown port:000002E8 irq:3
"""
ports = parse_serial_driver(SERIAL_TEXT)
assert ports == {'ttyS0': (0x3F8, 0), 'ttyS1': (0x2F8, 1199907)}, ports

# --- kernel options analysis ------------------------------------------------
# the quintuple hedge: last console= steals /dev/console, two named ttyS
hedge = ('earlycon=uart8250,io,0x3f8,115200n8 console=tty0 '
         'console=uart,io,0x2f8,115200n8 console=ttyS0,115200n8 '
         'console=ttyS1,115200n8')
a = analyze_kerneloptions(hedge)
assert a['dev_console'] == 'ttyS1'
assert a['named'] == ['ttyS0', 'ttyS1']
assert a['baud'] == 115200
assert any('more than one named' in w for w in a['warnings']), a['warnings']

# the collapsed, correct form: no warnings at all
good = suggested_kerneloptions('ttyS1', 0x2F8)
# baud parameter flows into every field of the suggestion
alt = suggested_kerneloptions('ttyS0', 0x3F8, baud=57600)
assert alt.count('57600') == 3 and '115200' not in alt and 'ttyS0' in alt
b = analyze_kerneloptions(good)
assert b['dev_console'] == 'ttyS1'
assert b['warnings'] == [], b['warnings']
assert b['baud'] == 115200

# the failure shapes seen in the field: invalid earlycon spec, missing baud
bad = analyze_kerneloptions('earlycon=ttyS1 console=tty0 console=ttyS1')
assert any('not a valid earlycon' in w for w in bad['warnings']), bad['warnings']
assert any('no baud' in w for w in bad['warnings']), bad['warnings']
assert not any('not a valid earlycon' in w
               for w in analyze_kerneloptions(
                   'earlycon=uart8250,io,0x2f8,115200n8 console=ttyS1,115200n8')['warnings'])

# no serial console at all -> loud warning
c = analyze_kerneloptions('quiet splash console=tty0')
assert any('no serial console' in w for w in c['warnings'])

# missing earlycon -> flagged (early crashes look like a dead node)
d = analyze_kerneloptions('console=tty0 console=ttyS1,115200n8')
assert any('no earlycon' in w for w in d['warnings'])

print('OK: detect/diagnose parsing — SPCR, serial-driver map, kerneloptions analysis')
