"""
Работа с config.ini из GUI.

Здесь сходятся две вещи. Правкой и записью занимается SettingsStore: он держит
исходное состояние файла, копит изменения и пишет их так, чтобы комментарии,
порядок ключей и незнакомые секции остались нетронутыми.

ConfigManager остаётся рядом ровно для двух задач, которые кроме него делать
некому: расшифровать секреты для подключения к базе и порталу и зашифровать
новый секрет через DPAPI. Значения оттуда не показываются оператору никогда.

Отличия от загрузки конфигурации в службе:
- валидация не блокирует загрузку (невалидный конфиг нужно открыть и починить);
- config.example.ini не подмешивается, иначе сохранение записало бы в боевой
  файл плейсхолдеры вроде `your_password`.
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.config_manager_v2 import ConfigManager, DPAPI_AVAILABLE

from gui.core import schema
from gui.core.store import Change, SettingsStore

from .paths import Workspace


class ConfigService:
    """Чтение, правка и сохранение конфигурации службы"""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.store: Optional[SettingsStore] = None
        self.manager: Optional[ConfigManager] = None
        self.load_error: Optional[str] = None

        # Отпечаток файла на момент чтения: по нему видно, что конфигурацию
        # правил кто-то ещё, пока она была открыта здесь
        self._signature: Optional[tuple] = None

    # ------------------------------------------------------------------
    # Загрузка и сохранение
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """
        Загружает конфигурацию. Возвращает False, если файл не удалось прочитать
        (текст ошибки — в load_error).
        """
        self.load_error = None

        if not self.workspace.config_path.exists():
            self.load_error = f'Файл не найден: {self.workspace.config_path}'
            self.store = None
            self.manager = None
            return False

        try:
            self.manager = self._open_manager()
            self.store = SettingsStore.load(
                self.workspace.config_path,
                encryptor=self._encrypt
            )
            self._signature = self._file_signature()
            return True
        except Exception as e:
            self.store = None
            self.manager = None
            self.load_error = str(e)
            return False

    def create_from_example(self) -> bool:
        """
        Создаёт config.ini из config.example.ini.

        Копируется именно файл, а не разобранная конфигурация: вместе со
        значениями оператор получает и комментарии, которыми образец объясняет,
        что означает каждый параметр.
        """
        example = self.workspace.example_config_path
        if not example.exists():
            self.load_error = f'Файл-образец не найден: {example}'
            return False

        self.workspace.config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, self.workspace.config_path)
        return self.load()

    def save(self) -> Optional[Path]:
        """
        Записывает изменения. Возвращает путь резервной копии прежнего файла.

        После записи ConfigManager перечитывается: расшифрованные значения
        для проверки подключений должны соответствовать тому, что теперь в файле.
        """
        self._require_loaded()

        backup = self.store.save(self.workspace.config_path)
        self._signature = self._file_signature()

        try:
            self.manager = self._open_manager()
        except Exception:
            # Файл уже записан. Если перечитать его не удалось, проверки
            # подключения перестанут работать, но настройки не потеряны.
            self.manager = None

        return backup

    # ------------------------------------------------------------------
    # Доступ к значениям
    # ------------------------------------------------------------------

    def get(self, section: str, option: str, fallback: str = '') -> str:
        """Значение как строка. Отсутствующий ключ берётся из реестра настроек."""
        if not self.store:
            return fallback

        if schema.get(section, option) is None and not self.store.document.has_option(section, option):
            return fallback

        return self.store.raw(section, option)

    def get_int(self, section: str, option: str, fallback: int = 0) -> int:
        value = self.value(section, option)
        return value if isinstance(value, int) and not isinstance(value, bool) else fallback

    def get_bool(self, section: str, option: str, fallback: bool = False) -> bool:
        value = self.value(section, option)
        return value if isinstance(value, bool) else fallback

    def value(self, section: str, option: str) -> Any:
        """Значение, разобранное типом из реестра настроек"""
        if not self.store:
            return None
        return self.store.value(section, option)

    def set(self, section: str, option: str, value: Any):
        self._require_loaded()
        self.store.set_value(section, option, value)

    def set_secret(self, section: str, option: str, plaintext: str):
        """Шифрует и записывает секрет. Пустая строка очищает значение."""
        self._require_loaded()
        self.store.set_secret(section, option, plaintext)

    def changed_on_disk(self) -> bool:
        """
        Изменился ли config.ini после того, как приложение его прочитало.

        Хранилище держит снимок файла с момента открытия и при сохранении
        записывает его целиком. Если за это время файл правил кто-то ещё —
        второй администратор, скрипт, блокнот, — его правки будут стёрты
        молча. Спросить об этом должно окно.
        """
        if self._signature is None:
            return False
        return self._file_signature() != self._signature

    def _file_signature(self) -> Optional[tuple]:
        try:
            stat = self.workspace.config_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def discard(self, section: str, option: str):
        """
        Отменяет несохранённую правку одного параметра.

        Нужно полям, у которых пустой ввод означает «оставить как было»:
        секретам. Без этого набранное и стёртое значение осталось бы
        в конфигурации обрезанным.
        """
        if self.store:
            self.store.discard(section, option)

    def is_encrypted(self, section: str, option: str) -> bool:
        return bool(self.store) and self.store.is_encrypted(section, option)

    def has_value(self, section: str, option: str) -> bool:
        return bool(self.store) and self.store.is_set(section, option)

    # ------------------------------------------------------------------
    # Состояние
    # ------------------------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        """Есть ли правки, которых нет в файле"""
        return bool(self.store) and self.store.is_dirty

    def changes(self) -> List[Change]:
        """Что именно изменится при сохранении"""
        return self.store.changes() if self.store else []

    def changed_groups(self) -> List[str]:
        """Группы настроек с несохранёнными правками — для пометок в интерфейсе"""
        return self.store.changed_groups() if self.store else []

    def discard_changes(self):
        if self.store:
            self.store.reset()

    def validate(self) -> List[str]:
        """Список проблем конфигурации с учётом несохранённых правок"""
        if not self.store:
            return [self.load_error or 'Конфигурация не загружена']

        problems = list(self.store.problems())

        if not DPAPI_AVAILABLE:
            for section, option in schema.secret_idents():
                if self.store.is_encrypted(section, option):
                    problems.append(
                        'В конфигурации есть зашифрованные значения, '
                        'но модуль pywin32 не установлен — расшифровать их нечем'
                    )
                    break

        return problems

    @property
    def encryption_available(self) -> bool:
        """Доступно ли шифрование (на не-Windows системах pywin32 отсутствует)"""
        return DPAPI_AVAILABLE

    # ------------------------------------------------------------------
    # Производные пути и подключения
    # ------------------------------------------------------------------

    def webhook_url(self) -> Optional[str]:
        """
        Собирает URL вебхука для запросов к порталу.

        Токен расшифровывается только здесь и только для обращения к API —
        в интерфейсе он не показывается.
        """
        if not self.manager:
            return None
        try:
            return self.manager.get_bitrix24_config().get('webhook_url')
        except Exception:
            return None

    def database_config(self) -> Optional[Dict[str, Any]]:
        """Параметры подключения к базе с расшифрованным паролем"""
        if not self.manager:
            return None
        return self.manager.get_database_config()

    def queue_file_path(self) -> Path:
        """Путь к файлу очереди с учётом настройки [Queue].persistence_file"""
        return self._resolve_path(self.get('Queue', 'persistence_file'), self.workspace.queue_path)

    def log_dir_path(self) -> Path:
        """Путь к каталогу логов с учётом настройки [Logging].log_dir"""
        return self._resolve_path(self.get('Logging', 'log_dir'), self.workspace.log_dir)

    # ------------------------------------------------------------------

    def _resolve_path(self, configured: str, default: Path) -> Path:
        configured = (configured or '').strip()
        if not configured:
            return default

        path = Path(configured)
        return path if path.is_absolute() else self.workspace.workdir / path

    def _open_manager(self) -> ConfigManager:
        return ConfigManager(
            str(self.workspace.config_path),
            require_config=False,
            validate_on_load=False,
            use_example_defaults=False
        )

    def _encrypt(self, plaintext: str) -> str:
        """
        Шифрование секрета через DPAPI.

        Выполняет ConfigManager: у службы и приложения должен быть один
        механизм, иначе служба не прочитает то, что записало приложение.
        """
        if not self.manager:
            raise RuntimeError('Конфигурация не загружена')
        return self.manager._encrypt_value(plaintext)

    def _require_loaded(self):
        if not self.store:
            raise RuntimeError('Конфигурация не загружена')
