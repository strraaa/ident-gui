"""
Работа с config.ini из GUI.

Важные особенности по сравнению с загрузкой конфига в службе:
- валидация не блокирует загрузку (невалидный конфиг нужно открыть и починить);
- config.example.ini не подмешивается, иначе сохранение записало бы в боевой файл
  плейсхолдеры вроде `your_password`;
- секреты никогда не расшифровываются: GUI показывает лишь факт «значение зашифровано»
  и умеет перезаписать его новым.
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config.config_manager_v2 import ConfigManager, DPAPI_AVAILABLE

from .paths import Workspace


class ConfigService:
    """Чтение, правка и сохранение конфигурации службы"""

    # Поля, которые хранятся в зашифрованном виде
    SECRET_FIELDS: List[Tuple[str, str]] = [
        ('Database', 'password'),
        ('bitrix', 'token'),
        ('Notifications', 'smtp_password'),
    ]

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.manager: Optional[ConfigManager] = None
        self.load_error: Optional[str] = None
        self._dirty = False

    # ------------------------------------------------------------------
    # Загрузка и сохранение
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """
        Загружает конфигурацию. Возвращает False, если файл не удалось прочитать
        (текст ошибки — в load_error).
        """
        self.load_error = None
        self._dirty = False

        if not self.workspace.config_path.exists():
            self.load_error = f"Файл не найден: {self.workspace.config_path}"
            self.manager = None
            return False

        try:
            self.manager = ConfigManager(
                str(self.workspace.config_path),
                require_config=False,
                validate_on_load=False,
                use_example_defaults=False
            )
            return True
        except Exception as e:
            self.manager = None
            self.load_error = str(e)
            return False

    def create_from_example(self) -> bool:
        """Создаёт config.ini из config.example.ini (для первичной настройки)"""
        example = self.workspace.example_config_path
        if not example.exists():
            self.load_error = f"Файл-образец не найден: {example}"
            return False

        self.workspace.config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, self.workspace.config_path)
        return self.load()

    def save(self):
        """Сохраняет изменения на диск"""
        self._require_loaded()
        self.manager.save()
        self._dirty = False

    def backup(self) -> Optional[Path]:
        """Делает копию конфига перед сохранением. Возвращает путь копии."""
        source = self.workspace.config_path
        if not source.exists():
            return None

        backup_path = source.with_suffix('.ini.bak')
        shutil.copyfile(source, backup_path)
        return backup_path

    # ------------------------------------------------------------------
    # Доступ к значениям
    # ------------------------------------------------------------------

    def get(self, section: str, option: str, fallback: str = '') -> str:
        if not self.manager:
            return fallback
        return self.manager.config.get(section, option, fallback=fallback)

    def get_int(self, section: str, option: str, fallback: int = 0) -> int:
        raw = self.get(section, option, str(fallback))
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return fallback

    def get_bool(self, section: str, option: str, fallback: bool = False) -> bool:
        raw = self.get(section, option, '').strip().lower()
        if raw in ('true', 'yes', '1', 'on'):
            return True
        if raw in ('false', 'no', '0', 'off'):
            return False
        return fallback

    def set(self, section: str, option: str, value: Any):
        self._require_loaded()
        self.manager.set_value(section, option, value)
        self._dirty = True

    def set_secret(self, section: str, option: str, plaintext: str):
        """Шифрует и записывает секрет. Пустая строка очищает значение."""
        self._require_loaded()
        self.manager.set_secret(section, option, plaintext)
        self._dirty = True

    def is_encrypted(self, section: str, option: str) -> bool:
        if not self.manager:
            return False
        return self.manager.is_encrypted(section, option)

    def has_value(self, section: str, option: str) -> bool:
        return bool(self.get(section, option, '').strip())

    def section_items(self, section: str) -> Dict[str, str]:
        """Все пары ключ-значение секции (пустой словарь, если секции нет)"""
        if not self.manager or not self.manager.config.has_section(section):
            return {}
        return dict(self.manager.config.items(section))

    def remove_option(self, section: str, option: str):
        if not self.manager or not self.manager.config.has_section(section):
            return
        if self.manager.config.remove_option(section, option):
            self._dirty = True

    # ------------------------------------------------------------------
    # Служебное
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Возвращает список проблем конфигурации (пустой — всё в порядке)"""
        if not self.manager:
            return [self.load_error or 'Конфигурация не загружена']
        try:
            return self.manager.validate()
        except Exception as e:
            return [f'Ошибка валидации: {e}']

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def encryption_available(self) -> bool:
        """Доступно ли шифрование (на не-Windows системах pywin32 отсутствует)"""
        return DPAPI_AVAILABLE

    def webhook_url(self) -> Optional[str]:
        """
        Собирает URL вебхука для запросов к порталу.

        Токен расшифровывается только здесь и только для обращения к API —
        в интерфейсе он не показывается.
        """
        if not self.manager:
            return None
        try:
            b24 = self.manager.get_bitrix24_config()
            return b24.get('webhook_url')
        except Exception:
            return None

    def queue_file_path(self) -> Path:
        """Путь к файлу очереди с учётом настройки [Queue].persistence_file"""
        configured = self.get('Queue', 'persistence_file', '').strip()
        if not configured:
            return self.workspace.queue_path

        path = Path(configured)
        if path.is_absolute():
            return path
        return self.workspace.workdir / path

    def log_dir_path(self) -> Path:
        """Путь к каталогу логов с учётом настройки [Logging].log_dir"""
        configured = self.get('Logging', 'log_dir', '').strip()
        if not configured:
            return self.workspace.log_dir

        path = Path(configured)
        if path.is_absolute():
            return path
        return self.workspace.workdir / path

    def _require_loaded(self):
        if not self.manager:
            raise RuntimeError('Конфигурация не загружена')
