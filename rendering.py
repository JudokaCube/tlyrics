"""Cell-aware lyrics rendering and optional curses colour effects."""
import curses
import math
import unicodedata
from functools import lru_cache
from lyrics import word_states

PALETTES = {
    'ocean': [31, 38, 45, 81, 117, 153, 159, 195],
    'sunset': [196, 202, 208, 214, 220, 213, 207, 201],
    'aurora': [57, 63, 69, 75, 81, 87, 86, 85],
    'rose': [125, 161, 168, 175, 182, 189, 225, 231],
    'forest': [22, 28, 34, 40, 76, 112, 150, 194],
    'ice': [24, 31, 38, 74, 110, 153, 195, 231],
    'rainbow': [196, 208, 226, 46, 51, 33, 93, 201],
}


@lru_cache(maxsize=4096)
def width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1


@lru_cache(maxsize=2048)
def safe_text(text):
    return ''.join(char for char in text if char.isprintable())


def clip_text(text, columns, tail=False):
    """Clip by terminal cells, retaining combining marks with their base."""
    clusters = []
    for char in safe_text(str(text)):
        if width(char) == 0:
            if clusters:
                clusters[-1] += char
        else:
            clusters.append(char)
    result, used = [], 0
    for cluster in reversed(clusters) if tail else clusters:
        size = width(cluster[0])
        if used + size > max(0, columns):
            break
        result.append(cluster)
        used += size
    return ''.join(reversed(result) if tail else result)


def configure(config):
    if not curses.has_colors():
        return curses.A_BOLD
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    if config.get('background', 'terminal') != 'terminal':
        background = {'black': 0, 'midnight': 17, 'charcoal': 235}[config['background']]
        if curses.COLORS < 256:
            background = curses.COLOR_BLACK
    colors = dict(blue=curses.COLOR_BLUE, red=curses.COLOR_RED, pink=curses.COLOR_MAGENTA,
                  teal=curses.COLOR_CYAN, cyan=curses.COLOR_CYAN, green=curses.COLOR_GREEN, purple=curses.COLOR_MAGENTA,
                  amber=curses.COLOR_YELLOW, white=curses.COLOR_WHITE)
    if curses.COLORS >= 256:
        colors.update(pink=211, teal=43, blue=75, red=203, green=84, amber=215,
                      purple=141, cyan=81, white=255)
    curses.init_pair(1, colors[config['theme']], background)
    curses.init_pair(2, -1 if background == -1 else curses.COLOR_WHITE, background)
    if curses.COLORS >= 256 and curses.COLOR_PAIRS >= 50:
        for index, color in enumerate(PALETTES[config['palette']]):
            curses.init_pair(16 + index, color, background)
            # Lift all RGB channels together to preserve the palette's hue.
            cube = color - 16
            lift = 2 if config['glow'] == 'bright' else 1
            red, green, blue = cube // 36, (cube // 6) % 6, cube % 6
            lifted = 16 + 36 * min(5, red + lift) + 6 * min(5, green + lift) + min(5, blue + lift)
            curses.init_pair(32 + index, lifted, background)
        curses.init_pair(48, 255 if config['glow'] == 'bright' else 252, background)
    if curses.COLORS >= 256 and curses.COLOR_PAIRS >= 80:
        for index, color in enumerate(PALETTES[config.get('viz_palette', 'ocean')]):
            curses.init_pair(64 + index, color, background)
    return curses.color_pair(1) | curses.A_BOLD


def effect_attr(config, progress, position, glowing=True):
    color = curses.color_pair(1) if curses.has_colors() else 0
    rich = curses.has_colors() and curses.COLORS >= 256 and curses.COLOR_PAIRS >= 50
    bold = True
    if rich:
        phase = position * 0.15 if config['animate_gradient'] else 0
        blend = (0.5 - 0.5 * math.cos((progress + phase) * math.pi * 2)) if config['animate_gradient'] else progress
        index = min(7, max(0, int(blend * 7)))
        glow = glowing and config['glow'] != 'off'
        if config['gradient'] != 'off':
            color = curses.color_pair((32 if glow else 16) + index)
        elif glow:
            color = curses.color_pair(48)
    if config['pulse'] and rich and math.sin(position * math.pi * 0.7) < -0.75:
        bold = False
    return color | (curses.A_BOLD if bold else 0)


