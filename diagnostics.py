"""Local, bounded diagnostics without lyrics, tokens or exception payloads."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import threading
import time
import traceback
import contextlib

ROOT = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'tlyrics'
LOG = ROOT / 'diagnostics.log'
logger = logging.getLogger('tlyrics.diagnostics')
logger.propagate = False
logger.addHandler(logging.NullHandler())
_signature = None
_recent = {}
_lock = threading.RLock()


class PrivateLog(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, 'a', encoding='utf-8')

    def handleError(self, record):
        pass  # Logging failures must never corrupt the terminal UI.


def configure(config):
    global _signature
    signature = (config['log_level'], config['log_files'], str(LOG))
    with _lock:
        if signature == _signature:
            return
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger.addHandler(logging.NullHandler())
        _signature = signature
        _recent.clear()
        logger.setLevel(logging.DEBUG if signature[0] == 'debug' else logging.ERROR)
        if signature[0] == 'off':
            return
        try:
            ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
            LOG.chmod(0o600)
            handler = PrivateLog(LOG, maxBytes=256 * 1024, backupCount=signature[1] - 1, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(handler)
            for index in range(signature[1], 8):
                LOG.with_name(LOG.name + f'.{index}').unlink(missing_ok=True)
        except OSError:
            pass


def record(event, exc=None, debug=False):
    with _lock:
        if not _signature or _signature[0] == 'off':
            return
        now = time.monotonic()
        if now - _recent.get(event, -100) < 60:
            return
        _recent[event] = now
        detail = event
        if exc is not None:
            detail += ' [' + type(exc).__name__ + ']'
            for frame in traceback.extract_tb(exc.__traceback__):
                detail += f'\n  {Path(frame.filename).name}:{frame.lineno} in {frame.name}'
        # Exception messages, source lines and locals can contain private data.
        try:
            logger.log(logging.DEBUG if debug else logging.ERROR, detail)
        except OSError:
            pass


def read():
    with _lock:
        parts = []
        for index in range(6, -1, -1):
            path = LOG.with_name(LOG.name + f'.{index}') if index else LOG
            try:
                with path.open('rb') as handle:
                    handle.seek(0, os.SEEK_END)
                    handle.seek(max(0, handle.tell() - 256 * 1024))
                    parts.append(handle.read(256 * 1024).decode('utf-8', errors='replace'))
            except OSError:
                pass
        return ''.join(parts) or 'No diagnostics recorded.'


def clear():
    with _lock:
        for index in range(7):
            path = LOG.with_name(LOG.name + f'.{index}') if index else LOG
            if index:
                path.unlink(missing_ok=True)
            elif path.exists():
                with path.open('w'):
                    pass
        _recent.clear()


def export(path, config):
    import settings
    payload = {'app': 'tlyrics', 'version': Path(__file__).with_name('VERSION').read_text().strip(),
               'system': platform.system(), 'architecture': platform.machine(),
               'python': platform.python_version(),
               'settings': {key: config[key] for key in settings.CHOICES}, 'diagnostics': read()}
    path = Path(path).expanduser()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)
        handle.write('\n')
    return path


class QuietStderr:
    def write(self, text):
        if text.strip():
            record('Background stderr output suppressed')
        return len(text)

    def flush(self):
        pass


@contextlib.contextmanager
def quiet_errors():
    original = threading.excepthook
    def thread_error(args):
        record('Background thread failed', args.exc_value)
    threading.excepthook = thread_error
    try:
        with contextlib.redirect_stderr(QuietStderr()):
            yield
    finally:
        threading.excepthook = original
