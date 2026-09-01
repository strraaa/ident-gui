"""
Оформление приложения.

Стиль намеренно сдержанный: приложением пользуются администраторы клиники
на рабочем сервере, и читаемость здесь важнее оригинальности.
"""

from PySide6.QtWidgets import QApplication

STYLESHEET = """
QWidget {
    font-size: 13px;
}

QGroupBox {
    font-weight: 600;
    border: 1px solid #d5d9e0;
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
}

QLabel#hint {
    color: #5c6470;
    font-size: 12px;
}

QPushButton {
    padding: 6px 14px;
    border: 1px solid #c4cad3;
    border-radius: 5px;
    background: #f7f8fa;
}

QPushButton:hover:enabled {
    background: #eef1f5;
}

QPushButton:disabled {
    color: #9aa2ad;
    background: #f2f3f5;
}

QPushButton:default {
    border-color: #2563eb;
    font-weight: 600;
}

QLineEdit, QComboBox, QSpinBox {
    padding: 4px 6px;
    border: 1px solid #c4cad3;
    border-radius: 4px;
    background: #ffffff;
    min-height: 20px;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #2563eb;
}

QTableWidget {
    gridline-color: #e5e8ec;
}

QHeaderView::section {
    background: #f2f4f7;
    padding: 6px;
    border: none;
    border-right: 1px solid #e0e4e9;
    border-bottom: 1px solid #d5d9e0;
    font-weight: 600;
}

QTabBar::tab {
    padding: 8px 16px;
}

QPlainTextEdit {
    border: 1px solid #d5d9e0;
    border-radius: 4px;
    background: #ffffff;
}
"""


def apply_theme(app: QApplication):
    """Применяет оформление ко всему приложению"""
    app.setStyle('Fusion')
    app.setStyleSheet(STYLESHEET)
