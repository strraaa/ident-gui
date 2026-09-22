"""
Главное окно приложения настроек.

Страницы разложены по трём разделам бокового меню. Наблюдение обновляется
само и ничего не сохраняет; настройка копит правки, пока их не запишут кнопкой;
диагностика ничего не меняет. Раньше всё это стояло семью вкладками в один ряд
и различалось только внутренним флагом.

Запись на диск — одно действие: проверяются все страницы, показывается, что
именно изменится в боевом файле, делается резервная копия, и только потом
конфигурация переписывается.
"""

from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget
)

from gui.core import schema
from gui.pages.connections import ConnectionsPage
from gui.pages.fields import FieldsPage
from gui.pages.logs import LogsPage
from gui.pages.page import SECTION_TITLES, Page, Section
from gui.pages.queue import QueuePage
from gui.pages.stages import StagesPage
from gui.pages.status import StatusPage
from gui.pages.sync import SyncPage
from gui.services.app_settings import AppSettings
from gui.services.config_service import ConfigService
from gui.services import logging_setup, updater
from gui.services.paths import Workspace
from gui.services.task_service import TaskService
from gui.services.workers import WorkerRunner
from gui.theme import apply_theme, set_tone, tokens
from gui.widgets import Banner, NavList
from gui.widgets.update_dialog import UpdateDialog

WINDOW_TITLE = 'Настройки интеграции Ident → Битрикс24'

#: сколько пунктов показывать списком, прежде чем сворачивать в счётчик
LIST_LIMIT = 12

#: задержка между правкой поля и пересчётом состояния, мс
EDIT_DEBOUNCE_MS = 200


