"""
Общий контракт вкладок.

Вкладки настроек только правят объект конфигурации в памяти (apply_to_config).
Запись на диск выполняет главное окно одной кнопкой «Сохранить» — чтобы конфиг
не переписывался по частям и не оставался в промежуточном состоянии.
"""

from typing import List

from PySide6.QtWidgets import QWidget

from gui.services.config_service import ConfigService


class BaseTab(QWidget):
    """Базовая вкладка"""

    #: показывать ли вкладку в списке тех, что участвуют в сохранении настроек
    is_settings_tab = False

    #: заголовок вкладки
    title = ''

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(parent)
        self.config = config

    def load_from_config(self):
        """Заполняет поля вкладки значениями из конфигурации"""

    def apply_to_config(self):
        """Переносит значения полей в конфигурацию (без записи на диск)"""

    def validate(self) -> List[str]:
        """Проверяет введённые значения. Возвращает список проблем."""
        return []

    def on_activated(self):
        """Вызывается при переключении на вкладку"""
