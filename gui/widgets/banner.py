"""
Полоса сообщения вместо модального окна.

Раньше каждая неудачная загрузка справочника открывала QMessageBox. На сервере
без доступа к порталу это встречало оператора чередой окон, которые нужно
закрывать по одному, прежде чем что-то сделать.

Полоса живёт внутри страницы: сообщает то же самое, не отбирает управление
и не мешает продолжать работу. Модальное окно остаётся там, где без ответа
дальше нельзя — подтверждение остановки службы, удаление записей очереди.
"""

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from gui.theme import repolish, tokens


class Banner(QFrame):
    """Сообщение в полосе: текст, необязательное действие и кнопка закрытия"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setProperty('role', 'banner')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(tokens.GAP + 2, tokens.GAP, tokens.GAP, tokens.GAP)
        layout.setSpacing(tokens.GAP)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._action = QPushButton()
        self._action.setProperty('variant', 'quiet')
        self._action.hide()

        self._close = QPushButton('✕')
        self._close.setProperty('variant', 'quiet')
        self._close.setFixedWidth(28)
        self._close.setToolTip('Скрыть сообщение')
        self._close.clicked.connect(self.clear)

        self._action_handler: Optional[Callable[[], None]] = None

        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._action)
        layout.addWidget(self._close)

        self.hide()

    # ------------------------------------------------------------------

    def show_message(self, text: str, tone: str = 'accent',
                     action_text: str = '', action: Optional[Callable[[], None]] = None,
                     closable: bool = True) -> None:
        """Показывает сообщение. `tone` — accent, success, warning или danger."""
        self._label.setText(text)

        self.setProperty('tone', tone)
        repolish(self)

        # Прошлое действие снимается: одна и та же полоса переиспользуется
        # для разных сообщений, и старый обработчик остался бы висеть
        if self._action_handler is not None:
            self._action.clicked.disconnect(self._action_handler)
            self._action_handler = None

        if action_text and action is not None:
            self._action.setText(action_text)
            self._action.clicked.connect(action)
            self._action_handler = action
            self._action.show()
        else:
            self._action.hide()

        self._close.setVisible(closable)
        self.show()

    def show_success(self, text: str, **kwargs) -> None:
        self.show_message(text, 'success', **kwargs)

    def show_warning(self, text: str, **kwargs) -> None:
        self.show_message(text, 'warning', **kwargs)

    def show_error(self, text: str, **kwargs) -> None:
        self.show_message(text, 'danger', **kwargs)

    def clear(self) -> None:
        """Прячет полосу"""
        self._label.clear()
        self._action.hide()
        self.hide()

    @property
    def text(self) -> str:
        return self._label.text()

    @property
    def tone(self) -> str:
        return self.property('tone') or ''
