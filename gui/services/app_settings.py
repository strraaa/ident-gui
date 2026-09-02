"""
Что приложение помнит между запусками.

Раньше не помнило ничего: размер окна, папку службы и открытый раздел
приходилось задавать заново каждый раз, а нештатную папку — ещё и ключом
командной строки.

Хранится это в реестре Windows через QSettings, а не в config.ini: конфигурация
принадлежит службе и одинакова для всех, кто её открывает, а привычки окна —
личное дело того, кто за ним сидит.
"""

from typing import Optional

from PySide6.QtCore import QByteArray, QSettings

ORGANIZATION = 'IDENT Integration'
APPLICATION = 'Настройки Ident'

KEY_GEOMETRY = 'window/geometry'
KEY_STATE = 'window/state'
KEY_WORKDIR = 'workspace/workdir'
KEY_PAGE = 'window/page'
KEY_THEME = 'appearance/theme'

THEME_MODES = ('system', 'light', 'dark')


class AppSettings:
    """Настройки самого приложения — не службы"""

    def __init__(self, settings: Optional[QSettings] = None):
        # Готовый QSettings принимается ради тестов: иначе они писали бы
        # в реестр той машины, на которой запущены
        self._settings = settings or QSettings(ORGANIZATION, APPLICATION)

    # ------------------------------------------------------------------
    # Окно
    # ------------------------------------------------------------------

    def geometry(self) -> Optional[QByteArray]:
        value = self._settings.value(KEY_GEOMETRY)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_geometry(self, value: QByteArray) -> None:
        self._settings.setValue(KEY_GEOMETRY, value)

    def page(self) -> Optional[str]:
        value = self._settings.value(KEY_PAGE)
        return str(value) if value else None

    def set_page(self, key: str) -> None:
        self._settings.setValue(KEY_PAGE, key)

    # ------------------------------------------------------------------
    # Рабочая папка
    # ------------------------------------------------------------------

    def workdir(self) -> Optional[str]:
        value = self._settings.value(KEY_WORKDIR)
        return str(value) if value else None

    def set_workdir(self, path) -> None:
        self._settings.setValue(KEY_WORKDIR, str(path))

    # ------------------------------------------------------------------
    # Оформление
    # ------------------------------------------------------------------

    def theme(self) -> str:
        """system, light или dark"""
        value = str(self._settings.value(KEY_THEME) or 'system')
        return value if value in THEME_MODES else 'system'

    def set_theme(self, mode: str) -> None:
        self._settings.setValue(KEY_THEME, mode if mode in THEME_MODES else 'system')

    # ------------------------------------------------------------------

    def sync(self) -> None:
        """Сбрасывает накопленное на диск — вызывается при закрытии окна"""
        self._settings.sync()
