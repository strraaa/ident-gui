"""
Проверки записи ini без потери комментариев.

Тесты написаны на unittest, а не на pytest: их можно запустить и без
установленных зависимостей — `python -m unittest discover tests`.
"""

import configparser
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.core.ini_document import IniDocument  # noqa: E402

SAMPLE = """\
# Конфигурация синхронизатора
# Вторая строка шапки

[Database]
# Адрес SQL Server
server = SERVER\\SQLEXPRESS
port = 1433
; пароль шифруется при первом запуске
password = DPAPI:qqq

[Sync]
# Интервал не влияет на планировщик
interval_minutes = 2
batch_size = 50
"""


class IniDocumentReadTests(unittest.TestCase):
    """Чтение значений совпадает с тем, как их видит служба"""

    def test_reads_values(self):
        doc = IniDocument.loads(SAMPLE)

        self.assertEqual(doc.get('Database', 'server'), 'SERVER\\SQLEXPRESS')
        self.assertEqual(doc.get('Sync', 'interval_minutes'), '2')

    def test_missing_key_returns_fallback(self):
        doc = IniDocument.loads(SAMPLE)

        self.assertIsNone(doc.get('Sync', 'нет_такого'))
        self.assertEqual(doc.get('Sync', 'нет_такого', 'по умолчанию'), 'по умолчанию')
        self.assertEqual(doc.get('Нет секции', 'ключ', ''), '')

    def test_sections_and_options_keep_file_order(self):
        doc = IniDocument.loads(SAMPLE)

        self.assertEqual(doc.sections(), ['Database', 'Sync'])
        self.assertEqual(doc.options('Sync'), ['interval_minutes', 'batch_size'])

    def test_key_lookup_ignores_case_section_does_not(self):
        doc = IniDocument.loads(SAMPLE)

        self.assertEqual(doc.get('Sync', 'INTERVAL_MINUTES'), '2')
        self.assertIsNone(doc.get('sync', 'interval_minutes'))

    def test_hash_inside_value_is_not_a_comment(self):
        """Служба читает конфиг без inline-комментариев — повторяем поведение"""
        doc = IniDocument.loads('[bitrix]\nwebhook_url = https://p.ru/rest/1/k/ # рабочий\n')

        self.assertEqual(doc.get('bitrix', 'webhook_url'), 'https://p.ru/rest/1/k/ # рабочий')

    def test_matches_configparser_on_real_example(self):
        """Значения совпадают с configparser на боевом образце конфигурации"""
        example = Path(__file__).resolve().parent.parent / 'config.example.ini'

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(example, encoding='utf-8-sig')

        doc = IniDocument.load(example)

        self.assertEqual(doc.sections(), parser.sections())

        for section in parser.sections():
            for key, expected in parser.items(section):
                self.assertEqual(
                    doc.get(section, key), expected,
                    f'Расхождение в [{section}].{key}'
                )


class IniDocumentWriteTests(unittest.TestCase):
    """Запись меняет только то, что просили изменить"""

    def test_comments_and_order_survive_edit(self):
        doc = IniDocument.loads(SAMPLE)
        doc.set('Sync', 'interval_minutes', 5)

        result = doc.dumps()

        self.assertIn('# Конфигурация синхронизатора', result)
        self.assertIn('# Интервал не влияет на планировщик', result)
        self.assertIn('; пароль шифруется при первом запуске', result)
        self.assertIn('interval_minutes = 5', result)
        self.assertNotIn('interval_minutes = 2', result)

    def test_edit_changes_exactly_one_line(self):
        doc = IniDocument.loads(SAMPLE)
        doc.set('Database', 'port', 1435)

        before = SAMPLE.splitlines()
        after = doc.dumps().splitlines()

        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(differing, [6])

    def test_untouched_file_is_byte_identical(self):
        doc = IniDocument.loads(SAMPLE)

        self.assertEqual(doc.dumps(), SAMPLE)

    def test_real_example_survives_single_edit(self):
        """На боевом образце правка одного ключа не задевает 92 строки комментариев"""
        example = Path(__file__).resolve().parent.parent / 'config.example.ini'
        original = example.read_text(encoding='utf-8-sig')

        doc = IniDocument.load(example)
        doc.set('Sync', 'batch_size', 99)

        before = original.splitlines()
        after = doc.dumps().splitlines()

        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(differing), 1)
        self.assertIn('batch_size', after[differing[0]])

    def test_new_key_lands_in_its_section(self):
        doc = IniDocument.loads(SAMPLE)
        doc.set('Database', 'database', 'IdentDB')

        lines = doc.dumps().splitlines()
        position = lines.index('database = IdentDB')

        self.assertEqual(lines[position - 1], 'password = DPAPI:qqq')
        self.assertEqual(lines[position + 1], '')
        self.assertEqual(doc.get('Database', 'database'), 'IdentDB')

    def test_new_key_keeps_section_spacing_style(self):
        doc = IniDocument.loads('[Sync]\nbatch_size=50\n')
        doc.set('Sync', 'initial_days', 7)

        self.assertIn('initial_days=7', doc.dumps())

    def test_new_section_appended_to_the_end(self):
        doc = IniDocument.loads(SAMPLE)
        doc.set('Advanced', 'dry_run', 'False')

        lines = doc.dumps().splitlines()

        self.assertEqual(lines[-2:], ['[Advanced]', 'dry_run = False'])
        self.assertEqual(doc.get('Advanced', 'dry_run'), 'False')

    def test_unknown_sections_are_left_alone(self):
        source = SAMPLE + '\n[Ручная секция]\n# правил оператор\nчто_то = значение\n'

        doc = IniDocument.loads(source)
        doc.set('Sync', 'batch_size', 10)

        result = doc.dumps()

        self.assertIn('[Ручная секция]', result)
        self.assertIn('# правил оператор', result)
        self.assertIn('что_то = значение', result)

    def test_remove_option_keeps_neighbouring_comments(self):
        doc = IniDocument.loads(SAMPLE)

        self.assertTrue(doc.remove_option('Database', 'password'))
        self.assertFalse(doc.remove_option('Database', 'password'))

        result = doc.dumps()

        self.assertNotIn('password', result)
        self.assertIn('; пароль шифруется при первом запуске', result)

    def test_rejects_multiline_value(self):
        doc = IniDocument.loads(SAMPLE)

        with self.assertRaises(ValueError):
            doc.set('Sync', 'batch_size', 'первая\nвторая')


