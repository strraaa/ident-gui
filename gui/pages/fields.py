"""
Вкладка «Поля»: соответствие данных Ident пользовательским полям Битрикс24.

Список доступных полей подгружается с портала, поэтому оператор выбирает поле
по названию, а не переписывает вручную идентификаторы вида UF_CRM_1769072841035.
Ручной ввод при этом остаётся: поля можно вписать и на неработающем портале.
"""

from typing import Dict, List, NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QVBoxLayout
)

from gui.services.b24_service import B24Service
from gui.services.config_service import ConfigService
from gui.services.workers import WorkerRunner
from gui.pages.page import Page, Section
from gui.theme import set_strong, set_tone


class FieldSpec(NamedTuple):
    """Описание одного настраиваемого поля"""
    entity: str      # deal / contact / lead — какие поля портала предлагать
    section: str     # секция config.ini
    option: str      # ключ в секции
    title: str       # понятное название
    required: bool
    comment: str


FIELD_SPECS: List[FieldSpec] = [
    FieldSpec('deal', 'deal_fields', 'ident_id', 'Идентификатор записи Ident', True,
              'Ключевое поле: по нему находится ранее созданная сделка'),
    FieldSpec('deal', 'deal_fields', 'card_number', 'Номер карты пациента', False, ''),
    FieldSpec('deal', 'deal_fields', 'start_time', 'Начало приёма', False, ''),
    FieldSpec('deal', 'deal_fields', 'end_time', 'Окончание приёма', False, ''),
    FieldSpec('deal', 'deal_fields', 'doctor_name', 'Врач', False, ''),
    FieldSpec('deal', 'deal_fields', 'doctor_speciality', 'Специальность врача', False, ''),
    FieldSpec('deal', 'deal_fields', 'services', 'Услуги приёма', False, ''),
    FieldSpec('deal', 'deal_fields', 'status', 'Статус приёма', False, ''),
    FieldSpec('deal', 'deal_fields', 'status_text', 'Статус текстом', False, ''),
    FieldSpec('deal', 'deal_fields', 'parent_name', 'Представитель пациента', False, ''),
    FieldSpec('deal', 'deal_fields', 'comment', 'Комментарий', False, ''),
    FieldSpec('deal', 'deal_fields', 'cancel_reason_name', 'Причина отмены приёма', False,
              'Из справочника Ident: «Отказ от приема», «Не пришел» и т. д.'),
    FieldSpec('deal', 'deal_fields', 'cancel_reason_comment', 'Комментарий к отмене', False,
              'Свободный текст регистратора при отмене приёма'),
    FieldSpec('deal', 'deal_fields', 'transfer_from', 'Перенесена из записи', False,
              'Идентификатор исходной записи вида F1_12345, если приём перенесён с другого времени'),
    FieldSpec('deal', 'deal_fields', 'filial', 'Филиал', False, ''),
    FieldSpec('deal', 'deal_fields', 'armchair', 'Кресло', False, ''),
    FieldSpec('deal', 'deal_fields', 'order_date', 'Дата заказа', False, ''),
    FieldSpec('deal', 'deal_fields', 'treatment_plan', 'План лечения', False, ''),
    FieldSpec('deal', 'deal_fields', 'treatment_plan_hash', 'Контрольная сумма плана лечения', False,
              'Служебное поле: по нему определяется, изменился ли план'),
    FieldSpec('deal', 'deal_fields', 'registrar_id', 'Регистратор (кто завёл запись)', False,
              'Заполняется только при создании сделки'),
    FieldSpec('deal', 'deal_fields', 'legacy_card_number', 'Номер карты (устаревшее поле)', False, ''),
    FieldSpec('deal', 'deal_fields', 'lead_source_id', 'ID исходного лида', False,
              'Куда бизнес-процесс записывает лид, из которого создана сделка'),
    FieldSpec('contact', 'contact_fields', 'card_number', 'Номер карты пациента', False, ''),
    FieldSpec('contact', 'contact_fields', 'parent_name', 'Представитель пациента', False, ''),
    FieldSpec('lead', 'lead_fields', 'convert_trigger', 'Поле-триггер конвертации лида', False,
              'В него записывается ID лида, чтобы бизнес-процесс создал сделку'),
]

ENTITY_TITLES = {
    'deal': 'Сделка',
    'contact': 'Контакт',
    'lead': 'Лид',
}

COLUMNS = ['Сущность', 'Что записываем', 'Поле в Битрикс24']


