"""
Оформление приложения.

Цвета и размеры — в `tokens`, сборка QSS и QPalette — в `stylesheet`.
Страницы обращаются к смыслу (`set_tone(label, 'danger')`), а не к цвету.
"""

from .stylesheet import apply_theme, detect_scheme, repolish, set_strong, set_tone
from .tokens import DARK, LIGHT, palette

__all__ = [
    'apply_theme', 'detect_scheme', 'repolish', 'set_strong', 'set_tone',
    'DARK', 'LIGHT', 'palette',
]
