"""Resize a real curses app, menu, chooser and prompt without losing input."""
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
import curses,os,threading,time,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tlyrics,settings,visualizer
class Screen:
    def __init__(self,screen): self.screen=screen; self.resized=False
    def __getattr__(self,key): return getattr(self.screen,key)
    def acknowledge(self):
        if self.resized:
            self.resized=False
            os.write(1,b"RESIZED")
    def getch(self):
        self.acknowledge()
        key=self.screen.getch()
        if key==curses.KEY_RESIZE: self.resized=True
        return key
    def get_wch(self):
        self.acknowledge()
        key=self.screen.get_wch()
        if key==curses.KEY_RESIZE: self.resized=True
        return key
class Players:
    changed=threading.Event()
    bridge=None
    def read(self):
        return [tlyrics.Track('limusic','Resize song','Artist','',60,2,'Paused',service='limusic')]
class Visualizer(visualizer.Visualizer):
    def configure(self,config):
        self.latest=tuple((i+1)/32 for i in range(32))
        self.stamp=time.monotonic()
def run(native):
    screen=Screen(native)
    tlyrics.setup_input(screen)
    curses.raw()
    config=settings.Preferences(dict(settings.DEFAULTS,update_mode='off',visualizer=True))
    os.write(1,b'STAGE_PROMPT')
    assert tlyrics.prompt(screen,'Input')=='abc'
    os.write(1,b'STAGE_CHOOSE')
    assert tlyrics.choose(screen,'Choice',['one','two'],'one')=='two'
    os.write(1,b'STAGE_MENU')
    tlyrics.menu(screen,config,[])
    os.write(1,b'STAGE_PLAYBACK')
    with tempfile.TemporaryDirectory() as folder, patch.object(visualizer,'Visualizer',Visualizer):
        path=Path(folder)/'test.lrc'
        path.write_text('[00:01]<00:01>Hello <00:03>world\\n[00:05]Next line')
        tlyrics.ui(screen,Players(),SimpleNamespace(lrc=str(path),settings=False),config)
curses.wrapper(run)
print('RESIZE_OK',flush=True)
'''
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
process = subprocess.Popen([sys.executable, '-c', CHILD], cwd=ROOT,
                           env=dict(os.environ, TERM='xterm-256color'),
                           stdin=slave, stdout=slave, stderr=slave)
os.close(slave)
output = b''


def wait_for(marker):
    global output
    deadline = time.monotonic() + 5
    while marker not in output:
        if time.monotonic() > deadline or process.poll() is not None:
            raise AssertionError(repr(output[-3000:]))
        if select.select([master], [], [], .05)[0]:
            output += os.read(master, 65536)
    output = output.split(marker, 1)[1]


try:
    for stage, finish in [('PROMPT', b'abc\r'), ('CHOOSE', b'\x1bOB\r'),
                          ('MENU', b'\x1b'), ('PLAYBACK', b'q')]:
        wait_for(('STAGE_' + stage).encode())
        for height, width in [(18, 37), (5, 10), (14, 24), (24, 81), (40, 143), (30, 100)]:
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
            os.kill(process.pid, signal.SIGWINCH)
            wait_for(b'RESIZED')
        os.write(master, finish)
    wait_for(b'RESIZE_OK')
    assert process.wait(timeout=2) == 0
    print('Real terminal resize passed: prompt, chooser, settings and playback; 24 size changes.')
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=2)
    os.close(master)
