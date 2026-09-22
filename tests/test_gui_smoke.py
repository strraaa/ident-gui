"""
Сборка окна и страниц вживую.

Тест не проверяет внешний вид — он ловит поломки стыка между страницами и
службами: переименованный метод, изменившуюся сигнатуру, исчезнувший атрибут.
Такое иначе обнаруживается только запуском на Windows.

Пропускается, если PySide6 не установлен.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

EXAMPLE = Path(__file__).resolve().parent.parent / 'config.example.ini'


@unittest.skipIf(QApplication is None, 'PySide6 не установлен')
class MainWindowSmokeTests(unittest.TestCase):
    """Окно строится, страницы читают конфигурацию, сохранение доходит до файла"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.main_window import MainWindow
        from gui.services.app_settings import AppSettings
        from gui.services.paths import Workspace

        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.config_path = self.workdir / 'config.ini'
        self.config_path.write_bytes(EXAMPLE.read_bytes())

        # Собственный файл настроек, чтобы не писать в реестр машины
        self.app_settings = AppSettings(
            QSettings(str(self.workdir / 'app.ini'), QSettings.IniFormat)
        )

        self._silence_dialogs()

        self.window = MainWindow(Workspace(self.workdir), self.app_settings)

    def tearDown(self):
        self.window.close()
        self._tmp.cleanup()

    def _silence_dialogs(self):
        """
        Модальные окна в тестах отвечают сами.

        Окно спрашивает о несохранённых правках при закрытии, поэтому без
        заглушки прогон встаёт на первом же тесте, который что-то поменял.
        """
        from PySide6.QtWidgets import QMessageBox

        self.dialogs = []
        self.answer = QMessageBox.Yes

        def question(parent, title, text, *args, **kwargs):
            self.dialogs.append(('question', title, text))
            return self.answer

        def notice(kind):
            def handler(parent, title, text, *args, **kwargs):
                self.dialogs.append((kind, title, text))
                return QMessageBox.Ok
            return handler

        for name, handler in (
            ('question', question),
            ('warning', notice('warning')),
            ('information', notice('information')),
            ('critical', notice('critical')),
        ):
            patcher = mock.patch.object(QMessageBox, name, staticmethod(handler))
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------
    # Каркас
    # ------------------------------------------------------------------

    def test_all_pages_are_built(self):
        titles = [page.title for page in self.window.pages]

        self.assertEqual(
            titles,
            ['Обзор', 'Очередь', 'Журнал', 'Подключения', 'Поля',
             'Стадии и воронка', 'Синхронизация']
        )

    def test_navigation_groups_pages_by_section(self):
        self.assertEqual(
            self.window.nav.keys(),
            ['overview', 'queue', 'logs', 'connections', 'fields', 'stages', 'sync']
        )

    def test_selecting_a_page_switches_the_stack(self):
        self.window.nav.select('fields')

        self.assertIs(self.window.current_page(), self.window.fields_page)
        self.assertEqual(self.app_settings.page(), 'fields')

    def test_settings_pages_are_marked_as_such(self):
        settings_keys = {page.key for page in self.window.pages if page.is_settings}

        self.assertEqual(settings_keys, {'connections', 'fields', 'stages', 'sync'})

    # ------------------------------------------------------------------
    # Отслеживание правок
    # ------------------------------------------------------------------

    def _settle(self):
        """Дожидается отложенного пересчёта состояния после правки"""
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(self.window._edit_timer.interval() + 150, loop.quit)
        loop.exec()

    def test_editing_a_field_enables_saving(self):
        """
        Главный регресс приложения: редакторы не были связаны с хранилищем,
        поэтому признак несохранённого оставался ложным, а кнопка «Сохранить»
        ждала именно его — и не включалась никогда.
        """
        self.assertFalse(self.window.btn_save.isEnabled())

        self.window.connections_page.txt_db_server.setText('SQL-01\\IDENT')
        self.window.connections_page.txt_db_server.textEdited.emit('SQL-01\\IDENT')
        self._settle()

        self.assertTrue(self.window.config.is_dirty)
        self.assertTrue(self.window.btn_save.isEnabled())
        self.assertTrue(self.window.btn_reload.isEnabled())

    def test_editing_marks_the_page_in_navigation(self):
        self.window.sync_page.spin_batch.setValue(75)
        self._settle()

        self.assertTrue(self.window.nav.is_dirty('sync'))
        self.assertFalse(self.window.nav.is_dirty('connections'))

    def test_typed_and_erased_password_does_not_reach_configuration(self):
        """Пустое поле пароля означает «оставить прежний», а не «записать набранное»"""
        field = self.window.connections_page.txt_db_password

        field.setText('partial')
        field.textEdited.emit('partial')
        self._settle()

        field.clear()
        field.textEdited.emit('')
        self._settle()

        changed = {change.key for change in self.window.config.changes()}
        self.assertNotIn('password', changed)

    def test_removing_a_mapping_row_is_a_change(self):
        table = self.window.stages_page.table
        table.selectRow(0)
        self.window.stages_page._remove_selected_row()
        self._settle()

        self.assertTrue(self.window.config.is_dirty)

    # ------------------------------------------------------------------
    # Конфигурация
    # ------------------------------------------------------------------

    def test_pages_load_values_from_configuration(self):
        self.assertEqual(self.window.sync_page.spin_interval.value(), 2)
        self.assertEqual(self.window.sync_page.spin_batch.value(), 50)
        self.assertEqual(self.window.connections_page.txt_db_name.text(), 'IdentDB')
        self.assertEqual(self.window.connections_page.spin_db_port.value(), 1433)

    def test_stage_mapping_is_parsed_into_rows(self):
        table = self.window.stages_page.table

        self.assertEqual(table.rowCount(), 6)
        self.assertEqual(table.cellWidget(0, 0).currentText(), 'Запланирован')
        self.assertEqual(self.window.stages_page._row_stage(0), 'NEW')

    def test_overview_fills_in_when_it_becomes_current(self):
        """Страница обновляется по переходу на неё, а не только по таймеру"""
        self.window.show()
        self.window.nav.select('overview')
        self.window.overview_page.refresh()

        self.assertEqual(
            self.window.overview_page.lbl_config_path.text(),
            str(self.config_path)
        )
        self.assertEqual(
            self.window.overview_page.lbl_config_problems.text(),
            'Проблем не обнаружено'
        )

    def test_clean_configuration_has_no_complaints(self):
        self.assertEqual(self.window._collect_problems(), [])

    def test_field_editors_show_configured_codes(self):
        codes = [
            self.window.fields_page._editor_value(editor)
            for editor in self.window.fields_page._editors.values()
        ]

        self.assertIn('UF_CRM_1769072841035', codes)

    # ------------------------------------------------------------------
    # Несохранённые правки
    # ------------------------------------------------------------------

    def test_edit_marks_the_page_in_the_navigation(self):
        """Пометка появляется на той странице, где правили"""
        self.window.sync_page.spin_interval.setValue(9)
        self.window._apply_pages()
        self.window._update_state()

        self.assertTrue(self.window.nav.is_dirty('sync'))
        self.assertFalse(self.window.nav.is_dirty('connections'))

    def test_save_button_is_off_until_something_changes(self):
        self.window._update_state()
        self.assertFalse(self.window.btn_save.isEnabled())

        self.window.sync_page.spin_interval.setValue(9)
        self.window._apply_pages()
        self.window._update_state()

        self.assertTrue(self.window.btn_save.isEnabled())

    def test_footer_counts_unsaved_changes(self):
        self.window.sync_page.spin_interval.setValue(9)
        self.window.sync_page.spin_batch.setValue(75)
        self.window._apply_pages()
        self.window._update_state()

        self.assertIn('Несохранённых изменений: 2', self.window.lbl_footer.text())

    def test_edit_and_save_reaches_the_file(self):
        self.window.sync_page.spin_interval.setValue(9)
        self.window._apply_pages()

        self.assertTrue(self.window.config.is_dirty)

        backup_path = self.window.config.save()
        written = self.config_path.read_text(encoding='utf-8-sig')

        self.assertIn('interval_minutes = 9', written)
        self.assertIn('# Интервал синхронизации записей в минутах', written)
        self.assertTrue(backup_path.exists())
        self.assertFalse(self.window.config.is_dirty)

    # ------------------------------------------------------------------
    # Сообщения и оформление
    # ------------------------------------------------------------------

    def test_missing_configuration_is_reported_in_a_banner(self):
        """Раньше это было модальное окно с вопросом"""
        from gui.main_window import MainWindow
        from gui.services.app_settings import AppSettings
        from gui.services.paths import Workspace

        with tempfile.TemporaryDirectory() as empty:
            settings = AppSettings(QSettings(str(Path(empty) / 'app.ini'), QSettings.IniFormat))
            window = MainWindow(Workspace(Path(empty)), settings)

            self.assertIn('нет файла config.ini', window.banner.text)
            self.assertEqual(window.banner.tone, 'warning')
            self.assertFalse(window.connections_page.isEnabled())

            window.close()

    def test_application_uses_dark_theme_only(self):
        self.assertEqual(self.app_settings.theme(), 'dark')
        self.app_settings.set_theme('light')
        self.assertEqual(self.app_settings.theme(), 'dark')

    def test_window_geometry_is_remembered(self):
        self.window.resize(1000, 700)
        self.window.close()

        self.assertIsNotNone(self.app_settings.geometry())


@unittest.skipIf(QApplication is None, 'PySide6 не установлен')
class ThemeTests(unittest.TestCase):
    """Оформление приложения собирается для тёмной темы"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_colour_is_a_hex_value(self):
        from gui.theme.tokens import DARK

        for name, value in DARK.as_dict().items():
            self.assertRegex(value, r'^#[0-9A-Fa-f]{6}$', f'{DARK.name}.{name}')

    def test_stylesheet_uses_the_dark_palette(self):
        from gui.theme.stylesheet import build_stylesheet
        from gui.theme.tokens import DARK

        dark = build_stylesheet(DARK)

        self.assertIn(DARK.accent, dark)

    def test_apply_theme_returns_the_applied_name(self):
        from gui.theme import apply_theme

        self.assertEqual(apply_theme(self.app, 'dark'), 'dark')
        self.assertEqual(apply_theme(self.app, 'light'), 'dark')
        self.assertEqual(apply_theme(self.app, 'system'), 'dark')


if __name__ == '__main__':
    unittest.main()
