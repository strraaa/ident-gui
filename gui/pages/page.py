"""
Общий контракт страниц.

Страницы настроек правят конфигурацию в памяти (`apply_to_config`), запись на
диск выполняет главное окно одной кнопкой — чтобы конфиг не переписывался
по частям и не оставался в промежуточном состоянии.

У страницы есть ключ и раздел: по ключу её находит боковое меню и запоминает
приложение, раздел определяет, где она в меню стоит и как себя ведёт.
Наблюдение обновляется само и ничего не сохраняет, настройка копит правки
до кнопки, диагностика не меняет ничего.
"""

from typing import List

from PySide6.QtWidgets import QWidget

from gui.services.config_service import ConfigService


class Section:
    """Разделы бокового меню"""

    MONITOR = 'monitor'
    SETTINGS = 'settings'
    DIAGNOSTICS = 'diagnostics'


SECTION_TITLES = {
    Section.MONITOR: 'Наблюдение',
    Section.SETTINGS: 'Настройка',
    Section.DIAGNOSTICS: 'Диагностика',
}


class Page(QWidget):
    """Базовая страница"""

    #: ключ страницы — им она адресуется в меню и в сохранённых настройках
    key = ''

    #: заголовок в меню
    title = ''

    #: подсказка при наведении
    hint = ''

    #: раздел меню
    section = Section.SETTINGS

    #: участвует ли страница в сохранении настроек
    is_settings = False

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(parent)
        self.config = config

    def load_from_config(self):
        """Заполняет поля страницы значениями из конфигурации"""

    def apply_to_config(self):
        """Переносит значения полей в конфигурацию (без записи на диск)"""

    def validate(self) -> List[str]:
        """Проверяет введённые значения. Возвращает список проблем."""
        return []

    def on_activated(self):
        """Вызывается при переходе на страницу"""
