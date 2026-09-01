"""
Проверки реестра настроек.

Главное здесь — сверка с config.example.ini: ключ, появившийся в образце,
но забытый в реестре, не должен пройти незамеченным.
"""

import configparser
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.core import schema  # noqa: E402
from gui.core.types import Flag, Int, Secret  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / 'config.example.ini'

# Ключи, которые читает код, но которых в образце нет.
# Список умышленно короткий: каждое расхождение — повод дописать образец.
EXTRA_IDENTS = {
    ('bitrix', 'request_timeout'),
    ('deal_fields', 'cancel_reason'),
}


def example_idents():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(EXAMPLE, encoding='utf-8-sig')
    return {(section, key) for section in parser.sections() for key in parser[section]}


class RegistryCoverageTests(unittest.TestCase):
    """Реестр и образец конфигурации описывают одно и то же"""

    def test_every_example_key_is_declared(self):
        declared = {s.ident for s in schema.SETTINGS}
        missing = example_idents() - declared

        self.assertEqual(
            missing, set(),
            'Ключи есть в config.example.ini, но не описаны в реестре: '
            + ', '.join(f'[{s}].{k}' for s, k in sorted(missing))
        )

    def test_registry_has_no_invented_keys(self):
        declared = {s.ident for s in schema.SETTINGS}
        unknown = declared - example_idents() - EXTRA_IDENTS

        self.assertEqual(
            unknown, set(),
            'Ключи описаны в реестре, но их нет ни в образце, ни в списке исключений: '
            + ', '.join(f'[{s}].{k}' for s, k in sorted(unknown))
        )

    def test_no_duplicate_declarations(self):
        idents = [s.ident for s in schema.SETTINGS]
        duplicates = {ident for ident in idents if idents.count(ident) > 1}

        self.assertEqual(duplicates, set())

    def test_every_setting_belongs_to_a_known_group(self):
        for setting in schema.SETTINGS:
            self.assertIsNotNone(
                schema.group(setting.group),
                f'[{setting.section}].{setting.key} ссылается на группу «{setting.group}»'
            )

    def test_every_group_has_settings(self):
        for group in schema.GROUPS:
            self.assertTrue(
                schema.settings_of_group(group.key),
                f'Группа «{group.key}» объявлена, но пуста'
            )


class RegistryContentTests(unittest.TestCase):
    """Содержательные требования к описаниям"""

    def test_labels_are_filled(self):
        for setting in schema.SETTINGS:
            self.assertTrue(setting.label.strip(), f'{setting.ident} без подписи')

    def test_defaults_match_declared_type(self):
        for setting in schema.SETTINGS:
            if isinstance(setting.type, Int):
                self.assertIsInstance(setting.default, int, f'{setting.ident}')
            elif isinstance(setting.type, Flag):
                self.assertIsInstance(setting.default, bool, f'{setting.ident}')

    def test_integer_defaults_are_inside_their_range(self):
        for setting in schema.SETTINGS:
            if isinstance(setting.type, Int):
                self.assertGreaterEqual(setting.default, setting.type.minimum, f'{setting.ident}')
                self.assertLessEqual(setting.default, setting.type.maximum, f'{setting.ident}')

    def test_secrets_have_no_default_value(self):
        """Секрет с непустым значением по умолчанию утёк бы в боевой конфиг"""
        for setting in schema.SETTINGS:
            if isinstance(setting.type, Secret):
                self.assertEqual(setting.default, '', f'{setting.ident}')

    def test_every_secret_is_encrypted_by_the_service(self):
        """
        Иначе приложение записало бы пароль открытым текстом, считая,
        что ConfigManager его зашифрует.

        Обратное включение не проверяем: в ENCRYPTED_FIELDS есть
        устаревший псевдоним секции — ('Bitrix24', 'token').
        """
        from src.config.config_manager_v2 import ConfigManager

        encrypted = set(ConfigManager.ENCRYPTED_FIELDS)
        for ident in schema.secret_idents():
            self.assertIn(ident, encrypted)

    def test_encryption_key_is_hidden_but_not_encrypted(self):
        """Ключ шифрования нельзя зашифровать им же самим"""
        self.assertIn(('Security', 'encryption_key'), schema.hidden_idents())
        self.assertNotIn(('Security', 'encryption_key'), schema.secret_idents())

    def test_pages_cover_all_settings(self):
        pages = (Page for Page in (
            schema.Page.CONNECTIONS, schema.Page.FIELDS, schema.Page.STAGES,
            schema.Page.SYNC, schema.Page.ADVANCED
        ))

        covered = set()
        for page in pages:
            covered.update(s.ident for s in schema.settings_of_page(page))

        self.assertEqual(covered, {s.ident for s in schema.SETTINGS})


if __name__ == '__main__':
    unittest.main()
