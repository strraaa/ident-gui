"""
Вкладка «Стадии»: соответствие статусов приёма Ident стадиям сделки Битрикс24,
а также перечни финальных и защищённых стадий.

Правила поведения службы:
- финальная стадия — сделка считается закрытой, синхронизатор её не трогает;
- защищённая стадия — поля обновляются, но стадию менять нельзя.

Формат хранения в config.ini: «Статус:STAGE_ID» через запятую. Разделителем
служит первое двоеточие, поэтому идентификаторы вида C2:NEW (стадии
дополнительных воронок) записываются без потерь.
"""

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout
)

from gui.services.b24_service import B24Service
from gui.services.config_service import ConfigService
from gui.services.workers import WorkerRunner
from gui.pages.page import Page, Section
from gui.theme import set_strong, set_tone

MAPPING_COLUMNS = ['Статус приёма в Ident', 'Стадия сделки в Битрикс24']

# Статусы Ident, встречающиеся в базах: подсказка при добавлении строки
KNOWN_IDENT_STATUSES = [
    'Запланирован',
    'Пациент пришел',
    'В процессе',
    'Завершен',
    'Завершен (счет выдан)',
    'Отменен',
]


class StagesPage(Page):
    """Маппинг статусов и правила защиты стадий"""

    key = 'stages'
    title = 'Стадии и воронка'
    hint = 'Статусы приёма, стадии сделки и защита от автоизменений'
    section = Section.SETTINGS
    is_settings = True

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(config, parent)
        self.runner = WorkerRunner()
        self.b24_service = B24Service(config)
        self._stages: List[Dict[str, str]] = []
        self._lead_statuses: List[Dict[str, str]] = []
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Воронка
        top = QHBoxLayout()

        self.cmb_category = QComboBox()
        self.cmb_category.setEditable(True)
        self.cmb_category.setMinimumWidth(220)

        self.btn_load = QPushButton('Загрузить стадии из Битрикс24')
        self.btn_load.clicked.connect(self._on_load)

        self.lbl_load_state = QLabel('Стадии портала не загружены — доступен ручной ввод')
        set_tone(self.lbl_load_state, 'muted')
        self.lbl_load_state.setWordWrap(True)

        top.addWidget(QLabel('Воронка сделок:'))
        top.addWidget(self.cmb_category)
        top.addWidget(self.btn_load)
        top.addWidget(self.lbl_load_state, stretch=1)
        layout.addLayout(top)

        # Маппинг
        layout.addWidget(self._build_mapping_box(), stretch=1)

        # Финальные и защищённые
        rules = QHBoxLayout()
        rules.addWidget(self._build_final_box(), stretch=1)
        rules.addWidget(self._build_protected_box(), stretch=1)
        rules.addWidget(self._build_lead_box(), stretch=1)
        layout.addLayout(rules, stretch=1)

    def _build_mapping_box(self) -> QGroupBox:
        box = QGroupBox('Соответствие статусов')
        layout = QVBoxLayout(box)

        self.table = QTableWidget(0, len(MAPPING_COLUMNS))
        self.table.setHorizontalHeaderLabels(MAPPING_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)

        layout.addWidget(self.table)

        buttons = QHBoxLayout()

        btn_add = QPushButton('Добавить статус')
        btn_add.clicked.connect(lambda: self._add_mapping_row('', ''))

        btn_remove = QPushButton('Удалить выбранный')
        btn_remove.clicked.connect(self._remove_selected_row)

        self.cmb_default_stage = QComboBox()
        self.cmb_default_stage.setEditable(True)
        self.cmb_default_stage.setMinimumWidth(200)

        buttons.addWidget(btn_add)
        buttons.addWidget(btn_remove)
        buttons.addStretch()
        buttons.addWidget(QLabel('Стадия для несопоставленных статусов:'))
        buttons.addWidget(self.cmb_default_stage)
        layout.addLayout(buttons)

        return box

    def _build_final_box(self) -> QGroupBox:
        box = QGroupBox('Финальные стадии')
        layout = QVBoxLayout(box)

        self.list_final = QListWidget()
        layout.addWidget(self.list_final)

        hint = QLabel('Сделка считается закрытой: служба её не обновляет и стадию не меняет.')
        hint.setWordWrap(True)
        set_tone(hint, 'muted')
        layout.addWidget(hint)

        return box

    def _build_protected_box(self) -> QGroupBox:
        box = QGroupBox('Защищённые стадии')
        layout = QVBoxLayout(box)

        self.list_protected = QListWidget()
        layout.addWidget(self.list_protected)

        hint = QLabel('Поля сделки обновляются, но стадию служба не переключает.')
        hint.setWordWrap(True)
        set_tone(hint, 'muted')
        layout.addWidget(hint)

        return box

    def _build_lead_box(self) -> QGroupBox:
        box = QGroupBox('Закрытые статусы лидов')
        layout = QVBoxLayout(box)

        self.list_lead_closed = QListWidget()
        layout.addWidget(self.list_lead_closed)

        hint = QLabel('Лиды в этих статусах не конвертируются в сделки.')
        hint.setWordWrap(True)
        set_tone(hint, 'muted')
        layout.addWidget(hint)

        return box

    # ------------------------------------------------------------------

    def load_from_config(self):
        category_id = self.config.get('pipelines', 'deal_category_id', '0')
        self.cmb_category.setCurrentText(category_id)

        # Маппинг статусов
        self.table.setRowCount(0)
        raw_mapping = self.config.get('deal_stages', 'mapping', '')
        for pair in raw_mapping.split(','):
            pair = pair.strip()
            if not pair or ':' not in pair:
                continue
            status, stage = pair.split(':', 1)
            self._add_mapping_row(status.strip(), stage.strip())

        self._set_combo_value(self.cmb_default_stage, self.config.get('deal_defaults', 'default_stage_id', 'NEW'))

        self._fill_checklist(self.list_final, self._config_list('deal_stages', 'final'))
        self._fill_checklist(self.list_protected, self._config_list('deal_stages', 'protected'))
        self._fill_checklist(self.list_lead_closed, self._config_list('lead_statuses', 'closed'), lead=True)

    def apply_to_config(self):
        self.config.set('pipelines', 'deal_category_id', self.cmb_category.currentText().strip() or '0')

        pairs = []
        for row in range(self.table.rowCount()):
            status = self._row_status(row)
            stage = self._row_stage(row)
            if status and stage:
                pairs.append(f'{status}:{stage}')

        self.config.set('deal_stages', 'mapping', ','.join(pairs))
        self.config.set('deal_defaults', 'default_stage_id', self._combo_value(self.cmb_default_stage))
        self.config.set('deal_stages', 'final', ','.join(self._checked_values(self.list_final)))
        self.config.set('deal_stages', 'protected', ','.join(self._checked_values(self.list_protected)))
        self.config.set('lead_statuses', 'closed', ','.join(self._checked_values(self.list_lead_closed)))

    def validate(self) -> List[str]:
        problems = []
        statuses = set()

        for row in range(self.table.rowCount()):
            status = self._row_status(row)
            stage = self._row_stage(row)

            if not status and not stage:
                continue
            if not status:
                problems.append(f'Строка {row + 1}: не указан статус Ident')
                continue
            if not stage:
                problems.append(f'Статус «{status}»: не выбрана стадия сделки')
                continue

            if ',' in status:
                problems.append(f'Статус «{status}» содержит запятую — она разделяет пары в конфигурации')
            if ':' in status:
                problems.append(f'Статус «{status}» содержит двоеточие — оно отделяет статус от стадии')
            if ',' in stage:
                problems.append(f'Стадия «{stage}» содержит запятую')

            if status in statuses:
                problems.append(f'Статус «{status}» указан несколько раз')
            statuses.add(status)

        if not self._combo_value(self.cmb_default_stage):
            problems.append('Не задана стадия для несопоставленных статусов')

        category = self.cmb_category.currentText().strip()
        if category and not category.isdigit():
            problems.append('ID воронки должен быть числом')

        # Стадия, которая одновременно финальная и целевая для маппинга, приведёт
        # к тому, что запись перестанет обновляться сразу после попадания в неё
        finals = set(self._checked_values(self.list_final))
        protected = set(self._checked_values(self.list_protected))
        overlap = finals & protected
        if overlap:
            problems.append(
                'Стадии одновременно финальные и защищённые: ' + ', '.join(sorted(overlap))
            )

        return problems

    # ------------------------------------------------------------------

    def _add_mapping_row(self, status: str, stage: str):
        row = self.table.rowCount()
        self.table.insertRow(row)

        status_editor = QComboBox()
        status_editor.setEditable(True)
        status_editor.addItems(KNOWN_IDENT_STATUSES)
        status_editor.setCurrentText(status)
        self.table.setCellWidget(row, 0, status_editor)

        stage_editor = QComboBox()
        stage_editor.setEditable(True)
        self._fill_stage_combo(stage_editor)
        self._set_combo_value(stage_editor, stage)
        self.table.setCellWidget(row, 1, stage_editor)

    def _remove_selected_row(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)

        if not rows:
            QMessageBox.information(self, 'Стадии', 'Выберите строку для удаления.')
            return

        for row in rows:
            self.table.removeRow(row)

    def _row_status(self, row: int) -> str:
        editor = self.table.cellWidget(row, 0)
        return editor.currentText().strip() if editor else ''

    def _row_stage(self, row: int) -> str:
        editor = self.table.cellWidget(row, 1)
        return self._combo_value(editor) if editor else ''

    # ------------------------------------------------------------------

    def _on_load(self):
        category = self.cmb_category.currentText().strip()
        category_id = int(category) if category.isdigit() else 0

        self.btn_load.setEnabled(False)
        self.lbl_load_state.setText('Загружаем стадии портала…')
        set_tone(self.lbl_load_state, 'muted')

        self.runner.run(
            self.b24_service.load_stage_environment,
            self._on_loaded,
            self._on_load_failed,
            category_id,
        )

    def _on_loaded(self, data: Dict[str, List[Dict[str, str]]]):
        self.btn_load.setEnabled(True)

        self._stages = data.get('stages', [])
        self._lead_statuses = data.get('lead_statuses', [])

        # Воронки
        current_category = self.cmb_category.currentText().strip()
        self.cmb_category.blockSignals(True)
        self.cmb_category.clear()
        for category in data.get('categories', []):
            self.cmb_category.addItem(f"{category['name']} (ID {category['id']})", category['id'])
        self.cmb_category.blockSignals(False)
        self._set_combo_value(self.cmb_category, current_category)

        # Стадии в таблице маппинга
        for row in range(self.table.rowCount()):
            editor = self.table.cellWidget(row, 1)
            if not editor:
                continue
            value = self._combo_value(editor)
            self._fill_stage_combo(editor)
            self._set_combo_value(editor, value)

        default_stage = self._combo_value(self.cmb_default_stage)
        self._fill_stage_combo(self.cmb_default_stage)
        self._set_combo_value(self.cmb_default_stage, default_stage)

        # Списки правил: сохраняем отмеченные значения
        self._refill_checklist(self.list_final)
        self._refill_checklist(self.list_protected)
        self._refill_checklist(self.list_lead_closed, lead=True)

        self.lbl_load_state.setText(
            f'Загружено стадий: {len(self._stages)}, статусов лидов: {len(self._lead_statuses)}'
        )
        set_tone(self.lbl_load_state, 'success')

    def _on_load_failed(self, error: str):
        self.btn_load.setEnabled(True)
        self.lbl_load_state.setText(f'Не удалось загрузить стадии: {error}')
        set_tone(self.lbl_load_state, 'danger')

        QMessageBox.warning(
            self,
            'Загрузка стадий',
            f'Не удалось получить стадии портала:\n{error}\n\n'
            f'Проверьте адрес вебхука на вкладке «Подключения». '
            f'Идентификаторы стадий можно ввести вручную.'
        )

    # ------------------------------------------------------------------

    def _fill_stage_combo(self, combo: QComboBox):
        combo.blockSignals(True)
        combo.clear()
        for stage in self._stages:
            combo.addItem(f"{stage['name']}  ({stage['id']})", stage['id'])
        combo.blockSignals(False)

    def _fill_checklist(self, widget: QListWidget, checked_values: List[str], lead: bool = False):
        """Наполняет список стадий с галочками: известные портала + заданные в конфиге"""
        widget.clear()

        source = self._lead_statuses if lead else self._stages
        known = {item['id']: item['name'] for item in source}

        # Значения из конфига, которых нет среди загруженных — тоже показываем
        for value in checked_values:
            known.setdefault(value, value)

        for stage_id, name in known.items():
            label = f'{name}  ({stage_id})' if name != stage_id else stage_id
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, stage_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if stage_id in checked_values else Qt.Unchecked)
            widget.addItem(item)

    def _refill_checklist(self, widget: QListWidget, lead: bool = False):
        self._fill_checklist(widget, self._checked_values(widget), lead=lead)

    @staticmethod
    def _checked_values(widget: QListWidget) -> List[str]:
        values = []
        for index in range(widget.count()):
            item = widget.item(index)
            if item.checkState() == Qt.Checked:
                values.append(str(item.data(Qt.UserRole)))
        return values

    def _config_list(self, section: str, option: str) -> List[str]:
        raw = self.config.get(section, option, '')
        return [part.strip() for part in raw.split(',') if part.strip()]

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        """Код стадии независимо от способа ввода"""
        data = combo.currentData()
        if data and combo.currentText().endswith(f'({data})'):
            return str(data)
        return combo.currentText().strip()

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str):
        value = (value or '').strip()
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentText(value)
