"""
Боковое меню разделов.

Семь вкладок стояли в один ряд, и по ним нельзя было понять, где смотрят,
а где правят: наблюдение и настройка различались только внутренним флагом.
Меню разделяет их явно — наблюдение обновляется само, настройка копит правки
до кнопки сохранения, диагностика ничего не меняет.

Пункт с несохранёнными правками помечается точкой: раньше узнать, на какой
странице что изменено, было нельзя вообще.
"""

from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from gui.theme import tokens

#: пометка несохранённых правок
DIRTY_MARK = ' ●'

_KEY_ROLE = Qt.UserRole
_GROUP_ROLE = Qt.UserRole + 1
_TITLE_ROLE = Qt.UserRole + 2


class NavList(QListWidget):
    """Список разделов и страниц"""

    page_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName('nav')
        self.setFixedWidth(tokens.NAV_WIDTH)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._items: Dict[str, QListWidgetItem] = {}

        self.currentItemChanged.connect(self._on_current_changed)

    # ------------------------------------------------------------------
    # Наполнение
    # ------------------------------------------------------------------

    def add_group(self, title: str) -> None:
        """Заголовок раздела — не выбирается и не реагирует на нажатие"""
        item = QListWidgetItem(title.upper())
        item.setData(_GROUP_ROLE, True)
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(0, 30))

        font = item.font()
        font.setPointSizeF(max(font.pointSizeF() - 1, 7.0))
        font.setBold(True)
        item.setFont(font)

        self.addItem(item)

    def add_page(self, key: str, title: str, hint: str = '') -> None:
        """Страница раздела"""
        item = QListWidgetItem(title)
        item.setData(_KEY_ROLE, key)
        item.setData(_TITLE_ROLE, title)
        item.setSizeHint(QSize(0, 30))

        if hint:
            item.setToolTip(hint)

        self.addItem(item)
        self._items[key] = item

    # ------------------------------------------------------------------
    # Состояние
    # ------------------------------------------------------------------

    def select(self, key: str) -> bool:
        """Переходит на страницу. False — такой страницы нет."""
        item = self._items.get(key)
        if item is None:
            return False

        self.setCurrentItem(item)
        return True

    def select_first(self) -> None:
        for key in self._items:
            self.select(key)
            return

    def current_key(self) -> Optional[str]:
        item = self.currentItem()
        return item.data(_KEY_ROLE) if item else None

    def keys(self) -> List[str]:
        return list(self._items)

    def set_dirty(self, key: str, dirty: bool) -> None:
        """Помечает страницу точкой несохранённых правок"""
        item = self._items.get(key)
        if item is None:
            return

        title = item.data(_TITLE_ROLE)
        item.setText(title + DIRTY_MARK if dirty else title)

    def clear_dirty(self) -> None:
        for key in self._items:
            self.set_dirty(key, False)

    def is_dirty(self, key: str) -> bool:
        item = self._items.get(key)
        return bool(item) and item.text().endswith(DIRTY_MARK)

    # ------------------------------------------------------------------

    def _on_current_changed(self, current: Optional[QListWidgetItem], _previous):
        if current is None or current.data(_GROUP_ROLE):
            return

        key = current.data(_KEY_ROLE)
        if key:
            self.page_selected.emit(key)
