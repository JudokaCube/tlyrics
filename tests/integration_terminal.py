"""Real PTY check: Escape returns promptly and arrow sequences remain intact."""
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''import curses,os
from tlyrics import setup_input
def run(screen):
    setup_input(screen)
    screen.timeout(-1)
    os.write(1,b"READY")
    up=screen.getch()
    esc=screen.getch()
    return up,esc
up,esc=curses.wrapper(run)
print("RESULT",up==curses.KEY_UP,esc==27,flush=True)
'''
master, slave = pty.openpty()
process = subprocess.Popen([sys.executable, '-c', CHILD], cwd=ROOT,
                           env=dict(os.environ, TERM='xterm-256color'),
                           stdin=slave, stdout=slave, stderr=slave)
os.close(slave)
output = b''
try:
    deadline = time.monotonic() + 5
    while b'READY' not in output:
        if time.monotonic() > deadline:
            raise AssertionError('Terminal did not start')
        if select.select([master], [], [], .1)[0]:
            output += os.read(master, 65536)
    started = time.monotonic()
    os.write(master, b'\x1bOA\x1b')
    while process.poll() is None:
        if time.monotonic() - started > 1:
            raise AssertionError('Escape still takes over one second')
        if select.select([master], [], [], .01)[0]:
            try:
                output += os.read(master, 65536)
            except OSError:
                break
    process.wait(timeout=1)
    elapsed = time.monotonic() - started
    assert b'RESULT True True' in output, output
    assert elapsed < .3, elapsed
    print(f'Terminal keys passed: arrow recognized, Escape completed in {elapsed * 1000:.0f} ms.')
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=2)
    os.close(master)