class FieldsPage(Page):
    """Настройка соответствия полей"""

    key = 'fields'
    title = 'Поля'
    hint = 'Соответствие данных приёма полям Битрикс24'
    section = Section.SETTINGS
    is_settings = True

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(config, parent)
        self.runner = WorkerRunner()
        self.b24_service = B24Service(config)
        self._editors: Dict[int, QComboBox] = {}
        self._portal_fields: Dict[str, List[Dict[str, str]]] = {}
        self._build_ui()
        self.watch_editors()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.btn_load = QPushButton('Загрузить поля из Битрикс24')
        self.btn_load.clicked.connect(self._on_load_fields)

        self.lbl_load_state = QLabel('Список полей портала не загружен — доступен ручной ввод')
        self.lbl_load_state.setWordWrap(True)
        set_tone(self.lbl_load_state, 'muted')

        top.addWidget(self.btn_load)
        top.addWidget(self.lbl_load_state, stretch=1)
        layout.addLayout(top)

        self.table = QTableWidget(len(FIELD_SPECS), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 280)

        self._build_rows()
        layout.addWidget(self.table, stretch=1)

        hint = QLabel(
            'Идентификаторы полей уникальны для каждого портала. Если поля переносились '
            'между порталами, значения нужно задать заново — иначе данные приёмов будут '
            'записываться не в те поля.'
        )
        hint.setWordWrap(True)
        set_tone(hint, 'muted')
        layout.addWidget(hint)

    def _build_rows(self):
        from PySide6.QtWidgets import QTableWidgetItem

        for index, spec in enumerate(FIELD_SPECS):
            entity_item = QTableWidgetItem(ENTITY_TITLES.get(spec.entity, spec.entity))
            entity_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(index, 0, entity_item)

            title = spec.title + (' *' if spec.required else '')
            title_item = QTableWidgetItem(title)
            if spec.comment:
                title_item.setToolTip(spec.comment)
            if spec.required:
                font = title_item.font()
                font.setBold(True)
                title_item.setFont(font)
            self.table.setItem(index, 1, title_item)

            editor = QComboBox()
            editor.setEditable(True)
            editor.setInsertPolicy(QComboBox.NoInsert)
            editor.lineEdit().setPlaceholderText('UF_CRM_… или пусто, если поле не используется')
            self._editors[index] = editor
            self.table.setCellWidget(index, 2, editor)

        self.table.resizeRowsToContents()

    # ------------------------------------------------------------------

    def load_from_config(self):
        for index, spec in enumerate(FIELD_SPECS):
            value = self.config.get(spec.section, spec.option, '')
            self._set_editor_value(self._editors[index], value)

    def apply_to_config(self):
        for index, spec in enumerate(FIELD_SPECS):
            self.config.set(spec.section, spec.option, self._editor_value(self._editors[index]))

    def validate(self) -> List[str]:
        problems = []
        seen: Dict[str, str] = {}

        for index, spec in enumerate(FIELD_SPECS):
            value = self._editor_value(self._editors[index])

            if not value:
                if spec.required:
                    problems.append(f'Не задано обязательное поле «{spec.title}»')
                continue

            if not value.upper().startswith('UF_CRM'):
                problems.append(
                    f'Поле «{spec.title}»: идентификатор должен начинаться с UF_CRM (указано «{value}»)'
                )

            # Одно и то же поле портала в двух назначениях одной сущности —
            # данные будут затирать друг друга
            key = f'{spec.entity}:{value.upper()}'
            if key in seen:
                problems.append(
                    f'Поле {value} назначено дважды: «{seen[key]}» и «{spec.title}»'
                )
            else:
                seen[key] = spec.title

        return problems

    # ------------------------------------------------------------------

    def _on_load_fields(self):
        self.btn_load.setEnabled(False)
        self.lbl_load_state.setText('Загружаем список полей портала…')
        set_tone(self.lbl_load_state, 'muted')

        self.runner.run(
            self.b24_service.load_all_user_fields,
            self._on_fields_loaded,
            self._on_fields_failed,
        )

    def _on_fields_loaded(self, fields: Dict[str, List[Dict[str, str]]]):
        self.btn_load.setEnabled(True)
        self._portal_fields = fields

        for index, spec in enumerate(FIELD_SPECS):
            editor = self._editors[index]
            current = self._editor_value(editor)

            editor.blockSignals(True)
            editor.clear()
            editor.addItem('— не используется —', '')

            for field in fields.get(spec.entity, []):
                editor.addItem(f"{field['title']}  ({field['code']})", field['code'])

            editor.blockSignals(False)
            self._set_editor_value(editor, current)

        total = sum(len(items) for items in fields.values())
        self.lbl_load_state.setText(
            f'Загружено полей портала: {total} '
            f"(сделка — {len(fields.get('deal', []))}, "
            f"контакт — {len(fields.get('contact', []))}, "
            f"лид — {len(fields.get('lead', []))})"
        )
        set_tone(self.lbl_load_state, 'success')

    def _on_fields_failed(self, error: str):
        self.btn_load.setEnabled(True)
        self.lbl_load_state.setText(f'Не удалось загрузить поля: {error}')
        set_tone(self.lbl_load_state, 'danger')

        QMessageBox.warning(
            self,
            'Загрузка полей',
            f'Не удалось получить список полей портала:\n{error}\n\n'
            f'Проверьте адрес вебхука на вкладке «Подключения». '
            f'Идентификаторы полей можно ввести и вручную.'
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _editor_value(editor: QComboBox) -> str:
        """Возвращает код поля независимо от того, выбран он из списка или введён руками"""
        data = editor.currentData()
        if data is not None and editor.currentText().endswith(f'({data})'):
            return str(data)
        if data == '' and editor.currentText().startswith('—'):
            return ''
        return editor.currentText().strip()

    @staticmethod
    def _set_editor_value(editor: QComboBox, value: str):
        """Выбирает значение в списке либо вписывает его как есть"""
        value = (value or '').strip()

        if not value:
            index = editor.findData('')
            if index >= 0:
                editor.setCurrentIndex(index)
            else:
                editor.setCurrentText('')
            return

        index = editor.findData(value)
        if index >= 0:
            editor.setCurrentIndex(index)
        else:
            editor.setCurrentText(value)
