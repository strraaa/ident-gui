"""
Палитра и размеры приложения.

Цвета раньше были зашиты в обработчиках вкладок — 22 вызова setStyleSheet
и 15 разных значений, разбросанных по пяти файлам. Из-за этого не было ни
тёмной темы, ни возможности поменять оттенок в одном месте: на тёмной
системной теме Windows виджеты темнели, а зашитые светлые цвета оставались
и текст сливался с фоном.

Здесь два набора значений с одинаковыми именами. Всё остальное приложение
обращается только к именам: `tokens.DANGER`, а не `#b42318`.

Именование смысловое, а не описательное: `WARNING`, а не `ORANGE`. Оттенок
можно поменять, смысл — нет.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Palette:
    """Один набор цветов — светлый или тёмный"""

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


LIGHT = Palette(
    name='light',

    background='#F4F6F8',
    surface='#FFFFFF',
    surface_alt='#EDF0F4',
    surface_hover='#E4E9EF',

    border='#D5DAE1',
    border_strong='#B9C1CB',

    text='#16202B',
    text_muted='#5C6673',
    text_on_accent='#FFFFFF',

    accent='#2C5AA0',
    accent_hover='#24497F',
    accent_soft='#E3EAF6',

    success='#1A7F37',
    success_soft='#E6F4EA',
    warning='#A04100',
    warning_soft='#FAEEE4',
    danger='#B42318',
    danger_soft='#FBEAE8',
)


DARK = Palette(
    name='dark',

    background='#11161D',
    surface='#1A212B',
    surface_alt='#222B36',
    surface_hover='#2A3541',

    border='#2E3945',
    border_strong='#3D4956',

    text='#E3E8EE',
    text_muted='#97A1AD',
    text_on_accent='#0E141B',

    accent='#7FA8E2',
    accent_hover='#9BBCEA',
    accent_soft='#1E2A3A',

    success='#4FAF6E',
    success_soft='#17281D',
    warning='#D08A45',
    warning_soft='#2B2116',
    danger='#E37A6E',
    danger_soft='#2D1C1B',
)


PALETTES = {'light': LIGHT, 'dark': DARK}


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
NAV_WIDTH = 208

#: минимальный размер окна — ниже формы начинают резаться
WINDOW_MIN_WIDTH = 960
WINDOW_MIN_HEIGHT = 620


def palette(name: str) -> Palette:
    """Набор цветов по имени. Незнакомое имя даёт светлую тему."""
    return PALETTES.get(name, LIGHT)
