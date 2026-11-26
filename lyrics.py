"""Validated line and word timestamps from LRC and LRCLIB Lyricsfile."""
from dataclasses import dataclass, field
import bisect
import math
import re

MAX_BYTES = 2_000_000
STAMP = r'(\d+):(\d{1,2}(?:\.\d{1,3})?)'


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float | None = None


@dataclass
class Line:
    start: float
    text: str
    end: float | None = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Document:
    lines: list[Line] = field(default_factory=list)
    plain: str = ''
    warning: str = ''

    def __post_init__(self):
        self.lines.sort(key=lambda line: line.start)
        self.starts = [line.start for line in self.lines]

    @property
    def has_words(self):
        return any(line.words for line in self.lines)

    def index(self, position):
        return bisect.bisect_right(self.starts, position) - 1

    def line_end(self, index):
        line = self.lines[index]
        if line.end is not None:
            return line.end
        following = bisect.bisect_right(self.starts, line.start)
        return self.starts[following] if following < len(self.lines) else math.inf

    def active(self, index, position):
        return self.lines[index].start <= position < self.line_end(index)


def stamp(minutes, seconds):
    if len(minutes) > 6 or int(minutes) > 10080 or float(seconds) >= 60:
        raise ValueError('Invalid LRC timestamp')
    return int(minutes) * 60 + float(seconds)


def parse_lrc(text):
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Lyrics file is too large')
    offset = re.search(r'\[offset:([+-]?\d+)\]', text, re.I)
    # Standard LRC positive offsets make lyrics appear earlier.
    shift = -int(offset[1]) / 1000 if offset else 0
    lines = []
    for source in text.lstrip('\ufeff').splitlines():
        timestamps = re.findall(r'\[' + STAMP + r'\]', source)
        if not timestamps:
            continue
        body = re.sub(r'\[[^\]]*\]', '', source).strip()
        tags = list(re.finditer(r'<' + STAMP + r'>', body))
        words = []
        if tags:
            if body[:tags[0].start()].strip():
                words.append(Word(body[:tags[0].start()], stamp(*timestamps[0]) + shift))
            for index, tag in enumerate(tags):
                next_tag = tags[index + 1] if index + 1 < len(tags) else None
                value = body[tag.end():next_tag.start() if next_tag else len(body)]
                end = stamp(*next_tag.groups()) + shift if next_tag else None
                if value:
                    words.append(Word(value, stamp(*tag.groups()) + shift, end))
            body = re.sub(r'<' + STAMP + r'>', '', body)
        first = stamp(*timestamps[0])
        for timestamp in timestamps:
            start = stamp(*timestamp)
            delta = start - first
            shifted = [Word(w.text, w.start + delta, None if w.end is None else w.end + delta) for w in words]
            # Malformed inline timing falls back to the complete line.
            if any(w.start < start + shift or (w.end is not None and w.end < w.start)
                   or (i and w.start < shifted[i - 1].start) for i, w in enumerate(shifted)):
                shifted = []
            # A final empty inline tag explicitly terminates the final word/line.
            end = stamp(*tags[-1].groups()) + shift + delta if tags and tags[-1].end() == len(re.sub(r'\[[^\]]*\]', '', source).strip()) else None
            lines.append(Line(start + shift, body, end, shifted))
    return Document(lines)


def yaml_document(text):
    import yaml
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Lyricsfile is too large')
    depth = 0
    safe_loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
    for event in yaml.parse(text, Loader=safe_loader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, 'anchor', None):
            raise ValueError('YAML anchors and aliases are not supported')
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
            if depth > 12:
                raise ValueError('Lyricsfile nesting is too deep')
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1

    class UniqueLoader(safe_loader):
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError('Duplicate or invalid Lyricsfile key')
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    return yaml.load(text, Loader=UniqueLoader)


def milliseconds(value, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not 0 <= value <= 604800000:
        raise ValueError('Invalid Lyricsfile timestamp')
    return value / 1000


def parse_lyricsfile(text):
    try:
        data = yaml_document(text)
    except ImportError:
        raise ValueError('Install PyYAML for Lyricsfile word sync; run install.sh') from None
    except Exception as exc:
        raise ValueError('Invalid Lyricsfile YAML') from exc
    if not isinstance(data, dict) or data.get('version') != '1.0':
        raise ValueError('Unsupported Lyricsfile version')
    entries = data.get('lines', [])
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError('Invalid Lyricsfile lines')
    lines = []
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise ValueError('Invalid lyric line')
        start, end = milliseconds(item.get('start_ms')), milliseconds(item.get('end_ms'), True)
        if end is not None and end < start:
            raise ValueError('Lyric line ends before it starts')
        words = []
        segments = item.get('words', [])
        if not isinstance(segments, list) or len(segments) > 2000:
            raise ValueError('Invalid word list')
        for word in segments:
            if not isinstance(word, dict) or not isinstance(word.get('text'), str):
                raise ValueError('Invalid lyric word')
            begin, stop = milliseconds(word.get('start_ms')), milliseconds(word.get('end_ms'), True)
            if begin < start or (end is not None and begin > end) or (stop is not None and stop < begin):
                raise ValueError('Invalid word timing')
            if words and begin < words[-1].start:
                raise ValueError('Words are out of time order')
            words.append(Word(word['text'], begin, stop))
        if words and ''.join(w.text for w in words) != item['text']:
            words = []  # Preserve full line text rather than highlight the wrong characters.
        lines.append(Line(start, item['text'], end, words))
    plain = data.get('plain', '')
    if not isinstance(plain, str):
        raise ValueError('Invalid plain lyrics')
    metadata = data.get('metadata', {})
    warning = 'Lyricsfile offset_ms is undefined by its draft format; use song timing adjustment.' if isinstance(metadata, dict) and metadata.get('offset_ms') else ''
    return Document(lines, plain, warning)


def from_record(record):
    warning = ''
    raw = record.get('lyricsfile')
    if raw:
        try:
            doc = parse_lyricsfile(raw)
            if doc.lines or doc.plain:
                return doc
        except ValueError as exc:
            warning = str(exc)
    try:
        doc = parse_lrc(record.get('syncedLyrics') or '')
    except ValueError as exc:
        doc = Document(warning=str(exc))
    doc.plain = record.get('plainLyrics') or doc.plain
    doc.warning = warning or doc.warning
    return doc


def word_states(line, position, line_end):
    """Return (word, state, progress), preserving explicit gaps and overlaps."""
    result = []
    for i, word in enumerate(line.words):
        end = word.end
        if end is None:
            following = next((w.start for w in line.words[i + 1:] if w.start > word.start), line_end)
            end = following
        if position < word.start:
            state, progress = 'future', 0.0
        elif position >= end:
            state, progress = 'past', 1.0
        else:
            state = 'active'
            progress = (position - word.start) / (end - word.start) if math.isfinite(end) and end > word.start else 0.0
        result.append((word, state, progress))
    return result
