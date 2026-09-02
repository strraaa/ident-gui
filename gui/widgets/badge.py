"""
Отметка состояния.

Состояние службы, результат последнего запуска, счётчик исчерпанных попыток —
всё это раньше было обычным текстом, которому в обработчике дописывали цвет.
Отметка показывает то же самое формой, а не только словом: её видно, не читая.
"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy

from gui.theme import repolish


class Badge(QLabel):
    """Короткая подпись состояния на цветной подложке"""

    def __init__(self, text: str = '—', tone: Optional[str] = None, parent=None):
        super().__init__(text, parent)

        self.setProperty('role', 'badge')
        self.setAlignment(Qt.AlignCenter)
        # По ширине — ровно по тексту: в строке формы иначе растягивается
        # во всю доступную ширину и перестаёт читаться как отметка
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.set_state(text, tone)

    def set_state(self, text: str, tone: Optional[str] = None) -> None:
        """
        Меняет подпись и смысл разом.

        `tone` — success, warning, danger, accent или None для нейтрального.
        """
        self.setText(text)
        self.setProperty('tone', tone)
        repolish(self)
