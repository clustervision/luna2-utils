#!/usr/bin/env python3
"""Self-driving harness: runs lconsole attached to a real PTY, injects
periodic keystrokes, and logs everything with timestamps so a human never
has to sit at the keyboard to see whether SOL is alive and interactive."""
import os, pty, select, subprocess, sys, time, fcntl, struct, termios

NODE = sys.argv[1] if len(sys.argv) > 1 else 'node002'
MODE_ARGS = sys.argv[2:] if len(sys.argv) > 2 else ['--sol-only']
LOGFILE = f'/tmp/self_drive_{NODE}.log'
DURATION = 900          # seconds to run
KEYSTROKE_INTERVAL = 15  # send a lone Enter this often, to test interactivity

master_fd, slave_fd = pty.openpty()
# give the slave a normal-ish window size
fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))

log = open(LOGFILE, 'wb', buffering=0)
def stamp(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}\n'.encode()
    log.write(line)
    sys.stderr.buffer.write(line)
    sys.stderr.flush()

cmd = ['lconsole', NODE] + MODE_ARGS
stamp(f'launching: {" ".join(cmd)}')
proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                        close_fds=True, start_new_session=True)
os.close(slave_fd)
flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

start = time.monotonic()
last_keystroke = start
total_bytes = 0
try:
    while time.monotonic() - start < DURATION:
        if proc.poll() is not None:
            stamp(f'lconsole process exited with code {proc.returncode}')
            break
        now = time.monotonic()
        if now - last_keystroke >= KEYSTROKE_INTERVAL:
            last_keystroke = now
            try:
                os.write(master_fd, b'\r')
                stamp('>>> sent keystroke: Enter')
            except OSError as e:
                stamp(f'>>> FAILED to send keystroke: {e}')
        r, _, _ = select.select([master_fd], [], [], 1.0)
        if master_fd in r:
            try:
                chunk = os.read(master_fd, 65536)
            except OSError as e:
                stamp(f'read error: {e}')
                break
            if not chunk:
                stamp('EOF on master fd')
                break
            total_bytes += len(chunk)
            log.write(chunk)
except KeyboardInterrupt:
    pass
finally:
    stamp(f'harness done. total_bytes_seen={total_bytes}')
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    log.close()
