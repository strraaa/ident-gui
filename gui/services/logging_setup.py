"""
Журнал приложения настроек.

Приложение работает у заказчика без консоли: собрано с `--noconsole`, поэтому
всё, что программа пишет в stderr, уходит в никуда. Пока журнала не было,
о любой неполадке можно было узнать только со слов оператора — «нажал, и
ничего не произошло».

Файл живёт в профиле пользователя, а не в папке службы. Причин две: писать
в `%ProgramFiles%` без прав администратора нельзя, а журнал службы трогать
нельзя тем более — её процесс держит свой файл открытым, и ротация чужого
процесса ломается.

Здесь же перехватывается журнал службы. Модули `src.*` при импорте вызывают
`get_logger('ident_integration')`, и тот заводит `logs/integration_log.txt`
относительно текущего каталога — то есть в папке службы, если приложение
запущено оттуда. Свой обработчик, поставленный заранее, это предотвращает:
`get_logger` видит готовые обработчики и своих не добавляет.

ВАЖНО: `configure()` нужно вызывать до первого импорта модулей `src.*`.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

#: журнал самого приложения
APP_LOGGER = 'ident_settings'

#: журнал, который заводят модули службы при импорте
SERVICE_LOGGER = 'ident_integration'

LOG_FILENAME = 'ident_settings.log'
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

FORMAT = '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
DATE_FORMAT = '%d.%m.%Y %H:%M:%S'

_log_path: Optional[Path] = None


def default_log_dir() -> Path:
    """Каталог журнала в профиле пользователя"""
    base = os.environ.get('LOCALAPPDATA')

    if not base:
        # Не Windows — при разработке и в тестах
        base = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')

    return Path(base) / 'IdentSettings'


def log_path() -> Optional[Path]:
    """Путь к файлу журнала — чтобы показать его оператору"""
    return _log_path


def configure(directory: Optional[Path] = None, level: int = logging.INFO) -> Optional[Path]:
    """
    Заводит файл журнала и подчиняет ему журнал службы.

    Возвращает путь к файлу или None, если писать не удалось (каталог только
    для чтения, диск заполнен). Отсутствие журнала не повод не запускаться.
    """
    global _log_path

    directory = Path(directory) if directory else default_log_dir()

    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / LOG_FILENAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding='utf-8'
        )
    except OSError:
        return None

    handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    handler.setLevel(logging.DEBUG)

    app_logger = logging.getLogger(APP_LOGGER)
    app_logger.setLevel(level)
    if not app_logger.handlers:
        app_logger.addHandler(handler)

    # Тот же файл для сообщений модулей службы: пусть пишут сюда, а не
    # в журнал работающего процесса синхронизации
    service_logger = logging.getLogger(SERVICE_LOGGER)
    service_logger.setLevel(logging.WARNING)
    service_logger.propagate = False
    if not service_logger.handlers:
        service_logger.addHandler(handler)

    _log_path = directory / LOG_FILENAME
    return _log_path


def logger(name: str = '') -> logging.Logger:
    """Журнал приложения; `name` — подсистема, например `logger('очередь')`"""
    return logging.getLogger(f'{APP_LOGGER}.{name}' if name else APP_LOGGER)


def describe_environment() -> str:
    """Строка о среде запуска — первое, что нужно в журнале при разборе жалобы"""
    from gui import __version__

    kind = 'сборка' if getattr(sys, 'frozen', False) else 'исходники'
    return (
        f'Настройки Ident → Битрикс24 {__version__} ({kind}), '
        f'Python {sys.version.split()[0]}, {sys.platform}'
    )
