"""
Сборка оформления из токенов.

Одно правило на весь интерфейс: цвет задаётся здесь и нигде больше. Страницы
помечают элемент смыслом — `set_tone(label, 'danger')`, — а как выглядит
опасность, решает тема.

Qt требует двух вещей сразу. QSS красит то, что рисует само приложение, но
не трогает системные части — подсказки, полосы прокрутки, выпадающие списки.
За них отвечает QPalette, поэтому оформление ставится в два приёма.
"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from . import tokens
from .tokens import Palette

#: значения свойства `tone` — смысл, а не цвет
TONES = ('muted', 'success', 'warning', 'danger', 'accent')


def build_stylesheet(p: Palette) -> str:
    """QSS для всего приложения"""
    return f"""
QWidget {{
    font-size: {tokens.FONT_SIZE}px;
    color: {p.text};
}}

QMainWindow, QDialog {{
    background: {p.background};
}}

QToolTip {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 4px 6px;
}}

/* ---------- текст ---------- */

QLabel[tone="muted"]   {{ color: {p.text_muted}; font-size: {tokens.FONT_SIZE_SMALL}px; }}
QLabel[tone="success"] {{ color: {p.success}; }}
QLabel[tone="warning"] {{ color: {p.warning}; }}
QLabel[tone="danger"]  {{ color: {p.danger}; }}
QLabel[tone="accent"]  {{ color: {p.accent}; }}

QLabel[strong="true"] {{ font-weight: 600; }}

QLabel[role="title"] {{
    font-size: {tokens.FONT_SIZE_TITLE}px;
    font-weight: 600;
}}

/* ---------- группы ---------- */

QGroupBox {{
    font-weight: 600;
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {tokens.RADIUS}px;
    margin-top: {tokens.GAP_LARGE - 2}px;
    padding: {tokens.GAP_LARGE}px {tokens.GAP + 2}px {tokens.GAP + 2}px {tokens.GAP + 2}px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {tokens.GAP + 2}px;
    padding: 0 {tokens.GAP_SMALL}px;
}}

/* ---------- кнопки ---------- */

QPushButton {{
    padding: 6px 14px;
    border: 1px solid {p.border_strong};
    border-radius: {tokens.RADIUS}px;
    background: {p.surface};
}}

QPushButton:hover:enabled {{
    background: {p.surface_hover};
}}

QPushButton:pressed:enabled {{
    background: {p.surface_alt};
}}

QPushButton:disabled {{
    color: {p.text_muted};
    background: {p.surface_alt};
    border-color: {p.border};
}}

QPushButton[variant="primary"] {{
    background: {p.accent};
    border-color: {p.accent};
    color: {p.text_on_accent};
    font-weight: 600;
}}

QPushButton[variant="primary"]:hover:enabled {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}

QPushButton[variant="primary"]:disabled {{
    background: {p.surface_alt};
    border-color: {p.border};
    color: {p.text_muted};
}}

QPushButton[variant="quiet"] {{
    border-color: transparent;
    background: transparent;
    color: {p.accent};
}}

QPushButton[variant="quiet"]:hover:enabled {{
    background: {p.accent_soft};
}}

/* ---------- поля ввода ---------- */

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    padding: 4px 6px;
    border: 1px solid {p.border_strong};
    border-radius: {tokens.RADIUS_SMALL}px;
    background: {p.surface};
    selection-background-color: {p.accent};
    selection-color: {p.text_on_accent};
    min-height: 20px;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {p.accent};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {p.surface_alt};
    color: {p.text_muted};
}}

QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}

QCheckBox {{
    spacing: {tokens.GAP}px;
}}

/* ---------- таблицы и списки ---------- */

QTableWidget, QTableView, QListWidget, QTreeView {{
    background: {p.surface};
    alternate-background-color: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {tokens.RADIUS_SMALL}px;
    gridline-color: {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}

QHeaderView::section {{
    background: {p.surface_alt};
    padding: 6px;
    border: none;
    border-right: 1px solid {p.border};
    border-bottom: 1px solid {p.border_strong};
    font-weight: 600;
}}

/* ---------- боковое меню ---------- */

QListWidget#nav {{
    background: {p.surface_alt};
    border: none;
    border-right: 1px solid {p.border};
    border-radius: 0;
    outline: none;
    padding: {tokens.GAP}px 0;
}}

QListWidget#nav::item {{
    padding: 7px {tokens.GAP_LARGE}px;
    border: none;
    color: {p.text};
}}

QListWidget#nav::item:selected {{
    background: {p.accent_soft};
    color: {p.accent};
    font-weight: 600;
}}

QListWidget#nav::item:hover:!selected {{
    background: {p.surface_hover};
}}

QLabel[role="nav-group"] {{
    color: {p.text_muted};
    font-size: {tokens.FONT_SIZE_SMALL - 1}px;
    font-weight: 600;
    padding: {tokens.GAP_LARGE}px {tokens.GAP_LARGE}px {tokens.GAP_SMALL}px;
}}

/* ---------- полосы сообщений ---------- */

