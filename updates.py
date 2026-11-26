"""Versioned GitHub release checks and verified updates."""
import concurrent.futures
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import deployment
import settings

VERSION = (Path(__file__).with_name('VERSION')).read_text().strip()
REPOSITORY = 'JudokaCube/tlyrics'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
STATE = settings.CONFIG.parent / 'update-state.json'
MAX_ARCHIVE = 20 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    version: str
    archive_url: str
    checksum_url: str
    notes: str = ''

    @property
    def filename(self):
        return f'tlyrics-{self.version}.tar.gz'


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        url = urllib.parse.urlparse(newurl)
        if url.scheme != 'https' or url.hostname not in ('github.com', 'api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            raise ValueError('Unexpected update download redirect')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': f'tlyrics/{VERSION} (https://github.com/{REPOSITORY})',
                                                 'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream'})
    with urllib.request.build_opener(HTTPSRedirect()).open(request, timeout=12) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('Update download exceeds the size limit')
    return payload


def latest():
    try:
        data = json.loads(download(API, 2 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code == 404:
            return None
        if code in (403, 429):
            raise RuntimeError('GitHub rate limit reached. Try again later.') from None
        raise RuntimeError(f'GitHub returned HTTP {code}') from None
    if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
        return None
    tag = data.get('tag_name', '')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise ValueError('Release has no valid version tag')
    version = tag[1:]
    deployment.version_tuple(version)
    base = f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
    wanted = [f'tlyrics-{version}.tar.gz', 'SHA256SUMS']
    assets = data.get('assets', [])
    if not isinstance(assets, list):
        raise ValueError('Invalid release assets')
    found = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get('name') in wanted:
            name = asset['name']
            if asset.get('browser_download_url') != base + name:
                raise ValueError('Release asset URL does not match the official repository')
            found[name] = asset['browser_download_url']
    if any(name not in found for name in wanted):
        raise ValueError('This release has no compatible update package')
    notes = data.get('body') or ''
    return Release(version, found[wanted[0]], found[wanted[1]], notes[:8000] if isinstance(notes, str) else '')


def newer(release, current=VERSION):
    return release is not None and deployment.version_tuple(release.version) > deployment.version_tuple(current)


def load_state():
    try:
        state = json.loads(STATE.read_text())
        if isinstance(state, dict):
            checked = state.get('checked_at', 0)
            if type(checked) in (int, float) and math.isfinite(checked) and 0 <= checked <= time.time():
                return state
    except (OSError, ValueError):
        pass
    return {'checked_at': 0}


def save_checked():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps({'checked_at': time.time()}) + '\n')
    temp.replace(STATE)


def due(config):
    return config['update_mode'] != 'off' and time.time() - load_state()['checked_at'] >= config['update_hours'] * 3600


def fetch_package(release, destination):
    deployment.version_tuple(release.version)
    base = f'https://github.com/{REPOSITORY}/releases/download/v{release.version}/'
    if release.archive_url != base + release.filename or release.checksum_url != base + 'SHA256SUMS':
        raise ValueError('Unexpected update URL')
    checksums = download(release.checksum_url, 16384).decode('ascii')
    matches = [line.split() for line in checksums.splitlines() if line.strip()]
    expected = [parts[0] for parts in matches if len(parts) == 2 and parts[1].lstrip('*') == release.filename]
    if len(expected) != 1 or not re.fullmatch(r'[0-9a-f]{64}', expected[0]):
        raise ValueError('Missing or invalid release checksum')
    payload = download(release.archive_url, MAX_ARCHIVE)
    if hashlib.sha256(payload).hexdigest() != expected[0]:
        raise ValueError('Update checksum mismatch; the current version was kept')
    root_name = f'tlyrics-{release.version}'
    root = Path(destination) / root_name
    seen, total = set(), 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if member.isdir() and parts == (root_name,):
                continue
            if (len(parts) != 2 or parts[0] != root_name or not member.isfile()
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}', parts[1]) or member.name in seen):
                raise ValueError('Unsafe update archive entry')
            seen.add(member.name)
            total += member.size
            if total > MAX_ARCHIVE or len(seen) > 100:
                raise ValueError('Update archive is too large')
            root.mkdir(exist_ok=True)
            with archive.extractfile(member) as source:
                (root / parts[1]).write_bytes(source.read())
    version, files = deployment.manifest(root)
    if version != release.version:
        raise ValueError('Downloaded version does not match the release')
    return root


def install_update(release, root=None, binary=None):
    root = Path(root) if root is not None else deployment.installed_root()
    if root is None:
        raise RuntimeError('This is a source checkout. Run sh install.sh, then use the installed tlyrics command.')
    with tempfile.TemporaryDirectory(prefix='tlyrics-update-') as directory:
        source = fetch_package(release, directory)
        return deployment.install(source, root=root, binary=binary, update=True)


class Manager:
    """One update worker; its results are consumed by the terminal UI."""
    def __init__(self):
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.action = None
        self.manual = False
        self.message = ''
        self.pending = None
        self.attempted_at = -math.inf

    def check(self, config, manual=False):
        if self.future or self.pending:
            return
        if not manual and (time.monotonic() - self.attempted_at < 60 or not due(config)):
            return
        self.manual, self.action = manual, 'check'
        self.attempted_at = time.monotonic()
        self.message = 'Checking for updates...'
        self.future = self.pool.submit(latest)

    def install(self, release):
        if self.future:
            return
        self.action = 'install'
        self.message = f'Installing tlyrics {release.version}...'
        self.future = self.pool.submit(install_update, release)

    def poll(self):
        if not self.future or not self.future.done():
            return None
        future, action = self.future, self.action
        self.future = None
        try:
            result = future.result()
            if action == 'install':
                self.message = 'Update installed. Restarting...'
                return result
            if newer(result):
                self.pending = result
                self.message = f'tlyrics {result.version} is available'
            else:
                self.message = f'tlyrics {VERSION} is up to date' if result else 'No published releases yet'
        except Exception as exc:
            self.message = f'Update failed: {exc}'
        finally:
            if action == 'check':
                try:
                    save_checked()
                except OSError:
                    pass
        return None

    def close(self):
        # An accepted installation must finish, even if the UI is closed.
        self.pool.shutdown(wait=self.action == 'install', cancel_futures=self.action != 'install')
