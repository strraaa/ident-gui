"""
Палитра и размеры приложения.

Цвета раньше были зашиты в обработчиках вкладок — 22 вызова setStyleSheet
и 15 разных значений, разбросанных по пяти файлам. Из-за этого не было ни
тёмной темы, ни возможности поменять оттенок в одном месте: на тёмной
системной теме Windows виджеты темнели, а зашитые светлые цвета оставались
и текст сливался с фоном.

Всё приложение обращается к смысловым именам палитры, а не к hex-цветам.

Именование смысловое, а не описательное: `WARNING`, а не `ORANGE`. Оттенок
можно поменять, смысл — нет.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Palette:
    """Единый набор цветов приложения."""

    name: str

    # Поверхности
    background: str        # фон окна
    surface: str           # карточки, группы, поля ввода
    surface_alt: str       # заголовки таблиц, чередование строк, боковое меню
    surface_hover: str

    # Границы
    border: str
    border_strong: str     # разделители, на которых держится структура

    # Текст
    text: str
    text_muted: str        # подсказки, второстепенное
    text_on_accent: str

    # Основной цвет
    accent: str
    accent_hover: str
    accent_soft: str       # фон выделенного пункта меню, подложка подсказки

    # Состояния
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str

    def as_dict(self) -> Dict[str, str]:
        return {
            key: value for key, value in self.__dict__.items()
            if key != 'name'
        }


DARK = Palette(
    name='dark',

    background='#0F131A',
    surface='#151A22',
    surface_alt='#11161D',
    surface_hover='#202936',

    border='#2A3340',
    border_strong='#3B4656',

    text='#E7ECF4',
    text_muted='#9AA7B8',
    text_on_accent='#07101F',

    accent='#5B9BFF',
    accent_hover='#78ACFF',
    accent_soft='#172A47',

    success='#4FAF6E',
    success_soft='#17281D',
    warning='#D08A45',
    warning_soft='#2B2116',
    danger='#E37A6E',
    danger_soft='#2D1C1B',
)


PALETTES = {'dark': DARK}


# ----------------------------------------------------------------------
# Размеры — одинаковые в обеих темах
# ----------------------------------------------------------------------

#: шаг сетки отступов
GAP = 8
GAP_SMALL = 4
GAP_LARGE = 16

RADIUS = 5
RADIUS_SMALL = 3

FONT_SIZE = 13
FONT_SIZE_SMALL = 12
FONT_SIZE_TITLE = 15

#: ширина бокового меню
NAV_COLLAPSED_WIDTH = 56
NAV_EXPANDED_WIDTH = 220

#: минимальный размер окна — ниже формы начинают резаться
WINDOW_MIN_WIDTH = 960
WINDOW_MIN_HEIGHT = 620


def palette(name: str) -> Palette:
    """Возвращает единственную палитру приложения."""
    return DARK
