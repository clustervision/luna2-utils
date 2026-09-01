#!/usr/bin/env python3
"""Standalone check for lconsole's EscapeFilter (run: python3 tests/test_escape_filter.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.lconsole import EscapeFilter, CTRL_C


def feed(f, data, interactive=True):
    return f.feed(data, interactive=interactive)


def test():
    # plain exit sequence at line start
    f = EscapeFilter(b'!')
    out, ex = feed(f, b'!..')
    assert ex and out == b'', (out, ex)

    # escape char mid-line is forwarded, no exit
    f = EscapeFilter(b'!')
    out, ex = feed(f, b'x!..')
    assert not ex and out == b'x!..', (out, ex)

    # THE old bug: aborting an escape with Enter must re-arm line start
    f = EscapeFilter(b'!')
    out, ex = feed(f, b'!\r')
    assert not ex and out == b'!\r', (out, ex)
    out, ex = feed(f, b'!..')
    assert ex, 'escape after aborted !<Enter> must still exit'

    # aborted sequence flushes swallowed bytes
    f = EscapeFilter(b'!')
    out, ex = feed(f, b'!.a')
    assert not ex and out == b'!.a', (out, ex)

    # split across reads (one byte at a time)
    f = EscapeFilter(b'!')
    for b in (b'!', b'.'):
        out, ex = feed(f, b)
        assert not ex and out == b''
    out, ex = feed(f, b'.')
    assert ex

    # Ctrl-C: non-interactive exits immediately
    f = EscapeFilter(b'!')
    out, ex = feed(f, CTRL_C, interactive=False)
    assert ex

    # Ctrl-C: interactive forwards first, exits on quick second
    f = EscapeFilter(b'!')
    out, ex = feed(f, CTRL_C, interactive=True)
    assert not ex and out == CTRL_C and f.notice
    out, ex = feed(f, CTRL_C, interactive=True)
    assert ex

    # exit works after multi-line traffic
    f = EscapeFilter(b'!')
    out, ex = feed(f, b'ls -l\rcat foo\r')
    assert not ex and out == b'ls -l\rcat foo\r'
    out, ex = feed(f, b'!..')
    assert ex

    print('OK: EscapeFilter')


if __name__ == '__main__':
    test()
