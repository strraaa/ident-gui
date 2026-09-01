"""
Вкладка «Состояние»: что сейчас происходит со службой, синхронизацией и очередью.
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
from gui.tabs.base import BaseTab

REFRESH_INTERVAL_MS = 5000


class StatusTab(BaseTab):
    """Сводка состояния и управление службой"""

    title = 'Состояние'

    def __init__(self, config: ConfigService, task_service: TaskService, parent=None):
        super().__init__(config, parent)
        self.task_service = task_service

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

        self.lbl_state = QLabel('—')
        self.lbl_last_run = QLabel('—')
        self.lbl_last_result = QLabel('—')
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
        self.lbl_admin_hint.setObjectName('hint')
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

        self._refresh_service()
        self._refresh_sync()
        self._refresh_queue()
        self._refresh_config()

    def _refresh_service(self):
        status = self.task_service.status()

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

        self.lbl_state.setText(status.state_title)
        self.lbl_state.setStyleSheet(
            'color: #1a7f37; font-weight: 600;' if status.is_running else 'color: #a04100; font-weight: 600;'
        )
        self.lbl_last_run.setText(status.last_run_time or '—')
        self.lbl_next_run.setText(status.next_run_time or '—')
        self.lbl_processes.setText(str(len(status.processes)) if status.processes else '0')

        if status.last_task_result is None:
            self.lbl_last_result.setText('—')
        elif status.last_result_is_error:
            self.lbl_last_result.setText(f'Код {status.last_task_result} (ошибка)')
            self.lbl_last_result.setStyleSheet('color: #b42318;')
        else:
            self.lbl_last_result.setText(f'Код {status.last_task_result}')
            self.lbl_last_result.setStyleSheet('')

        if len(status.processes) > 1:
            self.lbl_processes.setText(
                f'{len(status.processes)} — это дубликаты, синхронизация может задваиваться'
            )
            self.lbl_processes.setStyleSheet('color: #b42318; font-weight: 600;')
        else:
            self.lbl_processes.setStyleSheet('')

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
                self.lbl_sync_age.setStyleSheet(
                    'color: #b42318; font-weight: 600;' if minutes > 30 else ''
                )
            else:
                self.lbl_last_sync.setText(str(last_sync or 'нет данных'))
                self.lbl_sync_age.setText('—')

        filial = self.config.get('Sync', 'filial_id', '—')
        enabled_filials = self.config.get('FilialFilter', 'enabled_filial_ids', '').strip()
        self.lbl_filial.setText(
            f'{filial} (фильтр: {enabled_filials})' if enabled_filials else str(filial)
        )
        self.lbl_interval.setText(f"{self.config.get('Sync', 'interval_minutes', '—')} мин")

    def _refresh_queue(self):
        queue = QueueService(self.config.queue_file_path())
        max_attempts = self.config.get_int('Queue', 'max_retry_attempts', 3)

        rows = queue.read_rows()
        stats = queue.statistics(rows, max_attempts)

        waiting = stats['pending'] + stats['processing'] + stats['failed'] - stats['exhausted']

        self.lbl_queue_total.setText(str(stats['total']))
        self.lbl_queue_waiting.setText(str(max(waiting, 0)))
        self.lbl_queue_exhausted.setText(str(stats['exhausted']))
        self.lbl_queue_exhausted.setStyleSheet(
            'color: #b42318; font-weight: 600;' if stats['exhausted'] else ''
        )

    def _refresh_config(self):
        self.lbl_config_path.setText(str(self.config.workspace.config_path))

        problems = self.config.validate()
        if problems:
            text = '\n'.join(f'• {p}' for p in problems[:5])
            if len(problems) > 5:
                text += f'\n… и ещё {len(problems) - 5}'
            self.lbl_config_problems.setText(text)
            self.lbl_config_problems.setStyleSheet('color: #b42318;')
        else:
            self.lbl_config_problems.setText('Проблем не обнаружено')
            self.lbl_config_problems.setStyleSheet('color: #1a7f37;')

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
        ok, message = action()

        if ok:
            QMessageBox.information(self, 'Служба', message)
        else:
            QMessageBox.warning(self, 'Служба', message)

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
