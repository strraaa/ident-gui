"""
Вкладка «Синхронизация»: режим работы цикла, филиалы, очередь и журналирование.
"""

from typing import List

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
    QSpinBox, QVBoxLayout
)

from gui.services.config_service import ConfigService
from gui.tabs.base import BaseTab

LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']


class SyncTab(BaseTab):
    """Параметры синхронизации, очереди и логов"""

    title = 'Синхронизация'
    is_settings_tab = True

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(config, parent)
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(self._build_sync_box())
        layout.addWidget(self._build_filial_box())
        layout.addWidget(self._build_queue_box())
        layout.addWidget(self._build_logging_box())
        layout.addStretch()

    @staticmethod
    def _hint(text: str) -> QLabel:
        """
        Подсказка под формой.

        Переносимый по словам текст нельзя класть строкой в QFormLayout: высота
        считается по одной строке, и подсказка налезает на соседние поля.
        Поэтому подсказки живут во внешнем layout группы.
        """
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName('hint')
        return label

    def _build_sync_box(self) -> QGroupBox:
        box = QGroupBox('Цикл синхронизации')
        outer = QVBoxLayout(box)
        form = QFormLayout()
        outer.addLayout(form)

        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setSuffix(' мин')

        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 1000)

        self.spin_initial_days = QSpinBox()
        self.spin_initial_days.setRange(1, 3650)
        self.spin_initial_days.setSuffix(' дн')

        self.chk_update_existing = QCheckBox('Обновлять поля уже существующих сделок')

        form.addRow('Интервал запуска:', self.spin_interval)
        form.addRow('Записей за цикл:', self.spin_batch)
        form.addRow('Глубина первой загрузки:', self.spin_initial_days)
        form.addRow('', self.chk_update_existing)

        outer.addWidget(self._hint(
            'Интервал действует только внутри работающего процесса службы. '
            'Сам процесс запускается задачей планировщика при старте системы.'
        ))

        return box

    def _build_filial_box(self) -> QGroupBox:
        box = QGroupBox('Филиалы')
        outer = QVBoxLayout(box)
        form = QFormLayout()
        outer.addLayout(form)

        self.spin_filial_id = QSpinBox()
        self.spin_filial_id.setRange(1, 10)

        self.txt_enabled_filials = QLineEdit()
        self.txt_enabled_filials.setPlaceholderText('например, 4, 5 — пусто означает «все филиалы»')

        self.spin_default_filial = QSpinBox()
        self.spin_default_filial.setRange(0, 10)

        form.addRow('ID филиала по умолчанию:', self.spin_filial_id)
        form.addRow('Синхронизировать филиалы:', self.txt_enabled_filials)
        form.addRow('Филиал для записей без филиала:', self.spin_default_filial)

        outer.addWidget(self._hint(
            'ID филиала входит в уникальный идентификатор записи (F1_12345). '
            'Менять его на работающей интеграции нельзя — в Битрикс24 появятся дубли сделок. '
            'Значение 0 в последнем поле означает «пропускать такие записи».'
        ))

        return box

    def _build_queue_box(self) -> QGroupBox:
        box = QGroupBox('Очередь повторных попыток')
        outer = QVBoxLayout(box)
        form = QFormLayout()
        outer.addLayout(form)

        self.chk_queue_enabled = QCheckBox('Использовать очередь при ошибках отправки')

        self.spin_max_attempts = QSpinBox()
        self.spin_max_attempts.setRange(1, 20)
        self.spin_max_attempts.valueChanged.connect(self._update_retry_window)

        self.spin_retry_interval = QSpinBox()
        self.spin_retry_interval.setRange(1, 600)
        self.spin_retry_interval.setSuffix(' мин')
        self.spin_retry_interval.valueChanged.connect(self._update_retry_window)

        self.spin_queue_size = QSpinBox()
        self.spin_queue_size.setRange(10, 100000)

        # Однострочные: в QFormLayout перенос по словам ломает расчёт высоты
        self.lbl_retry_schedule = QLabel()
        self.lbl_retry_total = QLabel()

        form.addRow('', self.chk_queue_enabled)
        form.addRow('Всего попыток на запись:', self.spin_max_attempts)
        form.addRow('Базовый интервал повтора:', self.spin_retry_interval)
        form.addRow('Максимальный размер очереди:', self.spin_queue_size)
        form.addRow('Расписание попыток:', self.lbl_retry_schedule)
        form.addRow('Переживёт простой портала:', self.lbl_retry_total)

        outer.addWidget(self._hint(
            'Задержка удваивается с каждой попыткой. Чем больше попыток, тем дольше запись '
            'переживает недоступность портала — например, при обрыве DNS.'
        ))

        return box

    def _build_logging_box(self) -> QGroupBox:
        box = QGroupBox('Журналирование')
        outer = QVBoxLayout(box)
        form = QFormLayout()
        outer.addLayout(form)

        self.cmb_log_level = QComboBox()
        self.cmb_log_level.addItems(LOG_LEVELS)

        self.spin_rotation_days = QSpinBox()
        self.spin_rotation_days.setRange(1, 365)
        self.spin_rotation_days.setSuffix(' дн')

        self.spin_log_size = QSpinBox()
        self.spin_log_size.setRange(1, 1000)
        self.spin_log_size.setSuffix(' МБ')

        self.spin_log_backups = QSpinBox()
        self.spin_log_backups.setRange(1, 100)

        self.chk_mask_personal = QCheckBox('Маскировать персональные данные в журнале')

        form.addRow('Уровень подробности:', self.cmb_log_level)
        form.addRow('Хранить журналы:', self.spin_rotation_days)
        form.addRow('Размер файла до ротации:', self.spin_log_size)
        form.addRow('Файлов в архиве:', self.spin_log_backups)
        form.addRow('', self.chk_mask_personal)

        return box

    # ------------------------------------------------------------------

    def load_from_config(self):
        self.spin_interval.setValue(self.config.get_int('Sync', 'interval_minutes', 2))
        self.spin_batch.setValue(self.config.get_int('Sync', 'batch_size', 50))
        self.spin_initial_days.setValue(self.config.get_int('Sync', 'initial_days', 7))
        self.chk_update_existing.setChecked(self.config.get_bool('Sync', 'enable_update_existing', True))
        self.spin_filial_id.setValue(self.config.get_int('Sync', 'filial_id', 1))

        self.txt_enabled_filials.setText(self.config.get('FilialFilter', 'enabled_filial_ids'))
        self.spin_default_filial.setValue(self.config.get_int('FilialFilter', 'default_filial_id', 0))

        self.chk_queue_enabled.setChecked(self.config.get_bool('Queue', 'enabled', True))
        self.spin_max_attempts.setValue(self.config.get_int('Queue', 'max_retry_attempts', 3))
        self.spin_retry_interval.setValue(self.config.get_int('Queue', 'retry_interval_minutes', 5))
        self.spin_queue_size.setValue(self.config.get_int('Queue', 'max_size', 1000))

        level = self.config.get('Logging', 'level', 'INFO').upper()
        index = self.cmb_log_level.findText(level)
        self.cmb_log_level.setCurrentIndex(index if index >= 0 else LOG_LEVELS.index('INFO'))

        self.spin_rotation_days.setValue(self.config.get_int('Logging', 'rotation_days', 30))
        self.spin_log_size.setValue(self.config.get_int('Logging', 'max_log_size_mb', 20))
        self.spin_log_backups.setValue(self.config.get_int('Logging', 'max_backup_files', 5))
        self.chk_mask_personal.setChecked(self.config.get_bool('Logging', 'mask_personal_data', True))

        self._update_retry_window()

    def apply_to_config(self):
        self.config.set('Sync', 'interval_minutes', self.spin_interval.value())
        self.config.set('Sync', 'batch_size', self.spin_batch.value())
        self.config.set('Sync', 'initial_days', self.spin_initial_days.value())
        self.config.set('Sync', 'enable_update_existing', self.chk_update_existing.isChecked())
        self.config.set('Sync', 'filial_id', self.spin_filial_id.value())

        self.config.set('FilialFilter', 'enabled_filial_ids', self.txt_enabled_filials.text().strip())
        self.config.set('FilialFilter', 'default_filial_id', self.spin_default_filial.value())

        self.config.set('Queue', 'enabled', self.chk_queue_enabled.isChecked())
        self.config.set('Queue', 'max_retry_attempts', self.spin_max_attempts.value())
        self.config.set('Queue', 'retry_interval_minutes', self.spin_retry_interval.value())
        self.config.set('Queue', 'max_size', self.spin_queue_size.value())

        self.config.set('Logging', 'level', self.cmb_log_level.currentText())
        self.config.set('Logging', 'rotation_days', self.spin_rotation_days.value())
        self.config.set('Logging', 'max_log_size_mb', self.spin_log_size.value())
        self.config.set('Logging', 'max_backup_files', self.spin_log_backups.value())
        self.config.set('Logging', 'mask_personal_data', self.chk_mask_personal.isChecked())

    def validate(self) -> List[str]:
        problems = []

        raw_filials = self.txt_enabled_filials.text().strip()
        if raw_filials:
            for part in raw_filials.split(','):
                part = part.strip()
                if not part:
                    continue
                if not part.isdigit():
                    problems.append(f'Список филиалов содержит нечисловое значение: «{part}»')

        return problems

    # ------------------------------------------------------------------

    def _update_retry_window(self):
        """
        Показывает, когда именно будут выполнены попытки и сколько времени
        запись переживёт недоступность портала.
        """
        attempts = self.spin_max_attempts.value()
        base = self.spin_retry_interval.value()

        offsets = [0]
        for attempt in range(1, attempts):
            offsets.append(offsets[-1] + base * (2 ** (attempt - 1)))

        total = offsets[-1]
        schedule = ', '.join(
            'сразу' if offset == 0 else f'+{offset} мин'
            for offset in offsets[:6]
        )
        if len(offsets) > 6:
            schedule += ', …'

        self.lbl_retry_schedule.setText(schedule)
        self.lbl_retry_total.setText(self._humanize(total))

    @staticmethod
    def _humanize(minutes: int) -> str:
        if minutes < 60:
            return f'{minutes} мин'

        hours = minutes // 60
        rest = minutes % 60

        if hours < 24:
            return f'{hours} ч {rest} мин' if rest else f'{hours} ч'

        days = hours // 24
        return f'{days} дн {hours % 24} ч'
