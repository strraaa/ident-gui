"""
Страница «Обзор»: что сейчас происходит со службой, синхронизацией и очередью.
"""

import json
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout
)

from gui.services.config_service import ConfigService
from gui.services.queue_service import QueueService
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
        layout.setSpacing(12)

        # --- Служба ---
        service_box = QGroupBox('Служба синхронизации')
        service_layout = QVBoxLayout(service_box)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        # Состояние и результат — отметки: их видно, не читая
        self.lbl_state = Badge('—')
        self.lbl_last_run = QLabel('—')
        self.lbl_last_result = Badge('—')
        self.lbl_next_run = QLabel('—')
        self.lbl_processes = QLabel('—')

        form.addRow('Состояние:', self.lbl_state)
        form.addRow('Последний запуск:', self.lbl_last_run)
        form.addRow('Результат:', self.lbl_last_result)
        form.addRow('Следующий запуск:', self.lbl_next_run)
        form.addRow('Процессов запущено:', self.lbl_processes)
        service_layout.addLayout(form)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton('Запустить')
        self.btn_stop = QPushButton('Остановить')
        self.btn_restart = QPushButton('Перезапустить')

        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_restart.clicked.connect(self._on_restart)

        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_restart)
        buttons.addStretch()
        service_layout.addLayout(buttons)

        self.lbl_admin_hint = QLabel()
        self.lbl_admin_hint.setWordWrap(True)
        set_tone(self.lbl_admin_hint, 'muted')
        service_layout.addWidget(self.lbl_admin_hint)

        layout.addWidget(service_box)

        # --- Синхронизация ---
        sync_box = QGroupBox('Синхронизация')
        sync_form = QFormLayout(sync_box)
        sync_form.setLabelAlignment(Qt.AlignRight)

        self.lbl_last_sync = QLabel('—')
        self.lbl_sync_age = QLabel('—')
        self.lbl_filial = QLabel('—')
        self.lbl_interval = QLabel('—')

        sync_form.addRow('Данные обработаны до:', self.lbl_last_sync)
        sync_form.addRow('Отставание:', self.lbl_sync_age)
        sync_form.addRow('Филиал:', self.lbl_filial)
        sync_form.addRow('Интервал цикла:', self.lbl_interval)

        layout.addWidget(sync_box)

        # --- Очередь ---
        queue_box = QGroupBox('Очередь повторных попыток')
        queue_form = QFormLayout(queue_box)
        queue_form.setLabelAlignment(Qt.AlignRight)

        self.lbl_queue_total = QLabel('—')
        self.lbl_queue_waiting = QLabel('—')
        self.lbl_queue_exhausted = QLabel('—')

        queue_form.addRow('Всего записей:', self.lbl_queue_total)
        queue_form.addRow('Ожидают отправки:', self.lbl_queue_waiting)
        queue_form.addRow('Попытки исчерпаны:', self.lbl_queue_exhausted)

        layout.addWidget(queue_box)

        # --- Конфигурация ---
        config_box = QGroupBox('Конфигурация')
        config_layout = QVBoxLayout(config_box)

        self.lbl_config_path = QLabel('—')
        self.lbl_config_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_config_path.setWordWrap(True)
        config_layout.addWidget(self.lbl_config_path)

        self.lbl_config_problems = QLabel()
        self.lbl_config_problems.setWordWrap(True)
        config_layout.addWidget(self.lbl_config_problems)

        layout.addWidget(config_box)
        layout.addStretch()

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
            self._set_service_buttons(False)
            self.lbl_admin_hint.setText('')
            return

        if not status.exists:
            self.lbl_state.setText(f'Задача не найдена ({status.error})')
            self._set_service_buttons(False)
            self.lbl_admin_hint.setText(
                'Задача планировщика не установлена. Выполните install_task_onedir.ps1 '
                'от имени администратора.'
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
        elif status.last_result_is_error:
            self.lbl_last_result.set_state(f'Код {status.last_task_result} — ошибка', 'danger')
        else:
            self.lbl_last_result.set_state(f'Код {status.last_task_result}', 'success')

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
        set_tone(self.lbl_queue_exhausted, 'danger' if stats['exhausted'] else None)
        set_strong(self.lbl_queue_exhausted, bool(stats['exhausted']))

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
