"""
Страница «Обзор»: что сейчас происходит со службой, синхронизацией и очередью.
"""

import json
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QHeaderView
)

from gui.services.config_service import ConfigService
from gui.services.queue_service import QueueLockError, QueueService
from gui.services.task_service import TaskService
from gui.services.workers import WorkerRunner
from gui.pages.page import Page, Section
from gui.theme import set_strong, set_tone
from gui.widgets import Badge

# Опрос планировщика стоит запуска powershell.exe (сотни миллисекунд даже
# с -NoProfile), поэтому идёт в фоне и не чаще раза в 10 секунд.
REFRESH_INTERVAL_MS = 10000

# С какого отставания синхронизация считается остановившейся
STALE_SYNC_MINUTES = 30


class StatusPage(Page):
    """Сводка состояния и управление службой"""

    key = 'overview'
    title = 'Обзор'
    hint = 'Служба, синхронизация, очередь и проблемы конфигурации'
    section = Section.MONITOR

    def __init__(self, config: ConfigService, task_service: TaskService, parent=None):
        super().__init__(config, parent)
        self.task_service = task_service
        self.runner = WorkerRunner()
        self._busy = False          # опрос уже идёт — тик пропускаем

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        self.lbl_state = Badge('—')
        self.lbl_last_run = QLabel('—')
        self.lbl_last_result = Badge('—')
        self.lbl_next_run = QLabel('—')
        self.lbl_processes = QLabel('—')
        self.lbl_last_sync = QLabel('—')
        self.lbl_sync_age = QLabel('—')
        self.lbl_filial = QLabel('—')
        self.lbl_interval = QLabel('—')
        self.lbl_queue_total = QLabel('—')
        self.lbl_queue_waiting = QLabel('—')
        self.lbl_queue_exhausted = QLabel('—')
        self.lbl_config_path = QLabel()
        self.lbl_config_problems = QLabel()

        title = QLabel('Обзор')
        title.setProperty('role', 'title')
        subtitle = QLabel('Состояние службы и последние операции')
        set_tone(subtitle, 'muted')
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch()

        self.btn_start = QPushButton('Запустить службу')
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop = QPushButton('Остановить службу')
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_restart = QPushButton('Перезапустить службу')
        self.btn_restart.setProperty('variant', 'primary')
        self.btn_restart.clicked.connect(self._on_restart)
        self.btn_repair = QPushButton('Восстановить задачу')
        self.btn_repair.clicked.connect(self._on_repair)
        self.lbl_admin_hint = QLabel()
        set_tone(self.lbl_admin_hint, 'muted')
        self.lbl_admin_hint.hide()
        self.btn_start.hide()
        self.btn_repair.hide()

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.btn_stop)
        actions.addWidget(self.btn_restart)
        title_row.addLayout(actions)
        layout.addLayout(title_row)
        layout.addWidget(subtitle)

        status_box = QFrame()
        status_box.setProperty('role', 'overview-status')
        status_layout = QHBoxLayout(status_box)
        status_layout.setContentsMargins(18, 16, 18, 16)
        main_status = QVBoxLayout()
        status_line = QHBoxLayout()
        state_mark = QLabel()
        state_mark.setProperty('role', 'state-mark')
        state_mark.setFixedSize(9, 9)
        self.lbl_overview_state = QLabel('Служба работает')
        self.lbl_overview_state.setProperty('strong', True)
        self.lbl_overview_live = Badge('Активна', 'success')
        status_line.addWidget(state_mark)
        status_line.addWidget(self.lbl_overview_state)
        status_line.addWidget(self.lbl_overview_live)
        status_line.addStretch()
        main_status.addLayout(status_line)
        self.lbl_overview_copy = QLabel('Последняя синхронизация завершена успешно.')
        set_tone(self.lbl_overview_copy, 'muted')
        main_status.addWidget(self.lbl_overview_copy)
        facts = QGridLayout()
        self.lbl_fact_last = self._fact(facts, 0, 'Последний запуск')
        self.lbl_fact_result = self._fact(facts, 1, 'Результат')
        self.lbl_fact_next = self._fact(facts, 2, 'Следующий запуск')
        main_status.addLayout(facts)
        status_layout.addLayout(main_status, 1)
        health = QVBoxLayout()
        health.addWidget(self._muted_label('Подключения'))
        self.lbl_health = QLabel('—')
        self.lbl_health.setProperty('role', 'metric')
        health.addWidget(self.lbl_health)
        self.lbl_health_hint = self._muted_label('IDENT и Битрикс24 доступны')
        health.addWidget(self.lbl_health_hint)
        status_layout.addLayout(health)
        layout.addWidget(status_box)

        lower = QHBoxLayout()
        runs_box = QFrame()
        runs_box.setProperty('role', 'panel')
        runs_layout = QVBoxLayout(runs_box)
        runs_layout.addWidget(self._panel_heading('Последний запуск', 'Сегодня'))
        self.runs_table = QTableWidget(0, 4)
        self.runs_table.setHorizontalHeaderLabels(['Время', 'Результат', 'Записей', 'Длительность'])
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.runs_table.verticalHeader().setVisible(False)
        self.runs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.runs_table.setSelectionMode(QTableWidget.NoSelection)
        runs_layout.addWidget(self.runs_table)
        lower.addWidget(runs_box, 3)

        queue_box = QFrame()
        queue_box.setProperty('role', 'panel')
        queue_layout = QVBoxLayout(queue_box)
        queue_head = QHBoxLayout()
        queue_head.addWidget(QLabel('Очередь'))
        queue_head.addStretch()
        self.btn_queue = QPushButton('Открыть')
        self.btn_queue.clicked.connect(lambda: self._show_queue_hint())
        queue_head.addWidget(self.btn_queue)
        queue_layout.addLayout(queue_head)
        queue_form = QFormLayout()
        queue_form.addRow('Ожидают отправки:', self.lbl_queue_waiting)
        queue_form.addRow('Обработано сегодня:', self.lbl_queue_total)
        queue_form.addRow('Ошибки без повтора:', self.lbl_queue_exhausted)
        queue_layout.addLayout(queue_form)
        self.queue_attention = QLabel('Требует внимания\n3 записи ждут повторной отправки.')
        self.queue_attention.setWordWrap(True)
        set_tone(self.queue_attention, 'warning')
        queue_layout.addWidget(self.queue_attention)
        self.btn_retry = QPushButton('Повторить')
        self.btn_retry.clicked.connect(self._on_retry_queue)
        queue_layout.addWidget(self.btn_retry, alignment=Qt.AlignLeft)
        lower.addWidget(queue_box, 2)
        layout.addLayout(lower)
        layout.addStretch()

    @staticmethod
    def _muted_label(text: str) -> QLabel:
        label = QLabel(text)
        set_tone(label, 'muted')
        return label

    def _fact(self, layout: QGridLayout, column: int, title: str) -> QLabel:
        label = self._muted_label(title)
        value = QLabel('—')
        value.setProperty('strong', True)
        layout.addWidget(label, 0, column)
        layout.addWidget(value, 1, column)
        return value

    @staticmethod
    def _panel_heading(title: str, hint: str) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 6)
        row.addWidget(QLabel(title))
        row.addStretch()
        hint_label = QLabel(hint)
        set_tone(hint_label, 'muted')
        row.addWidget(hint_label)
        return widget

    def _show_queue_hint(self):
        self.queue_attention.setText('Откройте раздел «Очередь» в меню для управления записями.')

    def _on_retry_queue(self):
        try:
            count = QueueService(self.config.queue_file_path()).reset_all_unsent()
        except (QueueLockError, OSError) as error:
            QMessageBox.warning(self, 'Очередь', f'Не удалось изменить очередь: {error}')
            return
        QMessageBox.information(self, 'Очередь', f'Записей отправлено на повтор: {count}')
        self.refresh()

    # ------------------------------------------------------------------

    def on_activated(self):
        self.refresh()

    def refresh(self):
        """Обновляет все блоки вкладки"""
        if not self.isVisible():
            return

        # Дёшево (память + маленький json) — можно прямо в UI-потоке
        self._refresh_sync()
        self._refresh_config()

        # Дорого (powershell + разбор очереди) — в фоне
        self._request_snapshot()

    # ------------------------------------------------------------------
    # Фоновый сбор состояния
    # ------------------------------------------------------------------

    def _request_snapshot(self):
        if self._busy:
            # Предыдущий опрос ещё не вернулся: очередь тиков копить нельзя,
            # иначе на медленной машине потоки пойдут лавиной.
            return

        self._busy = True
        self.runner.run(self._collect_snapshot, self._on_snapshot, self._on_snapshot_failed)

    def _collect_snapshot(self):
        """Выполняется в фоновом потоке: запуск powershell и чтение очереди"""
        status = self.task_service.status()

        queue = QueueService(self.config.queue_file_path())
        max_attempts = self.config.get_int('Queue', 'max_retry_attempts', 3)
        stats = queue.statistics(queue.read_rows(), max_attempts)

        return status, stats

    def _on_snapshot(self, result):
        self._busy = False
        status, stats = result
        self._apply_service(status)
        self._apply_queue(stats)

    def _on_snapshot_failed(self, error: str):
        self._busy = False
        self.lbl_state.setText(f'Не удалось получить состояние: {error}')
        self._set_service_buttons(False)

    # ------------------------------------------------------------------

    def _apply_service(self, status):
        if not status.available:
            self.lbl_state.setText(status.error or 'Недоступно')
            self._set_overview_service('Служба недоступна', 'Недоступна', status.error or 'Нет ответа от планировщика')
            self._set_service_buttons(False)
            self.btn_repair.setEnabled(False)
            self.lbl_admin_hint.setText(
                'Проверьте службу Task Scheduler и права администратора.'
                if status.error_kind == 'provider_unavailable'
                else ''
            )
            return

        if not status.exists:
            if status.error_kind == 'wrong_path':
                self.lbl_state.setText(
                    f'Неверный путь задачи: {status.actual_task_path or status.error}'
                )
            else:
                self.lbl_state.setText(status.error or 'Задача не зарегистрирована')
            self._set_overview_service('Служба не зарегистрирована', 'Требует настройки', self.lbl_state.text())
            self._set_service_buttons(False)
            self.btn_repair.setEnabled(self.task_service.is_admin())
            self.lbl_admin_hint.setText(
                'Нажмите «Восстановить задачу» для повторной регистрации службы.'
                if self.task_service.is_admin() else
                'Для восстановления задачи запустите приложение от имени администратора.'
            )
            return

        self.lbl_state.set_state(
            status.state_title,
            'success' if status.is_running else 'warning'
        )
        self.lbl_last_run.setText(status.last_run_time or '—')
        self.lbl_next_run.setText(status.next_run_time or '—')
        self.lbl_processes.setText(str(len(status.processes)) if status.processes else '0')

        if status.last_task_result is None:
            self.lbl_last_result.set_state('—')
            result_text = 'Нет данных'
        elif status.last_result_is_error:
            self.lbl_last_result.set_state(f'Код {status.last_task_result} — ошибка', 'danger')
            result_text = f'Код {status.last_task_result} — ошибка'
        else:
            self.lbl_last_result.set_state(f'Код {status.last_task_result}', 'success')
            result_text = f'Код {status.last_task_result}'

        self._set_overview_service(
            'Служба работает' if status.is_running else status.state_title,
            'Активна' if status.is_running else status.state_title,
            'Последняя синхронизация завершена успешно.'
            if not status.last_result_is_error else 'Последний запуск завершился с ошибкой.'
        )
        self.lbl_fact_last.setText(status.last_run_time or '—')
        self.lbl_fact_result.setText(result_text)
        self.lbl_fact_next.setText(status.next_run_time or '—')
        self._populate_runs(status)

        if len(status.processes) > 1:
            self.lbl_processes.setText(
                f'{len(status.processes)} — это дубликаты, синхронизация может задваиваться'
            )
            set_tone(self.lbl_processes, 'danger')
            set_strong(self.lbl_processes)
        else:
            set_tone(self.lbl_processes, None)
            set_strong(self.lbl_processes, False)

        is_admin = self.task_service.is_admin()
        self._set_service_buttons(is_admin)
        self.btn_repair.setEnabled(False)
        self.lbl_admin_hint.setText(
            '' if is_admin else
            'Управление службой недоступно: запустите приложение от имени администратора.'
        )

    def _refresh_sync(self):
        state = self._read_sync_state()

        if not state:
            self.lbl_last_sync.setText('нет данных')
            self.lbl_sync_age.setText('—')
        else:
            last_sync = state.get('last_sync_time')
            parsed = self._parse_dt(last_sync)

            if parsed:
                self.lbl_last_sync.setText(parsed.strftime('%d.%m.%Y %H:%M:%S'))

                delta = datetime.now() - parsed
                minutes = int(delta.total_seconds() // 60)
                self.lbl_sync_age.setText(self._humanize_minutes(minutes))
                # Отставание больше получаса при штатном цикле в две минуты
                # означает, что служба стоит или не доходит до портала
                stale = minutes > STALE_SYNC_MINUTES
                set_tone(self.lbl_sync_age, 'danger' if stale else None)
                set_strong(self.lbl_sync_age, stale)
            else:
                self.lbl_last_sync.setText(str(last_sync or 'нет данных'))
                self.lbl_sync_age.setText('—')

        filial = self.config.get('Sync', 'filial_id', '—')
        enabled_filials = self.config.get('FilialFilter', 'enabled_filial_ids', '').strip()
        self.lbl_filial.setText(
            f'{filial} (фильтр: {enabled_filials})' if enabled_filials else str(filial)
        )
        self.lbl_interval.setText(f"{self.config.get('Sync', 'interval_minutes', '—')} мин")

    def _apply_queue(self, stats):
        waiting = stats['pending'] + stats['processing'] + stats['failed'] - stats['exhausted']

        self.lbl_queue_total.setText(str(stats['total']))
        self.lbl_queue_waiting.setText(str(max(waiting, 0)))
        self.lbl_queue_exhausted.setText(str(stats['exhausted']))
        self.queue_attention.setText(
            'Ошибок без повтора нет.'
            if not waiting else
            f'{max(waiting, 0)} записей ждут повторной отправки.'
        )
        set_tone(self.lbl_queue_exhausted, 'danger' if stats['exhausted'] else None)
        set_strong(self.lbl_queue_exhausted, bool(stats['exhausted']))

    def _set_overview_service(self, title: str, live: str, copy: str):
        self.lbl_overview_state.setText(title)
        self.lbl_overview_live.set_state(live, 'success' if live == 'Активна' else 'warning')
        self.lbl_overview_copy.setText(copy)

    def _populate_runs(self, status):
        self.runs_table.setRowCount(1)
        values = [
            status.last_run_time or '—',
            'Ошибка' if status.last_result_is_error else 'Успешно',
            '—',
            '—',
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            self.runs_table.setItem(0, column, item)

    def _refresh_config(self):
        self.lbl_config_path.setText(str(self.config.workspace.config_path))

        problems = self.config.validate()
        if problems:
            text = '\n'.join(f'• {p}' for p in problems[:5])
            if len(problems) > 5:
                text += f'\n… и ещё {len(problems) - 5}'
            self.lbl_config_problems.setText(text)
            set_tone(self.lbl_config_problems, 'danger')
        else:
            self.lbl_config_problems.setText('Проблем не обнаружено')
            set_tone(self.lbl_config_problems, 'success')

    # ------------------------------------------------------------------

    def _set_service_buttons(self, enabled: bool):
        self.btn_start.setEnabled(enabled)
        self.btn_stop.setEnabled(enabled)
        self.btn_restart.setEnabled(enabled)

    def _on_start(self):
        self._run_task_action(self.task_service.start)

    def _on_stop(self):
        confirmed = QMessageBox.question(
            self,
            'Остановка службы',
            'Остановить синхронизацию? Новые записи из Ident не будут попадать в Битрикс24, '
            'пока служба не запущена.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirmed == QMessageBox.Yes:
            self._run_task_action(self.task_service.stop)

    def _on_restart(self):
        self._run_task_action(self.task_service.restart)

    def _on_repair(self):
        self._run_task_action(self.task_service.install)

    def _run_task_action(self, action):
        # Пуск/останов/перезапуск — это два-три вызова powershell подряд.
        # В UI-потоке окно замирало бы на несколько секунд.
        self._set_service_buttons(False)
        self.lbl_state.setText('Выполняется…')
        self.runner.run(action, self._on_task_action_done, self._on_task_action_failed)

    def _on_task_action_done(self, result):
        ok, message = result

        if ok:
            QMessageBox.information(self, 'Служба', message)
        else:
            QMessageBox.warning(self, 'Служба', message)

        self.refresh()

    def _on_task_action_failed(self, error: str):
        QMessageBox.warning(self, 'Служба', f'Не удалось выполнить операцию: {error}')
        self.refresh()

    # ------------------------------------------------------------------

    def _read_sync_state(self) -> Optional[dict]:
        path = self.config.workspace.sync_state_path
        if not path.exists():
            return None

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _parse_dt(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _humanize_minutes(minutes: int) -> str:
        if minutes < 0:
            return '—'
        if minutes < 60:
            return f'{minutes} мин'

        hours = minutes // 60
        if hours < 24:
            return f'{hours} ч {minutes % 60} мин'

        days = hours // 24
        return f'{days} дн {hours % 24} ч'
