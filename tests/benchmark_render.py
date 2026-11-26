"""Synthetic rendering benchmark; terminal I/O is excluded."""
import sys,time,statistics
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
import rendering,lyrics,settings
class Screen:
    calls=0
    def getmaxyx(self): return 40,140
    def addstr(self,*args): self.calls+=1
screen=Screen()
doc=lyrics.Document([lyrics.Line(i*2,' '.join(['quick lyrics']*10),words=[lyrics.Word('quick lyrics ',i*2+j*.1,i*2+(j+1)*.1) for j in range(20)]) for i in range(200)])
config=dict(settings.DEFAULTS,gradient='line',glow='soft',animate_gradient=True)
samples=[]
with patch('curses.has_colors',return_value=True),patch('curses.COLORS',256,create=True),patch('curses.COLOR_PAIRS',256,create=True),patch('curses.color_pair',side_effect=lambda n:n<<8):
    for i in range(400):
        start=time.perf_counter();rendering.draw(screen,doc,100+i/60,config);samples.append((time.perf_counter()-start)*1000)
print('median_ms',round(statistics.median(samples),3),'p95_ms',round(sorted(samples)[379],3),'draw_calls_per_frame',screen.calls/400)
