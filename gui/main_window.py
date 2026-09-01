"""
Главное окно приложения настроек.

Вкладки настроек правят конфигурацию только в памяти. Запись на диск происходит
по кнопке «Сохранить настройки»: сначала проверяются все вкладки, затем делается
резервная копия config.ini, и лишь потом файл перезаписывается.
"""

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QTabWidget, QVBoxLayout, QWidget
)

from gui.services.config_service import ConfigService
from gui.services.paths import Workspace
from gui.services.task_service import TaskService
from gui.tabs.base import BaseTab
from gui.tabs.connections_tab import ConnectionsTab
from gui.tabs.fields_tab import FieldsTab
from gui.tabs.logs_tab import LogsTab
from gui.tabs.queue_tab import QueueTab
from gui.tabs.stages_tab import StagesTab
from gui.tabs.status_tab import StatusTab
from gui.tabs.sync_tab import SyncTab

WINDOW_TITLE = 'Настройки интеграции Ident → Битрикс24'


class MainWindow(QMainWindow):
    """Окно с вкладками настроек и инструментов"""

    def __init__(self, workspace: Workspace):
        super().__init__()

        self.workspace = workspace
        self.config = ConfigService(workspace)
        self.task_service = TaskService()

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1100, 780)

        self._build_ui()
        self._load_workspace()

    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Шапка: где лежат файлы службы
        header = QHBoxLayout()

        self.lbl_workdir = QLabel()
        self.lbl_workdir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_workdir.setWordWrap(True)

        btn_change = QPushButton('Выбрать папку…')
        btn_change.clicked.connect(self._on_change_workdir)

        header.addWidget(QLabel('Папка службы:'))
        header.addWidget(self.lbl_workdir, stretch=1)
        header.addWidget(btn_change)
        layout.addLayout(header)

        # Вкладки
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.status_tab = StatusTab(self.config, self.task_service)
        self.connections_tab = ConnectionsTab(self.config)
        self.fields_tab = FieldsTab(self.config)
        self.stages_tab = StagesTab(self.config)
        self.sync_tab = SyncTab(self.config)
        self.queue_tab = QueueTab(self.config)
        self.logs_tab = LogsTab(self.config)

        self.all_tabs: List[BaseTab] = [
            self.status_tab, self.connections_tab, self.fields_tab,
            self.stages_tab, self.sync_tab, self.queue_tab, self.logs_tab,
        ]

        for tab in self.all_tabs:
            self.tabs.addTab(tab, tab.title)

        layout.addWidget(self.tabs, stretch=1)

        # Нижняя панель
        footer = QHBoxLayout()

        self.lbl_footer = QLabel()
        self.lbl_footer.setWordWrap(True)

        self.btn_reload = QPushButton('Отменить изменения')
        self.btn_reload.clicked.connect(self._on_reload)

        self.btn_save = QPushButton('Сохранить настройки')
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)

        footer.addWidget(self.lbl_footer, stretch=1)
        footer.addWidget(self.btn_reload)
        footer.addWidget(self.btn_save)
        layout.addLayout(footer)

        self.setCentralWidget(central)
        self.statusBar().showMessage('Готово')

    # ------------------------------------------------------------------

    def _load_workspace(self):
        """Загружает конфигурацию выбранной папки и заполняет вкладки"""
        self.lbl_workdir.setText(str(self.workspace.workdir))

        loaded = self.config.load()

        if not loaded:
            self._offer_config_creation()
            return

        self._reload_tabs()
        self._update_footer()

    def _offer_config_creation(self):
        """config.ini не найден — предлагаем создать его из образца"""
        self.lbl_footer.setText(f'Конфигурация не загружена: {self.config.load_error}')
        self.lbl_footer.setStyleSheet('color: #b42318;')
        self._set_settings_enabled(False)

        answer = QMessageBox.question(
            self,
            'Конфигурация не найдена',
            f'В папке {self.workspace.workdir} нет файла config.ini.\n\n'
            f'Создать его из образца config.example.ini?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if answer != QMessageBox.Yes:
            return

        if self.config.create_from_example():
            QMessageBox.information(
                self, 'Конфигурация',
                'Файл создан. Заполните подключения к базе и порталу.'
            )
            self._set_settings_enabled(True)
            self._reload_tabs()
            self._update_footer()
        else:
            QMessageBox.warning(self, 'Конфигурация', self.config.load_error or 'Не удалось создать файл')

    def _reload_tabs(self):
        for tab in self.all_tabs:
            tab.load_from_config()

    def _set_settings_enabled(self, enabled: bool):
        for tab in self.all_tabs:
            if tab.is_settings_tab:
                tab.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)

    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int):
        widget = self.tabs.widget(index)
        if isinstance(widget, BaseTab):
            widget.on_activated()

    def _on_change_workdir(self):
        directory = QFileDialog.getExistingDirectory(
            self, 'Папка установки службы', str(self.workspace.workdir)
        )
        if not directory:
            return

        self.workspace = Workspace(directory)
        self.config.workspace = self.workspace
        self._set_settings_enabled(True)
        self._load_workspace()

    def _on_reload(self):
        answer = QMessageBox.question(
            self,
            'Отмена изменений',
            'Перечитать настройки с диска? Несохранённые изменения будут потеряны.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return

        if self.config.load():
            self._reload_tabs()
            self._update_footer()
            self.statusBar().showMessage('Настройки перечитаны с диска', 5000)
        else:
            QMessageBox.warning(self, 'Конфигурация', self.config.load_error or 'Не удалось прочитать файл')

    def _on_save(self):
        problems = self._collect_problems()

        if problems:
            text = '\n'.join(f'• {p}' for p in problems[:12])
            if len(problems) > 12:
                text += f'\n… и ещё {len(problems) - 12}'

            QMessageBox.warning(
                self, 'Проверьте настройки',
                f'Сохранение отменено — найдены проблемы:\n\n{text}'
            )
            return

        try:
            for tab in self.all_tabs:
                if tab.is_settings_tab:
                    tab.apply_to_config()

            backup_path = self.config.save()
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка сохранения', f'Не удалось сохранить настройки:\n{e}')
            return

        self._update_footer()
        self.statusBar().showMessage(
            f'Настройки сохранены. Резервная копия: {backup_path.name if backup_path else "—"}', 8000
        )

        self._offer_restart()

    def _collect_problems(self) -> List[str]:
        problems = []
        for tab in self.all_tabs:
            if tab.is_settings_tab:
                problems.extend(tab.validate())
        return problems

    def _offer_restart(self):
        """Служба читает конфигурацию только при старте — предлагаем перезапуск"""
        if not self.task_service.supported:
            QMessageBox.information(
                self, 'Настройки сохранены',
                'Изменения вступят в силу после перезапуска службы синхронизации.'
            )
            return

        if not self.task_service.is_admin():
            QMessageBox.information(
                self, 'Настройки сохранены',
                'Изменения вступят в силу после перезапуска службы.\n\n'
                'Чтобы перезапустить её отсюда, запустите приложение от имени администратора.'
            )
            return

        answer = QMessageBox.question(
            self,
            'Настройки сохранены',
            'Служба читает конфигурацию только при запуске.\n\nПерезапустить её сейчас?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if answer != QMessageBox.Yes:
            return

        ok, message = self.task_service.restart()

        if ok:
            QMessageBox.information(self, 'Служба', message)
        else:
            QMessageBox.warning(self, 'Служба', message)

        self.status_tab.refresh()

    def _update_footer(self):
        problems = self.config.validate()

        if problems:
            self.lbl_footer.setText(f'Замечаний к конфигурации: {len(problems)} — см. вкладку «Состояние»')
            self.lbl_footer.setStyleSheet('color: #a04100;')
        else:
            self.lbl_footer.setText(f'Конфигурация: {self.workspace.config_path}')
            self.lbl_footer.setStyleSheet('')

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self.config.is_dirty:
            answer = QMessageBox.question(
                self,
                'Несохранённые изменения',
                'Настройки изменены, но не сохранены. Закрыть приложение?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        for tab in self.all_tabs:
            runner = getattr(tab, 'runner', None)
            if runner:
                runner.wait_all()

        event.accept()
