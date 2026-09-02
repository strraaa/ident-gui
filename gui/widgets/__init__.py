"""
Переиспользуемые элементы интерфейса.

Всё, что появляется больше чем на одной странице: полоса сообщения вместо
модального окна, отметка состояния, боковое меню разделов.
"""

from .badge import Badge
from .banner import Banner
from .nav import NavList

__all__ = ['Badge', 'Banner', 'NavList']