class IniDocumentFormatTests(unittest.TestCase):
    """Перевод строки, BOM и продолжения значений"""

    def test_crlf_is_preserved(self):
        doc = IniDocument.loads('[Sync]\r\nbatch_size = 50\r\n')
        doc.set('Sync', 'batch_size', 60)

        self.assertEqual(doc.dumps(), '[Sync]\r\nbatch_size = 60\r\n')

    def test_bom_is_preserved(self):
        doc = IniDocument.loads('﻿[Sync]\nbatch_size = 50\n')
        doc.set('Sync', 'batch_size', 60)

        self.assertTrue(doc.dumps().startswith('﻿'))

    def test_file_without_trailing_newline_stays_that_way(self):
        doc = IniDocument.loads('[Sync]\nbatch_size = 50')
        doc.set('Sync', 'batch_size', 60)

        self.assertEqual(doc.dumps(), '[Sync]\nbatch_size = 60')

    def test_multiline_value_is_read_and_flattened_on_write(self):
        doc = IniDocument.loads('[deal_stages]\nprotected = WON\n    LOSE\nfinal = WON\n')

        self.assertEqual(doc.get('deal_stages', 'protected'), 'WON\nLOSE')

        doc.set('deal_stages', 'protected', 'WON,LOSE')
        lines = doc.dumps().splitlines()

        self.assertEqual(lines, ['[deal_stages]', 'protected = WON,LOSE', 'final = WON'])

    def test_duplicate_key_keeps_the_first_like_configparser(self):
        doc = IniDocument.loads('[Sync]\nbatch_size = 50\nbatch_size = 70\n')

        self.assertEqual(doc.get('Sync', 'batch_size'), '50')

        doc.set('Sync', 'batch_size', 60)
        self.assertEqual(doc.dumps().splitlines()[1], 'batch_size = 60')


class IniDocumentFileTests(unittest.TestCase):
    """Работа с файлом на диске"""

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(SAMPLE, encoding='utf-8', newline='')

            doc = IniDocument.load(path)
            doc.set('Sync', 'batch_size', 25)
            doc.save(path)

            self.assertEqual(IniDocument.load(path).get('Sync', 'batch_size'), '25')
            self.assertIn('# Интервал не влияет на планировщик', path.read_text(encoding='utf-8'))

    def test_save_does_not_leave_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_text(SAMPLE, encoding='utf-8', newline='')

            doc = IniDocument.load(path)
            doc.save(path)

            self.assertEqual([p.name for p in Path(tmp).iterdir()], ['config.ini'])

    def test_file_with_bom_reads_and_keeps_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.ini'
            path.write_bytes('﻿'.encode('utf-8') + SAMPLE.encode('utf-8'))

            doc = IniDocument.load(path)
            self.assertEqual(doc.get('Sync', 'batch_size'), '50')

            doc.set('Sync', 'batch_size', 30)
            doc.save(path)

            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))


if __name__ == '__main__':
    unittest.main()