def glyphs(doc, index, position, config):
    line = doc.lines[index]
    active = doc.active(index, position)
    marker = {'chevron': '> ', 'dot': '• ', 'bar': '▎ ', 'none': '  '}[config.get('active_marker', 'chevron')]
    prefix = marker if active else '  '
    if config['timestamps']:
        prefix += f'{max(0, int(line.start)) // 60}:{max(0, int(line.start)) % 60:02d}  '
    dim = curses.A_DIM if config['dim_inactive'] else curses.A_NORMAL
    result = [(char, dim, False) for char in prefix]
    timed = active and config['word_sync'] and bool(line.words)
    if timed:
        segments = [(word.text, state, progress) for word, state, progress in
                    word_states(line, position, doc.line_end(index))]
    else:
        segments = [(line.text or '♪', 'active' if active else 'future', 0)]
    total = max(1, sum(len(safe_text(text)) for text, _, _ in segments) - 1)
    cursor = 0
    attributes = {}
    def highlight(progress, glowing=True):
        # Quantise once per palette step, rather than calling curses per character.
        bucket = round(progress * 64)
        key = (bucket, glowing)
        if key not in attributes:
            attributes[key] = effect_attr(config, bucket / 64, position, glowing)
        return attributes[key]
    for text, state, word_progress in segments:
        text = safe_text(text)
        for letter, char in enumerate(text):
            highlighted = state == 'active'
            progress = letter / max(1, len(text) - 1) if config['gradient'] == 'word' else cursor / total
            attr = highlight(progress) if highlighted else (curses.A_NORMAL if active and state == 'past' else dim)
            if active and config['gradient'] == 'line' and not highlighted and state == 'past':
                attr = (highlight(progress, glowing=False) & ~curses.A_BOLD) | dim
            if timed and highlighted:
                if config['underline_word']:
                    attr |= curses.A_UNDERLINE
                if config['word_style'] == 'sweep' and letter / max(1, len(text)) > word_progress:
                    attr = dim
            result.append((char, attr, timed and highlighted))
            cursor += 1
    return result


def wrap(glyph_list, columns):
    """Wrap at whitespace where possible, respecting wide/combining characters."""
    rows, row, cells = [], [], 0
    for glyph in glyph_list:
        size = width(glyph[0])
        if row and cells + size > columns:
            split = next((i + 1 for i in range(len(row) - 1, 0, -1) if row[i][0].isspace()), len(row))
            rows.append(row[:split])
            row = row[split:]
            cells = sum(width(g[0]) for g in row)
            if cells + size > columns:
                rows.append(row)
                row, cells = [], 0
        if size <= columns:
            row.append(glyph)
            cells += size
    if row:
        rows.append(row)
    return rows or [[]]


