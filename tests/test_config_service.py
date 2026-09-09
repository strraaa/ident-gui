"""
Проверки ConfigService — слоя, которым пользуются страницы приложения.

Тесты не поднимают Qt: ConfigService про интерфейс ничего не знает.
"""

import configparser
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.services.config_service import ConfigService  # noqa: E402
from gui.services.paths import Workspace  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / 'config.example.ini'


class ConfigServiceTests(unittest.TestCase):
    """Загрузка, правка и запись конфигурации"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

        self.config_path = self.workdir / 'config.ini'
        self.config_path.write_bytes(EXAMPLE.read_bytes())

        self.service = ConfigService(Workspace(self.workdir))
        self.assertTrue(self.service.load(), self.service.load_error)

    def tearDown(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------------

    def test_missing_file_reports_error(self):
        service = ConfigService(Workspace(self.workdir / 'нет-такой-папки'))

        self.assertFalse(service.load())
        self.assertIn('Файл не найден', service.load_error)

    def test_create_from_example_keeps_comments(self):
        empty = self.workdir / 'новая'
        service = ConfigService(Workspace(empty))

        # config.example.ini ищется рядом с конфигом, поэтому кладём его туда
        empty.mkdir()
        (empty / 'config.example.ini').write_bytes(EXAMPLE.read_bytes())

        self.assertTrue(service.create_from_example())
        self.assertIn(
            '# Интервал синхронизации записей в минутах',
            (empty / 'config.ini').read_text(encoding='utf-8-sig')
        )

    def test_reads_typed_values(self):
        self.assertEqual(self.service.get_int('Sync', 'interval_minutes'), 2)
        self.assertEqual(self.service.get_int('Sync', 'batch_size'), 50)
        self.assertIs(self.service.get_bool('Logging', 'mask_personal_data'), True)
        self.assertIs(self.service.get_bool('Advanced', 'dry_run'), False)

    def test_unknown_key_returns_given_fallback(self):
        self.assertEqual(self.service.get('Sync', 'нет_такого', 'запасное'), 'запасное')
        self.assertEqual(self.service.get_int('Sync', 'нет_такого', 17), 17)

    def test_dirty_flag_reacts_to_edit_not_to_save(self):
        """Прежняя схема взводила признак только внутри обработчика сохранения"""
        self.assertFalse(self.service.is_dirty)

        self.service.set('Sync', 'interval_minutes', 5)

        self.assertTrue(self.service.is_dirty)
        self.assertEqual([c.label for c in self.service.changes()], ['Интервал запуска'])
        self.assertEqual(self.service.changed_groups(), ['sync_cycle'])

    def test_writing_the_same_value_is_not_a_change(self):
        self.service.set('Sync', 'interval_minutes', 2)

        self.assertFalse(self.service.is_dirty)

    def test_discard_changes(self):
        self.service.set('Sync', 'interval_minutes', 9)
        self.service.discard_changes()

        self.assertFalse(self.service.is_dirty)
        self.assertEqual(self.service.get_int('Sync', 'interval_minutes'), 2)

    def test_save_keeps_comments_and_makes_backup(self):
        self.service.set('Sync', 'interval_minutes', 6)
        backup = self.service.save()

        written = self.config_path.read_text(encoding='utf-8-sig')

        self.assertIn('# Интервал синхронизации записей в минутах', written)
        self.assertIn('interval_minutes = 6', written)
        self.assertFalse(self.service.is_dirty)

        self.assertTrue(backup.exists())
        self.assertIn('interval_minutes = 2', backup.read_text(encoding='utf-8-sig'))

    def test_save_changes_only_the_edited_line(self):
        original = self.config_path.read_text(encoding='utf-8-sig').splitlines()

        self.service.set('Queue', 'max_retry_attempts', 5)
        self.service.save()

        written = self.config_path.read_text(encoding='utf-8-sig').splitlines()
        differing = [i for i, (a, b) in enumerate(zip(original, written)) if a != b]

        self.assertEqual(len(original), len(written))
        self.assertEqual(len(differing), 1)
        self.assertIn('max_retry_attempts', written[differing[0]])

    def test_service_reads_what_gui_wrote(self):
        """Записанное приложением значение видно тем же разбором, что и у службы"""
        self.service.set('Sync', 'batch_size', 120)
        self.service.set('Advanced', 'dry_run', True)
        self.service.save()

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.config_path, encoding='utf-8-sig')

        self.assertEqual(parser.getint('Sync', 'batch_size'), 120)
        self.assertIs(parser.getboolean('Advanced', 'dry_run'), True)

    def test_validate_finds_broken_value(self):
        self.service.set('Sync', 'filial_id', 99)

        self.assertTrue(
            any('от 1 до 10' in problem for problem in self.service.validate()),
            self.service.validate()
        )

    def test_paths_follow_configuration(self):
        self.assertEqual(self.service.queue_file_path(), self.workdir / 'queue.json')
        self.assertEqual(self.service.log_dir_path(), self.workdir / 'logs')

        self.service.set('Queue', 'persistence_file', 'data/queue.json')
        self.assertEqual(self.service.queue_file_path(), self.workdir / 'data' / 'queue.json')

    def test_absolute_path_in_configuration_is_used_as_is(self):
        absolute = Path(self.workdir.anchor) / 'var' / 'log' / 'ident'
        self.service.set('Logging', 'log_dir', str(absolute))

        self.assertEqual(self.service.log_dir_path(), absolute)

    def test_external_change_of_the_file_is_noticed(self):
        """
        Хранилище пишет файл целиком из снимка, сделанного при открытии,
        поэтому правку, сделанную другим процессом, оно затрёт. Окно должно
        узнать об этом до записи.
        """
        self.assertFalse(self.service.changed_on_disk())

        # Кто-то ещё дописал строку в конфигурацию
        text = self.config_path.read_text(encoding='utf-8-sig')
        self.config_path.write_text(text + '\n; правка со стороны\n', encoding='utf-8')

        self.assertTrue(self.service.changed_on_disk())

    def test_saving_resets_the_file_signature(self):
        text = self.config_path.read_text(encoding='utf-8-sig')
        self.config_path.write_text(text + '\n; правка со стороны\n', encoding='utf-8')
        self.assertTrue(self.service.changed_on_disk())

        self.service.set('Sync', 'batch_size', 77)
        self.service.save()

        self.assertFalse(self.service.changed_on_disk())


if __name__ == '__main__':
    unittest.main()
