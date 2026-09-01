"""
Определение рабочей директории службы синхронизации.

Служба ставится в %ProgramFiles%\\IdentBitrix24 (install_task_onedir.ps1), и именно эта
папка является её WorkingDirectory. Значит там лежат config.ini, queue.json,
sync_state.json, treatment_plan_cache.json и каталог logs.
"""

import os
import sys
from pathlib import Path
from typing import Optional

# Имя задачи в планировщике Windows (см. install_task_onedir.ps1)
TASK_NAME = 'IdentBitrix24Integration'
TASK_PATH = '\\IDENT\\'

CONFIG_FILENAME = 'config.ini'
QUEUE_FILENAME = 'queue.json'
SYNC_STATE_FILENAME = 'sync_state.json'
LOG_DIRNAME = 'logs'


def app_dir() -> Path:
    """Папка, из которой запущено само GUI-приложение"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def default_install_dir() -> Path:
    """Штатный каталог установки службы"""
    program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
    return Path(program_files) / 'IdentBitrix24'


def detect_workdir(explicit: Optional[str] = None) -> Path:
    """
    Определяет рабочую директорию службы.

    Порядок поиска:
    1. Явно указанный путь (аргумент командной строки или сохранённый выбор оператора)
    2. Каталог установки службы (%ProgramFiles%\\IdentBitrix24), если там есть config.ini
    3. Папка самого GUI, если config.ini лежит рядом
    4. Текущая рабочая директория — как запасной вариант
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    install_dir = default_install_dir()
    if (install_dir / CONFIG_FILENAME).exists():
        return install_dir

    local_dir = app_dir()
    if (local_dir / CONFIG_FILENAME).exists():
        return local_dir

    if install_dir.exists():
        return install_dir

    return Path.cwd()


class Workspace:
    """Пути к файлам службы внутри выбранной рабочей директории"""

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)

    @property
    def config_path(self) -> Path:
        return self.workdir / CONFIG_FILENAME

    @property
    def example_config_path(self) -> Path:
        """config.example.ini ищем рядом с конфигом, затем рядом с приложением"""
        local = self.workdir / 'config.example.ini'
        if local.exists():
            return local
        return app_dir() / 'config.example.ini'

    @property
    def queue_path(self) -> Path:
        return self.workdir / QUEUE_FILENAME

    @property
    def sync_state_path(self) -> Path:
        return self.workdir / SYNC_STATE_FILENAME

    @property
    def log_dir(self) -> Path:
        return self.workdir / LOG_DIRNAME

    def exists(self) -> bool:
        return self.config_path.exists()

    def __str__(self) -> str:
        return str(self.workdir)
