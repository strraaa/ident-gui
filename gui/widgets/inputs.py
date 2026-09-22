"""Qt-контролы с безопасным поведением при прокрутке."""

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox


class NoWheelSpinBox(QSpinBox):
    """Числовое поле не меняется от случайного движения колеса мыши."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Дробное числовое поле не меняется от случайного движения колеса мыши."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelComboBox(QComboBox):
    """Список не переключает значение при прокрутке родительской формы."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()