QFrame[role="banner"] {{
    border-radius: {tokens.RADIUS}px;
    border: 1px solid {p.border};
    background: {p.surface_alt};
}}

QFrame[role="banner"][tone="success"] {{
    background: {p.success_soft};
    border-color: {p.success};
}}

QFrame[role="banner"][tone="warning"] {{
    background: {p.warning_soft};
    border-color: {p.warning};
}}

QFrame[role="banner"][tone="danger"] {{
    background: {p.danger_soft};
    border-color: {p.danger};
}}

QFrame[role="banner"][tone="accent"] {{
    background: {p.accent_soft};
    border-color: {p.accent};
}}

/* ---------- отметки состояния ---------- */

QLabel[role="badge"] {{
    border-radius: {tokens.RADIUS_SMALL}px;
    padding: 2px 8px;
    font-size: {tokens.FONT_SIZE_SMALL}px;
    font-weight: 600;
    background: {p.surface_alt};
    color: {p.text_muted};
}}

QLabel[role="badge"][tone="success"] {{ background: {p.success_soft}; color: {p.success}; }}
QLabel[role="badge"][tone="warning"] {{ background: {p.warning_soft}; color: {p.warning}; }}
QLabel[role="badge"][tone="danger"]  {{ background: {p.danger_soft};  color: {p.danger}; }}
QLabel[role="badge"][tone="accent"]  {{ background: {p.accent_soft};  color: {p.accent}; }}

/* ---------- разделители и полосы ---------- */

QFrame[role="separator"] {{
    background: {p.border};
    border: none;
    max-height: 1px;
}}

QScrollArea {{
    border: none;
    background: transparent;
}}

QStatusBar {{
    background: {p.surface_alt};
    border-top: 1px solid {p.border};
    color: {p.text_muted};
}}

QSplitter::handle {{
    background: {p.border};
}}
"""


def build_palette(p: Palette) -> QPalette:
    """
    QPalette для системных частей интерфейса.

    Без неё в тёмной теме остаются светлыми подсказки, выпадающие списки и
    полосы прокрутки — то, что Qt рисует мимо QSS.
    """
    qp = QPalette()

    window = QColor(p.background)
    base = QColor(p.surface)
    alt = QColor(p.surface_alt)
    text = QColor(p.text)
    muted = QColor(p.text_muted)
    accent = QColor(p.accent)

    qp.setColor(QPalette.Window, window)
    qp.setColor(QPalette.WindowText, text)
    qp.setColor(QPalette.Base, base)
    qp.setColor(QPalette.AlternateBase, alt)
    qp.setColor(QPalette.Text, text)
    qp.setColor(QPalette.Button, base)
    qp.setColor(QPalette.ButtonText, text)
    qp.setColor(QPalette.ToolTipBase, base)
    qp.setColor(QPalette.ToolTipText, text)
    qp.setColor(QPalette.Highlight, accent)
    qp.setColor(QPalette.HighlightedText, QColor(p.text_on_accent))
    qp.setColor(QPalette.PlaceholderText, muted)
    qp.setColor(QPalette.Link, accent)

    qp.setColor(QPalette.Disabled, QPalette.Text, muted)
    qp.setColor(QPalette.Disabled, QPalette.ButtonText, muted)
    qp.setColor(QPalette.Disabled, QPalette.WindowText, muted)

    return qp


# ----------------------------------------------------------------------


def detect_scheme(app: Optional[QApplication] = None) -> str:
    """
    Тема, выбранная в системе.

    Qt 6.5 и новее сообщает её напрямую; на более старых сборках остаёмся
    на светлой — она безопаснее для приложения, которым пользуются на сервере.
    """
    app = app or QApplication.instance()
    if app is None:
        return 'light'

    try:
        scheme = app.styleHints().colorScheme()
    except AttributeError:
        return 'light'

    return 'dark' if scheme == Qt.ColorScheme.Dark else 'light'


def apply_theme(app: QApplication, mode: str = 'system') -> str:
    """
    Применяет оформление. Возвращает имя применённой темы.

    `mode` — 'system', 'light' или 'dark'.
    """
    name = detect_scheme(app) if mode == 'system' else mode
    p = tokens.palette(name)

    app.setStyle('Fusion')
    app.setPalette(build_palette(p))
    app.setStyleSheet(build_stylesheet(p))

    return name


def set_tone(widget: QWidget, tone: Optional[str]) -> None:
    """
    Помечает элемент смыслом: 'success', 'warning', 'danger', 'muted', 'accent'.

    None снимает пометку. После смены свойства виджет нужно перерисовать —
    Qt не пересчитывает QSS по изменившемуся свойству сам.
    """
    widget.setProperty('tone', tone)
    repolish(widget)


def set_strong(widget: QWidget, strong: bool = True) -> None:
    """Выделяет элемент насыщенностью шрифта"""
    widget.setProperty('strong', 'true' if strong else None)
    repolish(widget)


def repolish(widget: QWidget) -> None:
    """Заставляет Qt перечитать QSS после смены свойства"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
