"""
Вкладка «Подключения»: доступ к базе Ident и к порталу Битрикс24.

Пароль БД хранится зашифрованным через DPAPI. GUI его не расшифровывает и не
показывает: видно лишь, что значение задано и зашифровано. Ввод нового значения
перезаписывает старое.
"""

from typing import List

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout
)

from gui.services.b24_service import B24Service, DatabaseService
from gui.services.config_service import ConfigService
from gui.services.workers import WorkerRunner
from gui.pages.page import Page, Section
from gui.theme import set_strong, set_tone

PLACEHOLDER_KEEP = '••••••••  (сохранён, не показывается)'


class ConnectionsPage(Page):
    """Настройки подключения к БД и порталу"""

    key = 'connections'
    title = 'Подключения'
    hint = 'Доступ к базе Ident и к порталу Битрикс24'
    section = Section.SETTINGS
    is_settings = True

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(config, parent)
        self.runner = WorkerRunner()
        self.db_service = DatabaseService(config)
        self.b24_service = B24Service(config)
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(self._build_database_box())
        layout.addWidget(self._build_bitrix_box())
        layout.addStretch()

    def _build_database_box(self) -> QGroupBox:
        box = QGroupBox('База данных Ident (SQL Server)')
        outer = QVBoxLayout(box)

        form = QFormLayout()

        self.txt_db_server = QLineEdit()
        self.txt_db_server.setPlaceholderText('например, SERVER\\SQLEXPRESS или 192.168.1.10')

        self.spin_db_port = QSpinBox()
        self.spin_db_port.setRange(1, 65535)

        self.txt_db_name = QLineEdit()
        self.txt_db_user = QLineEdit()

        self.txt_db_password = QLineEdit()
        self.txt_db_password.setEchoMode(QLineEdit.Password)

        self.lbl_db_password_state = QLabel()
        self.lbl_db_password_state.setWordWrap(True)
        set_tone(self.lbl_db_password_state, 'muted')

        self.spin_db_conn_timeout = QSpinBox()
        self.spin_db_conn_timeout.setRange(1, 600)
        self.spin_db_conn_timeout.setSuffix(' с')

        self.spin_db_query_timeout = QSpinBox()
        self.spin_db_query_timeout.setRange(1, 3600)
        self.spin_db_query_timeout.setSuffix(' с')

        form.addRow('Сервер:', self.txt_db_server)
        form.addRow('Порт:', self.spin_db_port)
        form.addRow('База данных:', self.txt_db_name)
        form.addRow('Пользователь:', self.txt_db_user)
        form.addRow('Пароль:', self.txt_db_password)
        form.addRow('Таймаут подключения:', self.spin_db_conn_timeout)
        form.addRow('Таймаут запроса:', self.spin_db_query_timeout)

        outer.addLayout(form)

        # Переносимую по словам подсказку нельзя класть строкой в QFormLayout:
        # высота считается по одной строке, и текст налезает на соседние поля
        outer.addWidget(self.lbl_db_password_state)

        buttons = QHBoxLayout()
        self.btn_test_db = QPushButton('Проверить подключение к БД')
        self.btn_test_db.clicked.connect(self._on_test_db)
        self.lbl_db_result = QLabel()
        self.lbl_db_result.setWordWrap(True)

        buttons.addWidget(self.btn_test_db)
        buttons.addWidget(self.lbl_db_result, stretch=1)
        outer.addLayout(buttons)

        return box

    def _build_bitrix_box(self) -> QGroupBox:
        box = QGroupBox('Портал Битрикс24')
        outer = QVBoxLayout(box)

        form = QFormLayout()

        self.txt_webhook = QLineEdit()
        self.txt_webhook.setEchoMode(QLineEdit.Password)
        self.txt_webhook.setPlaceholderText('https://portal.bitrix24.ru/rest/1/xxxxxxxx/')

        webhook_row = QHBoxLayout()
        webhook_row.addWidget(self.txt_webhook, stretch=1)

        self.btn_show_webhook = QPushButton('Показать')
        self.btn_show_webhook.setCheckable(True)
        self.btn_show_webhook.toggled.connect(self._on_toggle_webhook_visibility)
        webhook_row.addWidget(self.btn_show_webhook)

        self.spin_b24_timeout = QSpinBox()
        self.spin_b24_timeout.setRange(1, 600)
        self.spin_b24_timeout.setSuffix(' с')

        self.spin_b24_retries = QSpinBox()
        self.spin_b24_retries.setRange(1, 10)

        self.spin_rate_limit = QSpinBox()
        self.spin_rate_limit.setRange(1, 10)
        self.spin_rate_limit.setSuffix(' запр./сек')

        self.txt_assigned_by = QLineEdit()
        self.txt_assigned_by.setPlaceholderText('ID пользователя, пусто — владелец вебхука')

        form.addRow('Адрес вебхука:', webhook_row)
        form.addRow('Таймаут запроса:', self.spin_b24_timeout)
        form.addRow('Повторов при ошибке:', self.spin_b24_retries)
        form.addRow('Ограничение частоты:', self.spin_rate_limit)
        form.addRow('Ответственный по умолчанию:', self.txt_assigned_by)

        outer.addLayout(form)

        buttons = QHBoxLayout()
        self.btn_test_b24 = QPushButton('Проверить подключение к порталу')
        self.btn_test_b24.clicked.connect(self._on_test_b24)
        self.lbl_b24_result = QLabel()
        self.lbl_b24_result.setWordWrap(True)

        buttons.addWidget(self.btn_test_b24)
        buttons.addWidget(self.lbl_b24_result, stretch=1)
        outer.addLayout(buttons)

        hint = QLabel(
            'Адрес вебхука содержит секретный ключ портала — не передавайте его третьим лицам. '
            'Права вебхука должны включать CRM.'
        )
        hint.setWordWrap(True)
        set_tone(hint, 'muted')
        outer.addWidget(hint)

        return box

    # ------------------------------------------------------------------

    def load_from_config(self):
        self.txt_db_server.setText(self.config.get('Database', 'server'))
        self.spin_db_port.setValue(self.config.get_int('Database', 'port', 1433))
        self.txt_db_name.setText(self.config.get('Database', 'database'))
        self.txt_db_user.setText(self.config.get('Database', 'username'))
        self.spin_db_conn_timeout.setValue(self.config.get_int('Database', 'connection_timeout', 10))
        self.spin_db_query_timeout.setValue(self.config.get_int('Database', 'query_timeout', 30))

        self.txt_db_password.clear()
        self._update_password_state()

        self.txt_webhook.setText(self.config.get('bitrix', 'webhook_url'))
        self.spin_b24_timeout.setValue(self.config.get_int('bitrix', 'request_timeout', 30))
        self.spin_b24_retries.setValue(self.config.get_int('bitrix', 'max_retries', 3))
        self.spin_rate_limit.setValue(self.config.get_int('bitrix', 'rate_limit', 2))
        self.txt_assigned_by.setText(self.config.get('bitrix', 'default_assigned_by_id'))

    def apply_to_config(self):
        self.config.set('Database', 'server', self.txt_db_server.text().strip())
        self.config.set('Database', 'port', self.spin_db_port.value())
        self.config.set('Database', 'database', self.txt_db_name.text().strip())
        self.config.set('Database', 'username', self.txt_db_user.text().strip())
        self.config.set('Database', 'connection_timeout', self.spin_db_conn_timeout.value())
        self.config.set('Database', 'query_timeout', self.spin_db_query_timeout.value())

        # Пустое поле означает «оставить прежний пароль», а не «стереть»
        new_password = self.txt_db_password.text()
        if new_password:
            self.config.set_secret('Database', 'password', new_password)
            self.txt_db_password.clear()

        self.config.set('bitrix', 'webhook_url', self.txt_webhook.text().strip())
        self.config.set('bitrix', 'request_timeout', self.spin_b24_timeout.value())
        self.config.set('bitrix', 'max_retries', self.spin_b24_retries.value())
        self.config.set('bitrix', 'rate_limit', self.spin_rate_limit.value())
        self.config.set('bitrix', 'default_assigned_by_id', self.txt_assigned_by.text().strip())

        self._update_password_state()

    def validate(self) -> List[str]:
        problems = []

        if not self.txt_db_server.text().strip():
            problems.append('Не указан сервер базы данных')
        if not self.txt_db_name.text().strip():
            problems.append('Не указано имя базы данных')
        if not self.txt_db_user.text().strip():
            problems.append('Не указан пользователь базы данных')

        if not self.config.has_value('Database', 'password') and not self.txt_db_password.text():
            problems.append('Не задан пароль базы данных')

        webhook = self.txt_webhook.text().strip()
        if not webhook:
            problems.append('Не указан адрес вебхука Битрикс24')
        elif not webhook.startswith(('http://', 'https://')):
            problems.append('Адрес вебхука должен начинаться с http:// или https://')

        assigned = self.txt_assigned_by.text().strip()
        if assigned and not assigned.isdigit():
            problems.append('ID ответственного должен быть числом')

        if self.txt_db_password.text() and not self.config.encryption_available:
            problems.append(
                'Шифрование недоступно (не установлен pywin32) — пароль сохранить нельзя'
            )

        return problems

    # ------------------------------------------------------------------

    def _update_password_state(self):
        if self.config.is_encrypted('Database', 'password'):
            self.lbl_db_password_state.setText(
                'Пароль сохранён и зашифрован. Оставьте поле пустым, чтобы не менять его.'
            )
        elif self.config.has_value('Database', 'password'):
            self.lbl_db_password_state.setText(
                'Внимание: пароль хранится в открытом виде. Введите его заново, '
                'чтобы сохранить в зашифрованном виде.'
            )
        else:
            self.lbl_db_password_state.setText('Пароль не задан.')

        self.txt_db_password.setPlaceholderText(
            PLACEHOLDER_KEEP if self.config.has_value('Database', 'password') else 'Введите пароль'
        )

    def _on_toggle_webhook_visibility(self, shown: bool):
        self.txt_webhook.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.btn_show_webhook.setText('Скрыть' if shown else 'Показать')

    # ------------------------------------------------------------------

    def _on_test_db(self):
        """Проверка идёт по сохранённым настройкам — их нужно сначала записать"""
        if self._unsaved_warning('проверки подключения к базе'):
            return

        self.btn_test_db.setEnabled(False)
        self.lbl_db_result.setText('Проверяем…')
        set_tone(self.lbl_db_result, 'muted')

        self.runner.run(
            self.db_service.test_connection,
            lambda message: self._on_test_done(self.btn_test_db, self.lbl_db_result, message, True),
            lambda error: self._on_test_done(self.btn_test_db, self.lbl_db_result, error, False),
        )

    def _on_test_b24(self):
        if self._unsaved_warning('проверки подключения к порталу'):
            return

        self.btn_test_b24.setEnabled(False)
        self.lbl_b24_result.setText('Проверяем…')
        set_tone(self.lbl_b24_result, 'muted')

        self.runner.run(
            self.b24_service.test_connection,
            lambda message: self._on_test_done(self.btn_test_b24, self.lbl_b24_result, message, True),
            lambda error: self._on_test_done(self.btn_test_b24, self.lbl_b24_result, error, False),
        )

    @staticmethod
    def _on_test_done(button: QPushButton, label: QLabel, message: str, success: bool):
        button.setEnabled(True)
        label.setText(message if success else f'Ошибка: {message}')
        set_tone(label, 'success' if success else 'danger')

    def _unsaved_warning(self, action: str) -> bool:
        """Возвращает True, если проверку выполнять нельзя из-за несохранённых правок"""
        if not self.config.is_dirty:
            return False

        QMessageBox.information(
            self,
            'Сначала сохраните настройки',
            f'Настройки изменены, но ещё не сохранены. Для {action} '
            f'нажмите «Сохранить настройки» внизу окна.'
        )
        return True
