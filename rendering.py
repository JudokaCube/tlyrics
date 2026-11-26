"""Cell-aware lyrics rendering and optional curses colour effects."""
import curses
import math
import unicodedata
from lyrics import word_states

PALETTES = {
    'ocean': [33, 39, 45, 51, 50, 49, 48, 47],
    'sunset': [196, 202, 208, 214, 220, 213, 207, 201],
    'aurora': [57, 63, 69, 75, 81, 87, 86, 85],
    'rainbow': [196, 208, 226, 46, 51, 33, 93, 201],
}


def width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1


def safe_text(text):
    return ''.join(char for char in text if char.isprintable())


def configure(config):
    if not curses.has_colors():
        return curses.A_BOLD
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    colors = dict(cyan=curses.COLOR_CYAN, green=curses.COLOR_GREEN, purple=curses.COLOR_MAGENTA,
                  amber=curses.COLOR_YELLOW, white=curses.COLOR_WHITE)
    curses.init_pair(1, colors[config['theme']], background)
    if curses.COLORS >= 256 and curses.COLOR_PAIRS >= 50:
        for index, color in enumerate(PALETTES[config['palette']]):
            curses.init_pair(16 + index, color, background)
            curses.init_pair(32 + index, color, 235 if config['glow'] == 'soft' else 238)
        curses.init_pair(48, colors[config['theme']], 235 if config['glow'] == 'soft' else 238)
    return curses.color_pair(1) | curses.A_BOLD


def effect_attr(config, progress, position, glowing=True):
    color = curses.color_pair(1) if curses.has_colors() else 0
    rich = curses.has_colors() and curses.COLORS >= 256 and curses.COLOR_PAIRS >= 50
    bold = not config['pulse'] or math.sin(position * math.pi * 2) > -0.6
    if rich:
        phase = position * 0.15 if config['animate_gradient'] else 0
        index = min(7, int(((progress + phase) % 1.000001) * 8))
        glow = glowing and config['glow'] != 'off'
        if config['gradient'] != 'off':
            color = curses.color_pair((32 if glow else 16) + index)
        elif glow:
            color = curses.color_pair(48)
    return color | (curses.A_BOLD if bold else 0)


def glyphs(doc, index, position, config):
    line = doc.lines[index]
    active = doc.active(index, position)
    prefix = ('> ' if active else '  ')
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
    for text, state, word_progress in segments:
        text = safe_text(text)
        for letter, char in enumerate(text):
            highlighted = state == 'active'
            progress = letter / max(1, len(text) - 1) if config['gradient'] == 'word' else cursor / total
            attr = effect_attr(config, progress, position) if highlighted else dim
            if active and config['gradient'] == 'line' and not highlighted:
                attr = (effect_attr(config, progress, position, glowing=False) & ~curses.A_BOLD) | dim
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
    for char, attr in clusters:
        size = sum(width(c) for c in char)
        if x + size >= columns:
            break
        try:
            screen.addstr(y, x, char, attr)
        except curses.error:
            pass
        x += size


def draw(screen, doc, position, config, top=8):
    h, w = screen.getmaxyx()
    if not doc.lines or w < 6 or h <= top + 2:
        return
    rows_available = max(1, (h - top - 3) // config['spacing'])
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
        for row in wrap(glyphs(doc, line_index, position, config), columns):
            if word_target is None and any(glyph[2] for glyph in row):
                word_target = len(rows)
            rows.append(row)
    target = target if word_target is None else word_target
    start = max(0, min(target - rows_available // 2, len(rows) - rows_available))
    visible = rows[start:start + rows_available]
    y = top if config['layout'] == 'scroll' else max(top, top + (h - top - 3 - len(visible) * config['spacing']) // 2)
    for row in visible:
        put_row(screen, y, row, config['align'] == 'center')
        y += config['spacing']
