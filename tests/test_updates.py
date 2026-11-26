import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import build_release
import deployment
import players
import settings
import tlyrics
import updates

SOURCE = Path(__file__).resolve().parents[1]


class PlayerTests(unittest.TestCase):
    def test_unrelated_and_spoofed_sessions(self):
        for name, url in [('firefox', ''), ('firefox', 'https://web.whatsapp.com'),
                          ('chrome', 'https://open.spotify.com.evil.com'),
                          ('firefox', 'https://evil.com/open.spotify.com'),
                          ('whatsapp', 'https://music.youtube.com'), ('discord', ''),
                          ('spotifyfake', ''), ('firefox', 'file://open.spotify.com/song')]:
            self.assertIsNone(players.classify(name, url), (name, url))
        track = tlyrics.Track('firefox', 'WhatsApp', 'Someone', '', 60, 10, 'Playing')
        self.assertIsNone(tlyrics.select_player([track]))
        self.assertIsNone(tlyrics.select_player([track], 'firefox'))

    def test_supported_players(self):
        for name in ['spotify', 'spotify.instance42', 'limusic', 'pear-desktop', 'YoutubeMusic', 'vlc', 'cider']:
            self.assertIsNotNone(players.classify(name))
        for host, service in players.WEB_SERVICES.items():
            self.assertEqual(players.classify('firefox.instance123', 'https://' + host + '/song'), service)


class UpdateTests(unittest.TestCase):
    def test_versions_and_settings(self):
        self.assertGreater(deployment.version_tuple('1.10.0'), deployment.version_tuple('1.9.9'))
        for value in ['1.0', 'v1.0.0', '../1.0.0', '01.0.0', '1.0.0-beta']:
            with self.assertRaises(ValueError):
                deployment.version_tuple(value)
        with patch.object(updates, 'load_state', return_value={'checked_at': 0}):
            self.assertTrue(updates.due(settings.DEFAULTS))
            self.assertFalse(updates.due(dict(settings.DEFAULTS, update_mode='off')))

    def package(self, temp):
        source = temp / 'source'
        source.mkdir()
        for name in (*deployment.RUNTIME_FILES, 'install.sh', 'README.md'):
            shutil.copy2(SOURCE / name, source / name)
        (source / 'VERSION').write_text('1.0.1\n')
        archive = build_release.build(source, temp / 'dist')
        base = 'https://github.com/JudokaCube/tlyrics/releases/download/v1.0.1/'
        release = updates.Release('1.0.1', base + archive.name, base + 'SHA256SUMS')
        return source, archive, release

    def test_real_upgrade_and_failed_install_preserves_current(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, archive, release = self.package(temp)
            root = temp / 'installed'
            old = root / 'releases/old'
            old.mkdir(parents=True)
            (old / 'VERSION').write_text('1.0.0')
            shutil.copy2(source / 'requirements.txt', old / 'requirements.txt')
            (root / 'current').symlink_to(old)
            binary = temp / 'bin/tlyrics'
            def download(url, limit):
                return (archive.parent / 'SHA256SUMS').read_bytes() if url.endswith('SHA256SUMS') else archive.read_bytes()
            with patch.object(updates, 'download', side_effect=download):
                launch = updates.install_update(release, root, binary)
            self.assertEqual(subprocess.check_output([str(launch), '--version'], text=True).strip(), 'tlyrics 1.0.1')
            active = (root / 'current').resolve()
            self.assertTrue(old.is_dir())
            (source / 'VERSION').write_text('1.0.2')
            (source / 'tlyrics.py').write_text('invalid python syntax!!')
            with self.assertRaises(SyntaxError):
                deployment.install(source, root, binary, update=True)
            self.assertEqual((root / 'current').resolve(), active)
            self.assertEqual(len(list((root / 'releases').iterdir())), 2)

    def test_checksum_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, archive, release = self.package(temp)
            with patch.object(updates, 'download', side_effect=[b'0' * 64 + b'  ' + archive.name.encode(), archive.read_bytes()]):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    updates.fetch_package(release, temp)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode='w:gz') as tar:
                entry = tarfile.TarInfo('tlyrics-1.0.1/../../escape')
                entry.size = 1
                tar.addfile(entry, io.BytesIO(b'x'))
            payload = buf.getvalue()
            checksum = f'{hashlib.sha256(payload).hexdigest()}  {archive.name}'.encode()
            with patch.object(updates, 'download', side_effect=[checksum, payload]):
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    updates.fetch_package(release, temp)

    def test_source_checkout_cannot_self_update(self):
        with patch.object(deployment, 'installed_root', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'source checkout'):
                updates.install_update(None)

    def test_release_urls(self):
        data = {'tag_name': 'v1.0.1', 'assets': [{'name': 'SHA256SUMS', 'browser_download_url': 'https://evil.com'}]}
        with patch.object(updates, 'download', return_value=json.dumps(data).encode()):
            with self.assertRaisesRegex(ValueError, 'official repository'):
                updates.latest()

    def test_manager_prompt_auto_check_and_failures(self):
        from concurrent.futures import Future
        manager = updates.Manager()
        self.addCleanup(manager.close)
        release = updates.Release('9.0.0', '', '')
        manager.action = 'check'
        manager.future = Future()
        manager.future.set_result(release)
        with patch.object(updates, 'save_checked'):
            self.assertIsNone(manager.poll())
        self.assertEqual(manager.pending, release)
        self.assertIsNone(manager.future)  # Discovery alone never installs.
        manager.pending = None
        manager.future = Future()
        manager.future.set_exception(OSError('offline'))
        with patch.object(updates, 'save_checked'):
            self.assertIsNone(manager.poll())
        self.assertIn('offline', manager.message)
        with patch.object(manager.pool, 'submit') as submit:
            manager.check(dict(settings.DEFAULTS, update_mode='off'))
            submit.assert_not_called()
            manager.check(dict(settings.DEFAULTS, update_mode='off'), manual=True)
            submit.assert_called_once_with(updates.latest)
        manager.future = None
