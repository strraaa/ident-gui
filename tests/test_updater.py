"""
Проверки updater.py — проверки версии, загрузки и установки обновлений.

Сеть и exe подменяются моками: тесты не ходят в интернет и не рассчитывают
на Windows-исполняемый файл, как и остальные тесты сервисного слоя.
"""

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.services import updater  # noqa: E402


class VersionParsingTests(unittest.TestCase):
    """parse_version / is_newer — сравнение версий, а не строк"""

    def test_parses_plain_version(self):
        self.assertEqual(updater.parse_version('1.2.3'), (1, 2, 3))

    def test_strips_leading_v(self):
        self.assertEqual(updater.parse_version('v0.0.10'), (0, 0, 10))

    def test_pads_short_versions(self):
        self.assertEqual(updater.parse_version('2.1'), (2, 1, 0))

    def test_ignores_prerelease_suffix(self):
        self.assertEqual(updater.parse_version('1.4.0-rc1'), (1, 4, 0))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer('0.0.3', local='0.0.2'))
        self.assertFalse(updater.is_newer('0.0.2', local='0.0.2'))
        self.assertFalse(updater.is_newer('0.0.1', local='0.0.2'))

    def test_ten_is_newer_than_nine_not_string_sorted(self):
        # '0.0.10' < '0.0.9' при сравнении строк, но не как версия
        self.assertTrue(updater.is_newer('0.0.10', local='0.0.9'))


class CheckTests(unittest.TestCase):
    """check() разбирает ответ GitHub Releases API"""

    @staticmethod
    def _response(payload):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = payload
        return response

    @patch('gui.services.updater.requests.get')
    def test_no_update_when_not_newer(self, mock_get):
        mock_get.return_value = self._response({'tag_name': 'v0.0.1', 'assets': []})
        self.assertIsNone(updater.check())

    @patch('gui.services.updater.requests.get')
    def test_finds_update_and_asset(self, mock_get):
        mock_get.return_value = self._response({
            'tag_name': 'v9.9.9',
            'body': 'Что нового',
            'assets': [
                {'name': 'ident_settings_v9.9.9_win64.zip',
                 'browser_download_url': 'https://example.invalid/app.zip'},
            ],
        })

        info = updater.check()

        self.assertIsNotNone(info)
        self.assertEqual(info.version, '9.9.9')
        self.assertEqual(info.notes, 'Что нового')
        self.assertEqual(info.download_url, 'https://example.invalid/app.zip')
        self.assertIsNone(info.checksum)

    @patch('gui.services.updater.requests.get')
    def test_reads_checksum_from_sums_file(self, mock_get):
        release_response = self._response({
            'tag_name': 'v9.9.9',
            'assets': [
                {'name': 'ident_settings_v9.9.9_win64.zip',
                 'browser_download_url': 'https://example.invalid/app.zip'},
                {'name': 'SHA256SUMS.txt',
                 'browser_download_url': 'https://example.invalid/sums.txt'},
            ],
        })
        sums_response = Mock()
        sums_response.raise_for_status = Mock()
        sums_response.text = (
            'deadbeef00000000000000000000000000000000000000000000000000000000  '
            'ident_settings_v9.9.9_win64.zip\n'
        )
        mock_get.side_effect = [release_response, sums_response]

        info = updater.check()

        self.assertEqual(info.checksum, 'deadbeef00000000000000000000000000000000000000000000000000000000')

    @patch('gui.services.updater.requests.get')
    def test_missing_asset_raises(self, mock_get):
        mock_get.return_value = self._response({'tag_name': 'v9.9.9', 'assets': []})

        with self.assertRaises(updater.UpdateError):
            updater.check()

    @patch('gui.services.updater.requests.get')
    def test_network_error_raises_update_error(self, mock_get):
        mock_get.side_effect = updater.requests.ConnectionError('нет сети')

        with self.assertRaises(updater.UpdateError):
            updater.check()

    @patch('gui.services.updater.requests.get')
    def test_missing_checksum_file_is_reported(self, mock_get):
        release_response = self._response({
            'tag_name': 'v9.9.9',
            'assets': [
                {'name': 'ident_settings_v9.9.9_win64.zip',
                 'browser_download_url': 'https://example.invalid/app.zip'},
                {'name': 'SHA256SUMS.txt',
                 'browser_download_url': 'https://example.invalid/sums.txt'},
            ],
        })
        mock_get.side_effect = [release_response, updater.requests.ConnectionError('нет сети')]

        info = updater.check()

        self.assertIsNotNone(info)
        self.assertIsNone(info.checksum)


class DownloadAndVerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @patch('gui.services.updater.requests.get')
    def test_download_writes_file_and_reports_progress(self, mock_get):
        content = b'0123456789' * 10
        response = Mock()
        response.raise_for_status = Mock()
        response.headers = {'Content-Length': str(len(content))}
        response.iter_content.return_value = [
            content[i:i + 10] for i in range(0, len(content), 10)
        ]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        mock_get.return_value = response

        info = updater.UpdateInfo(version='9.9.9', notes='', download_url='https://example.invalid/app.zip',
                                   asset_name='app.zip', checksum=None)
        progress = []

        path = updater.download(info, self.tmp, on_progress=lambda done, total: progress.append((done, total)))

        self.assertEqual(path.read_bytes(), content)
        self.assertTrue(progress)
        self.assertEqual(progress[-1], (len(content), len(content)))

    @patch('gui.services.updater.requests.get')
    def test_download_failure_removes_partial_file(self, mock_get):
        mock_get.side_effect = updater.requests.ConnectionError('оборвалось')

        info = updater.UpdateInfo(version='9.9.9', notes='', download_url='https://example.invalid/app.zip',
                                   asset_name='app.zip', checksum=None)

        with self.assertRaises(updater.UpdateError):
            updater.download(info, self.tmp)
        self.assertFalse((self.tmp / 'app.zip').exists())

    def test_verify_accepts_matching_checksum(self):
        archive = self.tmp / 'app.zip'
        archive.write_bytes(b'hello world')
        digest = hashlib.sha256(b'hello world').hexdigest()
        info = updater.UpdateInfo(version='9.9.9', notes='', download_url='', asset_name='app.zip', checksum=digest)

        updater.verify(archive, info)  # не должно поднять исключение
        self.assertTrue(archive.exists())

    def test_verify_rejects_mismatched_checksum(self):
        archive = self.tmp / 'app.zip'
        archive.write_bytes(b'hello world')
        info = updater.UpdateInfo(version='9.9.9', notes='', download_url='',
                                   asset_name='app.zip', checksum='0' * 64)

        with self.assertRaises(updater.UpdateError):
            updater.verify(archive, info)
        self.assertFalse(archive.exists())  # подозрительный файл не остаётся на диске

    def test_verify_rejects_when_no_checksum_published(self):
        archive = self.tmp / 'app.zip'
        archive.write_bytes(b'hello world')
        info = updater.UpdateInfo(version='9.9.9', notes='', download_url='',
                                   asset_name='app.zip', checksum=None)

        with self.assertRaises(updater.UpdateError):
            updater.verify(archive, info)
        self.assertTrue(archive.exists())


class InstallTests(unittest.TestCase):
    """install() — атомарная замена папки с откатом при сбое"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        self.install_dir = self.root / 'settings'
        self.install_dir.mkdir()
        (self.install_dir / updater.EXE_NAME).write_text('старая версия')
        (self.install_dir / 'marker.txt').write_text('старая версия')

        self.backup_dir = self.install_dir.with_name('settings.bak')

    def tearDown(self):
        self._tmp.cleanup()

    def _make_archive(self, exe_name=None) -> Path:
        exe_name = updater.EXE_NAME if exe_name is None else exe_name
        archive = self.root / 'update.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr(f'ident_settings/{exe_name}', 'новая версия')
            zf.writestr('ident_settings/marker.txt', 'новая версия')
        return archive

    def test_install_replaces_files_on_successful_selftest(self):
        archive = self._make_archive()

        result = updater.install(archive, self.install_dir, run_selftest=lambda exe: None)

        self.assertTrue(result.installed)
        self.assertTrue(result.restart_required)
        self.assertEqual((self.install_dir / 'marker.txt').read_text(), 'новая версия')
        self.assertFalse(self.backup_dir.exists())

    def test_install_rolls_back_when_selftest_fails(self):
        archive = self._make_archive()

        def failing_selftest(exe):
            raise updater.UpdateError('самопроверка не пройдена')

        with self.assertRaises(updater.UpdateError):
            updater.install(archive, self.install_dir, run_selftest=failing_selftest)

        self.assertEqual((self.install_dir / 'marker.txt').read_text(), 'старая версия')
        self.assertFalse(self.backup_dir.exists())

    def test_install_rolls_back_when_exe_missing_in_archive(self):
        archive = self._make_archive(exe_name='wrong_name.exe')

        with self.assertRaises(updater.UpdateError):
            updater.install(archive, self.install_dir, run_selftest=lambda exe: None)

        self.assertEqual((self.install_dir / 'marker.txt').read_text(), 'старая версия')
        self.assertFalse(self.backup_dir.exists())

    def test_install_refuses_to_run_against_source_checkout(self):
        archive = self._make_archive()

        with self.assertRaises(updater.UpdateError):
            updater.install(archive, install_dir=None, run_selftest=lambda exe: None)

    def test_extract_rejects_path_traversal(self):
        archive = self.root / 'unsafe.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('../outside.txt', 'не должно появиться')

        with self.assertRaises(updater.UpdateError):
            updater._extract(archive, self.root / 'extract')

        self.assertFalse((self.root / 'outside.txt').exists())


if __name__ == '__main__':
    unittest.main()
