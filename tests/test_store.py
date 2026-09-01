"""
Проверки хранилища настроек и перекрёстной валидации.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.core.ini_document import IniDocument  # noqa: E402
from gui.core.store import SettingsStore  # noqa: E402

CONFIG = """\
[Database]
server = localhost
port = 1433
database = IdentDB
username = ident_user
password = DPAPI:xxx

[bitrix]
webhook_url = https://portal.bitrix24.ru/rest/1/key/

[deal_fields]
# Ключевое поле
ident_id = UF_CRM_1

[deal_defaults]
default_stage_id = NEW

[deal_stages]
mapping = Запланирован:NEW,Отменен:LOSE
final = WON,LOSE
protected = EXECUTING

[Sync]
interval_minutes = 2
"""


def store_from(text: str = CONFIG) -> SettingsStore:
    return SettingsStore(IniDocument.loads(text), encryptor=lambda s: 'DPAPI:' + s)


class ReadingTests(unittest.TestCase):
    """Чтение значений и умолчаний"""

    def test_reads_stored_value(self):
        store = store_from()

        self.assertEqual(store.raw('Sync', 'interval_minutes'), '2')
        self.assertEqual(store.value('Sync', 'interval_minutes'), 2)

    def test_falls_back_to_registry_default(self):
        """Ключа в файле нет — берётся умолчание реестра, а не пустая строка"""
        store = store_from()

        self.assertEqual(store.value('Sync', 'batch_size'), 50)
        self.assertEqual(store.value('Queue', 'max_retry_attempts'), 3)
        self.assertIs(store.value('Logging', 'mask_personal_data'), True)

    def test_typed_values(self):
        store = store_from()

        self.assertEqual(store.value('deal_stages', 'final'), ['WON', 'LOSE'])
        self.assertEqual(
            store.value('deal_stages', 'mapping'),
            [('Запланирован', 'NEW'), ('Отменен', 'LOSE')]
        )

    def test_detects_encrypted_value(self):
        store = store_from()

        self.assertTrue(store.is_encrypted('Database', 'password'))
        self.assertFalse(store.is_encrypted('Database', 'username'))

    def test_lists_options_missing_from_the_registry(self):
        store = store_from(CONFIG + '\n[Ручная секция]\nчто_то = значение\n')

        self.assertIn(('Ручная секция', 'что_то'), store.unknown_options())


class ChangeTrackingTests(unittest.TestCase):
    """Признак несохранённого работает до сохранения, а не после"""

    def test_dirty_right_after_edit(self):
        store = store_from()

        self.assertFalse(store.is_dirty)
        store.set_value('Sync', 'interval_minutes', 5)
        self.assertTrue(store.is_dirty)

    def test_writing_the_same_value_is_not_a_change(self):
        store = store_from()
        store.set_value('Sync', 'interval_minutes', 2)

        self.assertFalse(store.is_dirty)
        self.assertEqual(store.changes(), [])

    def test_reset_discards_edits(self):
        store = store_from()
        store.set_value('Sync', 'interval_minutes', 9)
        store.reset()

        self.assertFalse(store.is_dirty)
        self.assertEqual(store.raw('Sync', 'interval_minutes'), '2')

    def test_change_describes_itself(self):
        store = store_from()
        store.set_value('Sync', 'interval_minutes', 5)

        change = store.changes()[0]

        self.assertEqual(change.label, 'Интервал запуска')
        self.assertEqual(change.describe(), 'Интервал запуска: 2 → 5')

    def test_secret_change_hides_values(self):
        store = store_from()
        store.set_secret('Database', 'password', 'новый-пароль')

        described = store.changes()[0].describe()

        self.assertNotIn('новый-пароль', described)
        self.assertNotIn('DPAPI:', described)
        self.assertIn('задано', described)

    def test_changed_groups_point_at_the_page(self):
        store = store_from()
        store.set_value('Sync', 'interval_minutes', 5)
        store.set_value('Database', 'port', 1435)

        self.assertEqual(store.changed_groups(), ['db', 'sync_cycle'])


class SecretTests(unittest.TestCase):
    """Секреты шифруются на входе и никогда не расшифровываются"""

    def test_secret_is_encrypted_before_storing(self):
        store = store_from()
        store.set_secret('Database', 'password', 'тайна')

        self.assertEqual(store.raw('Database', 'password'), 'DPAPI:тайна')

    def test_empty_secret_clears_the_value(self):
        store = store_from()
        store.set_secret('Database', 'password', '')

        self.assertEqual(store.raw('Database', 'password'), '')

    def test_refuses_to_store_secret_without_encryption(self):
        store = SettingsStore(IniDocument.loads(CONFIG), encryptor=None)

        with self.assertRaises(RuntimeError):
            store.set_secret('Database', 'password', 'тайна')

    def test_encryption_key_is_stored_as_is(self):
        """Ключ шифрования сам собой не шифруется"""
        store = store_from()
        store.set_secret('Security', 'encryption_key', 'abc123')

        self.assertEqual(store.raw('Security', 'encryption_key'), 'abc123')


class ValidationTests(unittest.TestCase):
    """Проверки конфигурации"""

    def test_clean_config_has_no_problems(self):
        store = store_from()

        self.assertEqual(store.problems(), [])

    def test_reports_missing_required_value(self):
        store = store_from(CONFIG.replace('database = IdentDB', 'database = '))

        self.assertIn('Не заполнено обязательное поле «База данных»', store.problems())

    def test_reports_bad_number(self):
        store = store_from()
        store.set_raw('Sync', 'interval_minutes', 'два')

        self.assertIn('«Интервал запуска»: должно быть числом, указано «два»', store.problems())

    def test_reports_number_outside_range(self):
        store = store_from()
        store.set_raw('Sync', 'filial_id', '42')

        self.assertTrue(any('от 1 до 10' in p for p in store.problems()))

    def test_reports_field_used_twice(self):
        store = store_from()
        store.set_raw('deal_fields', 'card_number', 'UF_CRM_1')

        self.assertTrue(
            any('назначено дважды' in p for p in store.problems()),
            store.problems()
        )

    def test_same_field_on_different_entities_is_fine(self):
        """Поле сделки и поле контакта могут совпасть по коду — это разные сущности"""
        store = store_from()
        store.set_raw('contact_fields', 'card_number', 'UF_CRM_1')

        self.assertEqual(store.problems(), [])

    def test_reports_stage_both_final_and_protected(self):
        store = store_from()
        store.set_raw('deal_stages', 'protected', 'EXECUTING,WON')

        self.assertTrue(any('одновременно финальные и защищённые' in p for p in store.problems()))

    def test_reports_default_filial_outside_the_filter(self):
        store = store_from()
        store.set_raw('FilialFilter', 'enabled_filial_ids', '4,5')
        store.set_raw('FilialFilter', 'default_filial_id', '7')

        self.assertTrue(any('не входит в список' in p for p in store.problems()))

    def test_default_filial_zero_means_skip_and_is_not_a_problem(self):
        store = store_from()
        store.set_raw('FilialFilter', 'enabled_filial_ids', '4,5')
        store.set_raw('FilialFilter', 'default_filial_id', '0')

        self.assertEqual(store.problems(), [])

    def test_stored_problems_ignore_unsaved_edits(self):
        store = store_from()
        store.set_raw('Sync', 'interval_minutes', 'два')

        self.assertEqual(store.stored_problems(), [])
        self.assertNotEqual(store.problems(), [])


class SaveTests(unittest.TestCase):
    """Запись на диск"""

    def test_save_keeps_comments_and_writes_only_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(CONFIG, encoding='utf-8', newline='')

            store = SettingsStore.load(path)
            store.set_value('Sync', 'interval_minutes', 7)
            store.save(path)

            written = path.read_text(encoding='utf-8')

            self.assertIn('# Ключевое поле', written)
            self.assertIn('interval_minutes = 7', written)
            self.assertEqual(len(written.splitlines()), len(CONFIG.splitlines()))

    def test_save_creates_timestamped_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(CONFIG, encoding='utf-8', newline='')

            store = SettingsStore.load(path)
            store.set_value('Sync', 'interval_minutes', 7)
            backup = store.save(path)

            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding='utf-8'), CONFIG)
            self.assertTrue(backup.name.startswith('config.ini.'))
            self.assertTrue(backup.name.endswith('.bak'))

    def test_old_backups_are_trimmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(CONFIG, encoding='utf-8', newline='')

            for index in range(5):
                store = SettingsStore.load(path)
                store.set_value('Sync', 'interval_minutes', index + 3)
                store.save(path, backup_limit=2)

            backups = list(path.parent.glob('config.ini.*.bak'))
            self.assertLessEqual(len(backups), 2)

    def test_store_is_clean_after_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(CONFIG, encoding='utf-8', newline='')

            store = SettingsStore.load(path)
            store.set_value('Sync', 'interval_minutes', 7)
            store.save(path)

            self.assertFalse(store.is_dirty)
            self.assertEqual(store.raw('Sync', 'interval_minutes'), '7')

    def test_new_key_is_added_to_its_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(CONFIG, encoding='utf-8', newline='')

            store = SettingsStore.load(path)
            store.set_value('Sync', 'batch_size', 25)
            store.save(path)

            written = path.read_text(encoding='utf-8').splitlines()
            position = written.index('batch_size = 25')

            self.assertEqual(written[position - 1], 'interval_minutes = 2')


if __name__ == '__main__':
    unittest.main()