def put_row(screen, y, row, centered):
    h, columns = screen.getmaxyx()
    if not 0 <= y < h or columns < 4:
        return
    cells = sum(width(g[0]) for g in row)
    x = max(1, (columns - cells) // 2) if centered else 1
    clusters = []
    for char, attr, active in row:
        if width(char) == 0 and clusters:
            clusters[-1][0] += char
        else:
            clusters.append([char, attr])
    runs = []
    for char, attr in clusters:
        size = sum(width(c) for c in char)
        if x + size >= columns:
            break
        if runs and runs[-1][2] == attr:
            runs[-1][1] += char
        else:
            runs.append([x, char, attr])
        x += size
    for x, text, attr in runs:
        try:
            screen.addstr(y, x, text, attr)
        except curses.error:
            pass


@lru_cache(maxsize=512)
def inactive_rows(text, start, timestamps, dim, columns):
    prefix = '  '
    if timestamps:
        prefix += f'{max(0, int(start)) // 60}:{max(0, int(start)) % 60:02d}  '
    glyph_list = [(c, dim, False) for c in prefix + safe_text(text or '♪')]
    return tuple(tuple(row) for row in wrap(glyph_list, columns))


def draw(screen, doc, position, config, top=8, bottom=None):
    h, w = screen.getmaxyx()
    if not doc.lines or w < 6 or h <= top + 2:
        return
    bottom = h - 3 if bottom is None else min(h - 3, bottom)
    if bottom <= top:
        return
    rows_available = max(1, (bottom - top) // config['spacing'])
    columns = min(w - 4, config['max_width'] or w - 4)
    index = max(0, doc.index(position))
    radius = rows_available if config['layout'] == 'scroll' else 1
    lo, hi = max(0, index - radius), min(len(doc.lines), index + radius + 1)
    if config['layout'] == 'focus':
        lo, hi = index, index + 1
    rows, target, word_target = [], 0, None
    for line_index in range(lo, hi):
        if line_index == index:
            target = len(rows)
        if doc.active(line_index, position):
            line_rows = wrap(glyphs(doc, line_index, position, config), columns)
        else:
            line = doc.lines[line_index]
            line_rows = inactive_rows(line.text, line.start, config['timestamps'],
                                      curses.A_DIM if config['dim_inactive'] else curses.A_NORMAL, columns)
        for row in line_rows:
            if len(line_rows) > rows_available and word_target is None and any(glyph[2] for glyph in row):
                word_target = len(rows)
            rows.append(row)
    target = target if word_target is None else word_target
    if config.get('center_active', True):
        # Allow blank space at song boundaries instead of pulling the active
        # line away from the middle to fill the panel.
        start = target - rows_available // 2
        visible = rows[max(0, start):max(0, start + rows_available)]
        y = top + max(0, -start) * config['spacing']
    else:
        start = max(0, min(target - rows_available // 2, len(rows) - rows_available))
        visible = rows[start:start + rows_available]
        y = top if config['layout'] == 'scroll' else max(top, top + (bottom - top - len(visible) * config['spacing']) // 2)
    for row in visible:
        put_row(screen, y, row, config['align'] == 'center')
        y += config['spacing']


def chrome(screen, config):
    """Apply the chosen background and frame before drawing contents."""
    if curses.has_colors() and screen.getbkgd() != (ord(' ') | curses.color_pair(2)):
        screen.bkgd(' ', curses.color_pair(2))
    screen.erase()
    style = config.get('border', 'none')
    h, w = screen.getmaxyx()
    if style == 'none' or h < 4 or w < 6:
        return
    tl, tr, bl, br, horizontal, vertical = {
        'line': ('┌', '┐', '└', '┘', '─', '│'),
        'rounded': ('╭', '╮', '╰', '╯', '─', '│'),
        'double': ('╔', '╗', '╚', '╝', '═', '║'),
    }[style]
    attr = curses.color_pair(1) if curses.has_colors() else curses.A_DIM
    strokes = [(0, 0, tl + horizontal * (w - 2) + tr),
               (h - 1, 0, bl + horizontal * (w - 2) + br)]
    strokes += [(row, col, vertical) for row in range(1, h - 1) for col in (0, w - 1)]
    for row, col, text in strokes:
        try:
            screen.addstr(row, col, text, attr)
        except curses.error:
            pass


def progress_bar(fraction, columns, style):
    filled = min(columns, max(0, int(columns * fraction)))
    on, off = {'line': ('━', '─'), 'blocks': ('█', '░'),
               'dots': ('●', '·'), 'ascii': ('=', '-')}[style]
    return on * filled + off * (columns - filled)
