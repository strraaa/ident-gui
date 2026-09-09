"""
Общий контракт страниц.

Страницы настроек правят конфигурацию в памяти (`apply_to_config`), запись на
диск выполняет главное окно одной кнопкой — чтобы конфиг не переписывался
по частям и не оставался в промежуточном состоянии.

У страницы есть ключ и раздел: по ключу её находит боковое меню и запоминает
приложение, раздел определяет, где она в меню стоит и как себя ведёт.
Наблюдение обновляется само и ничего не сохраняет, настройка копит правки
до кнопки, диагностика не меняет ничего.

Правка поля сообщается наружу сигналом `changed`. Без него страница молчала,
пока её не спросят, а спрашивали только в обработчике сохранения — и признак
несохранённого оставался ложным всегда. Кнопка «Сохранить» из-за этого не
включалась никогда: она ждала правок, о которых ей не сообщали.

`apply_to_config` вызывается теперь после каждой правки, поэтому обязан быть
чистым: перенести значения полей в конфигурацию и ничего больше. Всё, что
нужно сделать один раз после записи на диск, — в `on_saved`.
"""

from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget,
    QPlainTextEdit, QSpinBox, QTableWidget, QTextEdit, QWidget
)

from gui.services.config_service import ConfigService

#: пометка на виджете, что он уже подключён — повторный обход его пропустит
WATCHED_PROPERTY = '_page_watched'

#: чем каждый вид редактора сообщает о правке
#:
#: У строки ввода берётся `textEdited`, а не `textChanged`: первый приходит
#: только от человека, второй — ещё и от `setText` при заполнении страницы.
EDITOR_SIGNALS = (
    (QLineEdit, 'textEdited'),
    (QSpinBox, 'valueChanged'),
    (QDoubleSpinBox, 'valueChanged'),
    (QComboBox, 'currentIndexChanged'),
    (QAbstractButton, 'toggled'),
    (QPlainTextEdit, 'textChanged'),
    (QTextEdit, 'textChanged'),
    (QListWidget, 'itemChanged'),
    (QTableWidget, 'itemChanged'),
)


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

    #: страница изменена оператором
    changed = Signal()

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
        """
        Переносит значения полей в конфигурацию (без записи на диск).

        Выполняется после каждой правки, поэтому должен быть идемпотентным
        и не менять состояние интерфейса.
        """

    def on_saved(self):
        """Вызывается после успешной записи конфигурации на диск"""

    def validate(self) -> List[str]:
        """Проверяет введённые значения. Возвращает список проблем."""
        return []

    def on_activated(self):
        """Вызывается при переходе на страницу"""

    # ------------------------------------------------------------------
    # Отслеживание правок
    # ------------------------------------------------------------------

    def watch_editors(self, root: Optional[QWidget] = None) -> None:
        """
        Подключает редакторы страницы к сигналу `changed`.

        Вызывается в конце сборки интерфейса и повторно — когда редакторы
        появляются по ходу работы (строки таблицы соответствий, списки стадий
        после загрузки справочников портала). Подключённые виджеты помечаются,
        поэтому повторный обход ничего не задваивает.
        """
        root = self if root is None else root

        widgets: List[QWidget] = [] if root is self else [root]
        widgets.extend(root.findChildren(QWidget))

        for widget in widgets:
            if widget.property(WATCHED_PROPERTY):
                continue

            signal_name = self._editor_signal(widget)
            if signal_name is None:
                continue

            getattr(widget, signal_name).connect(self.notify_changed)
            widget.setProperty(WATCHED_PROPERTY, True)

    def notify_changed(self, *args) -> None:
        """Сообщает окну, что страница изменена. Аргументы сигналов не нужны."""
        self.changed.emit()

    @staticmethod
    def _editor_signal(widget: QWidget) -> Optional[str]:
        for widget_type, signal_name in EDITOR_SIGNALS:
            if isinstance(widget, widget_type):
                return signal_name
        return None