class MainWindow(QMainWindow):
    """Окно со страницами наблюдения и настройки"""

    def __init__(self, workspace: Workspace, app_settings: Optional[AppSettings] = None):
        super().__init__()

        self.workspace = workspace
        self.config = ConfigService(workspace)
        self.task_service = TaskService()
        self.app_settings = app_settings or AppSettings()
        self._runner = WorkerRunner()
        self._update_checked = False

        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(tokens.WINDOW_MIN_WIDTH, tokens.WINDOW_MIN_HEIGHT)

        self._build_ui()
        self._restore_window()
        self._load_workspace()
        QTimer.singleShot(1200, self._check_updates_silently)

    # ------------------------------------------------------------------
    # Сборка
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_pages()

        self.nav = NavList()
        self._fill_nav()
        self.nav.page_selected.connect(self._on_page_selected)

        outer.addWidget(self.nav)
        outer.addWidget(self._build_content(), stretch=1)

        self.setCentralWidget(central)
        self.statusBar().showMessage('Готово')

    def _build_pages(self):
        self.overview_page = StatusPage(self.config, self.task_service)
        self.queue_page = QueuePage(self.config)
        self.logs_page = LogsPage(self.config)
        self.connections_page = ConnectionsPage(self.config)
        self.fields_page = FieldsPage(self.config)
        self.stages_page = StagesPage(self.config)
        self.sync_page = SyncPage(self.config)

        self.pages: List[Page] = [
            self.overview_page, self.queue_page, self.logs_page,
            self.connections_page, self.fields_page, self.stages_page, self.sync_page,
        ]

        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(self._scrollable(page))

        # Пересчёт после правки идёт с задержкой: страница сообщает о каждом
        # нажатии клавиши, а переносить значения и считать разницу на каждый
        # символ незачем.
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(EDIT_DEBOUNCE_MS)
        self._edit_timer.timeout.connect(self._on_edited)

        for page in self.pages:
            if page.is_settings:
                page.changed.connect(self._edit_timer.start)

    @staticmethod
    def _scrollable(page: Page) -> QScrollArea:
        """
        Страница внутри области прокрутки.

        Без этого при нехватке высоты окна QFormLayout сжимает строки ниже
        их собственной высоты, и подписи наезжают на поля. Так страница
        сохраняет естественный размер, а лишнее прокручивается.
        """
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(page)
        return area

    def _fill_nav(self):
        """Пункты меню разделами, в порядке объявления страниц"""
        for section in (Section.MONITOR, Section.SETTINGS, Section.DIAGNOSTICS):
            pages = [page for page in self.pages if page.section == section]
            if not pages:
                continue

            self.nav.add_group(SECTION_TITLES[section])
            for page in pages:
                self.nav.add_page(page.key, page.title, page.hint)

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(tokens.GAP_LARGE, tokens.GAP_LARGE,
                                  tokens.GAP_LARGE, tokens.GAP_LARGE)
        layout.setSpacing(tokens.GAP + 2)

        layout.addLayout(self._build_header())

        self.banner = Banner()
        layout.addWidget(self.banner)

        layout.addWidget(self.stack, stretch=1)
        layout.addLayout(self._build_footer())

        return content

    def _build_header(self) -> QHBoxLayout:
        """Строка с папкой службы и переключателем темы"""
        header = QHBoxLayout()
        header.setSpacing(tokens.GAP)

        caption = QLabel('Папка службы:')
        set_tone(caption, 'muted')

        self.lbl_workdir = QLabel()
        self.lbl_workdir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_workdir.setWordWrap(True)

        btn_change = QPushButton('Выбрать…')
        btn_change.setToolTip('Открыть настройки службы из другой папки')
        btn_change.clicked.connect(self._on_change_workdir)

        self.btn_updates = QPushButton('Проверить обновления')
        self.btn_updates.setProperty('variant', 'quiet')
        self.btn_updates.clicked.connect(self._open_updates)

        header.addWidget(caption)
        header.addWidget(self.lbl_workdir, stretch=1)
        header.addWidget(btn_change)
        header.addWidget(self.btn_updates)

        return header

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(tokens.GAP)

        self.lbl_footer = QLabel()
        self.lbl_footer.setWordWrap(True)

        self.btn_reload = QPushButton('Отменить изменения')
        self.btn_reload.clicked.connect(self._on_reload)

        self.btn_save = QPushButton('Сохранить настройки')
        self.btn_save.setProperty('variant', 'primary')
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)

        footer.addWidget(self.lbl_footer, stretch=1)
        footer.addWidget(self.btn_reload)
        footer.addWidget(self.btn_save)

        return footer

    # ------------------------------------------------------------------
    # Рабочая папка
    # ------------------------------------------------------------------

    def _restore_window(self):
        """Возвращает размер окна и раздел, на котором остановились"""
        geometry = self.app_settings.geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1120, 760)

        if not self.nav.select(self.app_settings.page() or ''):
            self.nav.select_first()

    def _load_workspace(self):
        """Загружает конфигурацию выбранной папки и заполняет страницы"""
        self.lbl_workdir.setText(str(self.workspace.workdir))
        self.banner.clear()

        if not self.config.load():
            self._offer_config_creation()
            return

        self._set_settings_enabled(True)
        self._reload_pages()
        self._update_state()

    def _offer_config_creation(self):
        """config.ini не найден — предлагаем создать его из образца"""
        self._set_settings_enabled(False)
        self._update_state()

        self.banner.show_warning(
            f'В папке {self.workspace.workdir} нет файла config.ini — '
            f'настройки открывать не из чего.',
            action_text='Создать из образца',
            action=self._create_config,
            closable=False,
        )

    def _create_config(self):
        if not self.config.create_from_example():
            self.banner.show_error(self.config.load_error or 'Не удалось создать файл')
            return

        self._set_settings_enabled(True)
        self._reload_pages()
        self._update_state()

        self.banner.show_success(
            'Файл создан из образца. Заполните подключения к базе и порталу.'
        )

    def _reload_pages(self):
        # Страницы сейчас заполняются из файла и сообщат об этом как о правке —
        # отложенный пересчёт от прошлых правок больше не актуален
        self._edit_timer.stop()

        for page in self.pages:
            page.load_from_config()

    def _set_settings_enabled(self, enabled: bool):
        for page in self.pages:
            if page.is_settings:
                page.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)
        self.btn_reload.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Навигация и оформление
    # ------------------------------------------------------------------

    def _on_page_selected(self, key: str):
        for index, page in enumerate(self.pages):
            if page.key != key:
                continue

            self.stack.setCurrentIndex(index)
            self.app_settings.set_page(key)
            page.on_activated()
            return

    def current_page(self) -> Optional[Page]:
        index = self.stack.currentIndex()
        return self.pages[index] if 0 <= index < len(self.pages) else None

    def _open_updates(self):
        UpdateDialog(self).exec()

    def _check_updates_silently(self):
        if self._update_checked:
            return
        self._update_checked = True
        self._runner.run(
            updater.check,
            self._updates_checked,
            self._updates_check_failed,
        )

    def _updates_checked(self, info):
        if info is None:
            return
        self.banner.show_message(
            f'Доступно обновление {info.version}.',
            tone='accent',
            action_text='Обновить',
            action=self._open_updates,
        )

    def _updates_check_failed(self, message: str):
        # Фоновая автоматическая проверка не должна мешать работе приложения.
        log = logging_setup.logger('обновление')
        log.warning(message)

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    def _on_change_workdir(self):
        directory = QFileDialog.getExistingDirectory(
            self, 'Папка установки службы', str(self.workspace.workdir)
        )
        if not directory:
            return

        self._apply_pages()
        if self.config.is_dirty and not self._confirm_losing_changes('Смена папки'):
            return

        self.workspace = Workspace(directory)
        self.config.workspace = self.workspace
        self.app_settings.set_workdir(directory)
        self._load_workspace()

    def _on_reload(self):
        self._apply_pages()
        if self.config.is_dirty and not self._confirm_losing_changes('Отмена изменений'):
            return

        if self.config.load():
            self._reload_pages()
            self._update_state()
            self.banner.show_message('Настройки перечитаны с диска')
        else:
            self.banner.show_error(self.config.load_error or 'Не удалось прочитать файл')

    def _on_save(self):
        self._apply_pages()

        problems = self._collect_problems()
        if problems:
            self._show_problems(problems)
            return

        if not self._confirm_changes():
            self._update_state()
            return

        if not self._confirm_external_changes():
            return

        try:
            backup_path = self.config.save()
        except Exception as e:
            self.banner.show_error(f'Не удалось сохранить настройки: {e}')
            return

        for page in self.pages:
            if page.is_settings:
                page.on_saved()

        self._reload_pages()
        self._update_state()
        self.statusBar().showMessage(
            f'Настройки сохранены. Резервная копия: {backup_path.name if backup_path else "—"}',
            8000
        )

        self._offer_restart()

    def _on_edited(self):
        """Оператор что-то изменил: переносим значения и пересчитываем состояние"""
        self._apply_pages()
        self._update_state()

    def _apply_pages(self):
        """
        Переносит значения страниц в конфигурацию — без записи на диск.

        Вызывается после каждой правки, а не только перед сохранением: иначе
        признак несохранённых правок оставался бы ложным до самого нажатия
        кнопки, а кнопка — недоступной, потому что ждёт этого признака.
        """
        if not self.config.store:
            return

        for page in self.pages:
            if not page.is_settings:
                continue

            try:
                page.apply_to_config()
            except Exception as e:
                # Переносу значений нельзя падать: в PySide6 необработанное
                # исключение в обработчике сигнала завершает процесс молча.
                # Самый вероятный случай — недоступное шифрование секрета.
                self.banner.show_error(f'Страница «{page.title}»: {e}')

    def _collect_problems(self) -> List[str]:
        problems = []

        for page in self.pages:
            if page.is_settings:
                problems.extend(page.validate())

        problems.extend(self.config.validate())

        # Страница и общая проверка могут сказать об одном и том же
        unique = []
        for problem in problems:
            if problem not in unique:
                unique.append(problem)
        return unique

    def _show_problems(self, problems: List[str]):
        self.banner.show_error(
            f'Сохранение отменено: {self._count_problems(len(problems))}. '
            f'Первое — {problems[0]}'
        )

        QMessageBox.warning(
            self, 'Проверьте настройки',
            'Сохранение отменено — найдены проблемы:\n\n' + self._bullets(problems)
        )

    def _confirm_changes(self) -> bool:
        """Показывает, что именно будет записано в боевой конфиг"""
        changes = self.config.changes()
        if not changes:
            self.banner.show_message('Изменять нечего — настройки совпадают с файлом')
            return False

        answer = QMessageBox.question(
            self,
            'Сохранение настроек',
            f'В {self.workspace.config_path.name} будет записано:\n\n'
            + self._bullets([change.describe() for change in changes]),
            QMessageBox.Save | QMessageBox.Cancel,
            QMessageBox.Save
        )

        return answer == QMessageBox.Save

    def _confirm_external_changes(self) -> bool:
        """
        Файл могли изменить, пока он был открыт здесь.

        Приложение пишет конфигурацию целиком из снимка, сделанного при
        открытии, поэтому чужие правки оно затирает. Молча этого делать
        нельзя — спрашиваем.
        """
        if not self.config.changed_on_disk():
            return True

        answer = QMessageBox.warning(
            self,
            'Файл изменился на диске',
            f'{self.workspace.config_path.name} изменился после того, как приложение '
            f'его открыло — конфигурацию правил кто-то ещё.\n\n'
            f'Если продолжить, эти изменения будут заменены значениями из окна. '
            f'Чтобы их увидеть, отмените запись и нажмите «Отменить изменения» — '
            f'файл будет перечитан.',
            QMessageBox.Save | QMessageBox.Cancel,
            QMessageBox.Cancel
        )

        return answer == QMessageBox.Save

    def _confirm_losing_changes(self, title: str) -> bool:
        answer = QMessageBox.question(
            self, title,
            'Несохранённые изменения будут потеряны. Продолжить?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        return answer == QMessageBox.Yes

    def _offer_restart(self):
        """Служба читает конфигурацию только при старте — предлагаем перезапуск"""
        saved = 'Настройки сохранены. Изменения вступят в силу после перезапуска службы.'

        if not self.task_service.supported:
            self.banner.show_success(saved)
            return

        if not self.task_service.is_admin():
            self.banner.show_success(
                'Настройки сохранены. Чтобы перезапустить службу отсюда, '
                'запустите приложение от имени администратора.'
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
            self.banner.show_success(saved)
            return

        ok, message = self.task_service.restart()

        if ok:
            self.banner.show_success(message)
        else:
            self.banner.show_error(message)

        self.overview_page.refresh()

    # ------------------------------------------------------------------
    # Состояние интерфейса
    # ------------------------------------------------------------------

    def _update_state(self):
        """Пометки несохранённого, доступность кнопок и строка внизу"""
        changed_groups = set(self.config.changed_groups())

        for page in self.pages:
            if not page.is_settings:
                continue

            page_groups = {group.key for group in schema.groups_of_page(page.key)}
            self.nav.set_dirty(page.key, bool(page_groups & changed_groups))

        dirty = self.config.is_dirty

        self.btn_save.setEnabled(dirty)
        self.btn_reload.setEnabled(dirty)

        self._update_footer(dirty)

    def _update_footer(self, dirty: bool):
        if not self.config.store:
            self.lbl_footer.setText(self.config.load_error or 'Конфигурация не загружена')
            set_tone(self.lbl_footer, 'danger')
            return

        if dirty:
            self.lbl_footer.setText(f'Несохранённых изменений: {len(self.config.changes())}')
            set_tone(self.lbl_footer, 'warning')
            return

        problems = self.config.validate()

        if problems:
            self.lbl_footer.setText(
                f'{self._count_problems(len(problems))} — подробности на странице «Обзор»'
            )
            set_tone(self.lbl_footer, 'warning')
        else:
            self.lbl_footer.setText(str(self.workspace.config_path))
            set_tone(self.lbl_footer, 'muted')

    @staticmethod
    def _bullets(items: List[str]) -> str:
        text = '\n'.join(f'• {item}' for item in items[:LIST_LIMIT])
        if len(items) > LIST_LIMIT:
            text += f'\n… и ещё {len(items) - LIST_LIMIT}'
        return text

    @staticmethod
    def _count_problems(count: int) -> str:
        tail = 'замечаний'
        if count % 10 == 1 and count % 100 != 11:
            tail = 'замечание'
        elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            tail = 'замечания'
        return f'{count} {tail} к конфигурации'

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._apply_pages()

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

        self.app_settings.set_geometry(self.saveGeometry())
        self.app_settings.set_workdir(self.workspace.workdir)
        self.app_settings.sync()

        for page in self.pages:
            runner = getattr(page, 'runner', None)
            if runner:
                runner.wait_all()

        event.accept()
