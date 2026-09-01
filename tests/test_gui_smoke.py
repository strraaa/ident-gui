"""
Сборка окна и вкладок вживую.

Тест не проверяет внешний вид — он ловит поломки стыка между страницами и
службами конфигурации: переименованный метод, изменившуюся сигнатуру,
исчезнувший атрибут. Такое иначе обнаруживается только запуском на Windows.

Пропускается, если PySide6 не установлен — на машине сборки службы его нет.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

EXAMPLE = Path(__file__).resolve().parent.parent / 'config.example.ini'


@unittest.skipIf(QApplication is None, 'PySide6 не установлен')
class MainWindowSmokeTests(unittest.TestCase):
    """Окно строится, вкладки читают конфигурацию, сохранение доходит до файла"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.main_window import MainWindow
        from gui.services.paths import Workspace

        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.config_path = self.workdir / 'config.ini'
        self.config_path.write_bytes(EXAMPLE.read_bytes())

        self.window = MainWindow(Workspace(self.workdir))

    def tearDown(self):
        self.window.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------------

    def test_all_tabs_are_built(self):
        titles = [tab.title for tab in self.window.all_tabs]

        self.assertEqual(
            titles,
            ['Состояние', 'Подключения', 'Поля', 'Стадии', 'Синхронизация', 'Очередь', 'Логи']
        )

    def test_tabs_load_values_from_configuration(self):
        self.assertEqual(self.window.sync_tab.spin_interval.value(), 2)
        self.assertEqual(self.window.sync_tab.spin_batch.value(), 50)
        self.assertEqual(self.window.connections_tab.txt_db_name.text(), 'IdentDB')
        self.assertEqual(self.window.connections_tab.spin_db_port.value(), 1433)

    def test_stage_mapping_is_parsed_into_rows(self):
        table = self.window.stages_tab.table

        self.assertEqual(table.rowCount(), 6)
        self.assertEqual(table.cellWidget(0, 0).currentText(), 'Запланирован')
        self.assertEqual(self.window.stages_tab._row_stage(0), 'NEW')

    def test_clean_configuration_has_no_complaints(self):
        self.assertEqual(self.window._collect_problems(), [])

    def test_edit_and_save_reaches_the_file(self):
        self.window.sync_tab.spin_interval.setValue(9)

        for tab in self.window.all_tabs:
            if tab.is_settings_tab:
                tab.apply_to_config()

        self.assertTrue(self.window.config.is_dirty)

        backup = self.window.config.save()

        written = self.config_path.read_text(encoding='utf-8-sig')

        self.assertIn('interval_minutes = 9', written)
        self.assertIn('# Интервал синхронизации записей в минутах', written)
        self.assertTrue(backup.exists())
        self.assertFalse(self.window.config.is_dirty)

    def test_field_editors_show_configured_codes(self):
        fields_tab = self.window.fields_tab
        codes = [
            fields_tab._editor_value(editor)
            for editor in fields_tab._editors.values()
        ]

        self.assertIn('UF_CRM_1769072841035', codes)


if __name__ == '__main__':
    unittest.main()
