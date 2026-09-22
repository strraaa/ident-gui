"""Компактная навигационная панель в стиле desktop rail."""

from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from gui.theme import repolish, tokens

DIRTY_MARK = ' ●'
_KEY_ROLE = Qt.UserRole
_GROUP_ROLE = Qt.UserRole + 1
_TITLE_ROLE = Qt.UserRole + 2


class NavList(QWidget):
    """Навигация с компактным rail и раскрытием подписей."""

    page_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._items: Dict[str, QListWidgetItem] = {}
        self._groups: List[QListWidgetItem] = []

        self.setObjectName('navigationView')
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 8, 6, 8)
        self._layout.setSpacing(4)

        self.toggle = QPushButton('☰')
        self.toggle.setObjectName('navigationToggle')
        self.toggle.setProperty('variant', 'quiet')
        self.toggle.setFixedHeight(36)
        self.toggle.setToolTip('Свернуть навигацию')
        self.toggle.clicked.connect(lambda: self.set_expanded(not self._expanded))
        self._layout.addWidget(self.toggle)

        self.list = QListWidget()
        self.list.setObjectName('nav')
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self._layout.addWidget(self.list, stretch=1)
        self.set_expanded(False, animated=False)

    def add_group(self, title: str) -> None:
        item = QListWidgetItem(title.upper())
        item.setData(_GROUP_ROLE, True)
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(0, 30))
        self.list.addItem(item)
        self._groups.append(item)

    def add_page(self, key: str, title: str, hint: str = '') -> None:
        item = QListWidgetItem(title)
        item.setData(_KEY_ROLE, key)
        item.setData(_TITLE_ROLE, title)
        item.setSizeHint(QSize(0, 36))
        if hint:
            item.setToolTip(hint)
        self.list.addItem(item)
        self._items[key] = item

    def set_expanded(self, expanded: bool, animated: bool = True) -> None:
        del animated
        self._expanded = bool(expanded)
        width = tokens.NAV_EXPANDED_WIDTH if self._expanded else tokens.NAV_COLLAPSED_WIDTH
        self.setFixedWidth(width)
        self.toggle.setText('☰' if self._expanded else '›')
        self.toggle.setToolTip(
            'Свернуть навигацию' if self._expanded else 'Развернуть навигацию'
        )
        for item in self._groups:
            item.setHidden(not self._expanded)
        for item in self._items.values():
            title = item.data(_TITLE_ROLE)
            item.setText(title if self._expanded else title[:1])
            item.setTextAlignment(
                Qt.AlignLeft | Qt.AlignVCenter if self._expanded
                else Qt.AlignHCenter | Qt.AlignVCenter
            )
        repolish(self)

    def select(self, key: str) -> bool:
        item = self._items.get(key)
        if item is None:
            return False
        self.list.setCurrentItem(item)
        return True

    def select_first(self) -> None:
        for key in self._items:
            self.select(key)
            return

    def current_key(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(_KEY_ROLE) if item else None

    def keys(self) -> List[str]:
        return list(self._items)

    def set_dirty(self, key: str, dirty: bool) -> None:
        item = self._items.get(key)
        if item is None:
            return
        title = item.data(_TITLE_ROLE)
        item.setText((title if self._expanded else title[:1]) + (DIRTY_MARK if dirty else ''))

    def clear_dirty(self) -> None:
        for key in self._items:
            self.set_dirty(key, False)

    def is_dirty(self, key: str) -> bool:
        item = self._items.get(key)
        return bool(item) and item.text().endswith(DIRTY_MARK)

    def _on_current_changed(self, current, _previous):
        if current is None or current.data(_GROUP_ROLE):
            return
        key = current.data(_KEY_ROLE)
        if key:
            self.page_selected.emit(key)
