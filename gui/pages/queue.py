"""
Вкладка «Очередь»: что не доехало до Битрикс24 и почему.

Здесь же ручной аналог ночного дослава — кнопка «Дослать всё»: сбрасывает
счётчики попыток у всех неотправленных записей, после чего служба заберёт их
в ближайшем цикле синхронизации.
"""

from datetime import datetime
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout
)
from gui.widgets.inputs import NoWheelComboBox

from gui.services.config_service import ConfigService
from gui.services.queue_service import (
    STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_PROCESSING,
    QueueLockError, QueueRow, QueueService
)
from gui.pages.page import Page, Section
from gui.theme import set_tone

COLUMNS = ['IDENT ID', 'Пациент', 'Статус', 'Попыток', 'Обновлён', 'Следующая попытка', 'Ошибка']

FILTERS = [
    ('Все записи', None),
    ('Не отправленные', 'unsent'),
    ('Попытки исчерпаны', 'exhausted'),
    ('Ожидают', STATUS_PENDING),
    ('В обработке', STATUS_PROCESSING),
    ('С ошибкой', STATUS_FAILED),
    ('Отправленные', STATUS_COMPLETED),
]


class QueuePage(Page):
    """Просмотр и управление очередью повторных попыток"""

    key = 'queue'
    title = 'Очередь'
    hint = 'Что не доехало до Битрикса и почему'
    section = Section.MONITOR

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(config, parent)
        self._rows: List[QueueRow] = []
        self._build_ui()

        # Очередь меняется службой, поэтому обновляем содержимое периодически
        self._timer = QTimer(self)
        self._timer.setInterval(10000)
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Панель фильтров
        filters = QHBoxLayout()

        self.cmb_filter = NoWheelComboBox()
        for title, _ in FILTERS:
            self.cmb_filter.addItem(title)
        self.cmb_filter.currentIndexChanged.connect(self._apply_filters)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText('Поиск по ID, пациенту или тексту ошибки')
        self.txt_search.textChanged.connect(self._apply_filters)

        btn_refresh = QPushButton('Обновить')
        btn_refresh.clicked.connect(self.reload)

        filters.addWidget(QLabel('Показать:'))
        filters.addWidget(self.cmb_filter)
        filters.addWidget(self.txt_search, stretch=1)
        filters.addWidget(btn_refresh)
        layout.addLayout(filters)

        # Таблица
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)

        layout.addWidget(self.table, stretch=1)

        # Сводка
        self.lbl_summary = QLabel('—')
        layout.addWidget(self.lbl_summary)

        # Кнопки действий
        actions = QHBoxLayout()

        self.btn_retry = QPushButton('Повторить выбранные')
        self.btn_retry_all = QPushButton('Дослать всё')
        self.btn_delete = QPushButton('Удалить выбранные')
        self.btn_clear = QPushButton('Очистить отправленные')
        self.btn_export = QPushButton('Выгрузить в CSV')

        self.btn_retry.clicked.connect(self._on_retry_selected)
        self.btn_retry_all.clicked.connect(self._on_retry_all)
        self.btn_delete.clicked.connect(self._on_delete_selected)
        self.btn_clear.clicked.connect(self._on_clear_completed)
        self.btn_export.clicked.connect(self._on_export)

        actions.addWidget(self.btn_retry)
        actions.addWidget(self.btn_retry_all)
        actions.addWidget(self.btn_delete)
        actions.addWidget(self.btn_clear)
        actions.addStretch()
        actions.addWidget(self.btn_export)
        layout.addLayout(actions)

        hint = QLabel(
            'Записи из очереди отправляет служба — кнопки здесь лишь возвращают их в работу. '
            'Пока служба запущена, она перезаписывает файл очереди целиком, поэтому перед '
            'массовыми операциями её лучше остановить на вкладке «Состояние».'
        )
        hint.setWordWrap(True)
        set_tone(hint, 'muted')
        layout.addWidget(hint)

    # ------------------------------------------------------------------

    def on_activated(self):
        self.reload()

    def reload(self):
        """Перечитывает очередь с диска"""
        service = self._service()
        self._rows = service.read_rows()
        self._apply_filters()

    def _auto_refresh(self):
        """Тихое обновление по таймеру — не трогает выделение, если ничего не изменилось"""
        if not self.isVisible():
            return

        service = self._service()
        rows = service.read_rows()

        if len(rows) != len(self._rows):
            self._rows = rows
            self._apply_filters()
            return

        self._rows = rows

    def _service(self) -> QueueService:
        return QueueService(self.config.queue_file_path())

    def _max_attempts(self) -> int:
        return self.config.get_int('Queue', 'max_retry_attempts', 3)

    # ------------------------------------------------------------------

    def _apply_filters(self):
        """Перерисовывает таблицу с учётом фильтра и строки поиска"""
        mode = FILTERS[self.cmb_filter.currentIndex()][1]
        search = self.txt_search.text().strip().lower()
        max_attempts = self._max_attempts()

        visible = []
        for row in self._rows:
            if mode == 'unsent' and row.status == STATUS_COMPLETED:
                continue
            if mode == 'exhausted' and not (
                row.status == STATUS_FAILED and row.is_exhausted(max_attempts)
            ):
                continue
            if mode in (STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED, STATUS_COMPLETED) \
                    and row.status != mode:
                continue

            if search:
                haystack = ' '.join([
                    row.unique_id, row.patient, row.last_error or ''
                ]).lower()
                if search not in haystack:
                    continue

            visible.append(row)

        self._fill_table(visible)
        self._update_summary(visible)

    def _fill_table(self, rows: List[QueueRow]):
        self.table.setRowCount(len(rows))
        max_attempts = self._max_attempts()

        for index, row in enumerate(rows):
            values = [
                row.unique_id,
                row.patient,
                row.status_title,
                f'{row.retry_count} из {max_attempts}',
                QueueService._format_dt(row.updated_at),
                QueueService._format_dt(row.next_retry_at or ''),
                (row.last_error or '').replace('\n', ' '),
            ]

            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))

                if column == 0:
                    item.setData(Qt.UserRole, row.unique_id)

                if row.status == STATUS_FAILED and row.is_exhausted(max_attempts):
                    item.setForeground(Qt.red)
                elif row.status == STATUS_COMPLETED:
                    item.setForeground(Qt.gray)

                if column == 6 and row.last_error:
                    item.setToolTip(row.last_error)

                self.table.setItem(index, column, item)

    def _update_summary(self, visible: List[QueueRow]):
        stats = self._service().statistics(self._rows, self._max_attempts())

        self.lbl_summary.setText(
            f"Показано {len(visible)} из {stats['total']}   •   "
            f"ожидают: {stats['pending']}   •   в обработке: {stats['processing']}   •   "
            f"с ошибкой: {stats['failed']}   •   попытки исчерпаны: {stats['exhausted']}   •   "
            f"отправлено: {stats['completed']}"
        )

    def _selected_ids(self) -> List[str]:
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item:
                ids.append(item.data(Qt.UserRole) or item.text())
        return ids

    # ------------------------------------------------------------------

    def _on_retry_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, 'Очередь', 'Выберите записи в таблице.')
            return

        self._mutate(
            lambda service: service.reset_items(ids),
            f'Возвращено в работу записей: {{count}} из {len(ids)}'
        )

    def _on_retry_all(self):
        unsent = [row for row in self._rows if row.status != STATUS_COMPLETED]
        if not unsent:
            QMessageBox.information(self, 'Очередь', 'Все записи уже отправлены.')
            return

        confirmed = QMessageBox.question(
            self,
            'Дослать всё',
            f'Вернуть в работу все неотправленные записи ({len(unsent)} шт.)?\n\n'
            f'Счётчики попыток будут обнулены, и служба попробует отправить их заново '
            f'в ближайшем цикле синхронизации.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirmed != QMessageBox.Yes:
            return

        self._mutate(
            lambda service: service.reset_all_unsent(),
            'Возвращено в работу записей: {count}'
        )

    def _on_delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, 'Очередь', 'Выберите записи в таблице.')
            return

        confirmed = QMessageBox.question(
            self,
            'Удаление записей',
            f'Удалить {len(ids)} запис(ей) из очереди безвозвратно?\n\n'
            f'Эти приёмы не попадут в Битрикс24, пока не изменятся в Ident.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirmed != QMessageBox.Yes:
            return

        self._mutate(
            lambda service: service.remove_items(ids),
            'Удалено записей: {count}'
        )

    def _on_clear_completed(self):
        self._mutate(
            lambda service: service.clear_completed(),
            'Удалено отправленных записей: {count}'
        )

    def _mutate(self, action, success_template: str):
        """Выполняет операцию над очередью и показывает результат"""
        try:
            count = action(self._service())
        except QueueLockError as e:
            QMessageBox.warning(self, 'Очередь занята', str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка', f'Не удалось изменить очередь:\n{e}')
            return

        QMessageBox.information(self, 'Очередь', success_template.format(count=count))
        self.reload()

    def _on_export(self):
        if not self._rows:
            QMessageBox.information(self, 'Выгрузка', 'Очередь пуста.')
            return

        default_name = f"queue_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, 'Выгрузить очередь', default_name, 'CSV (*.csv)'
        )
        if not path:
            return

        try:
            count = self._service().export_csv(Path(path), self._rows)
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка', f'Не удалось сохранить файл:\n{e}')
            return

        QMessageBox.information(self, 'Выгрузка', f'Записей выгружено: {count}\n{path}')
