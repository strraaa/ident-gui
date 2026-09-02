"""
Хранилище настроек: правки в памяти, запись на диск одним действием.

Страницы пишут сюда по мере редактирования, а не в момент сохранения.
Хранилище держит исходное состояние файла и считает разницу — отсюда берутся
и признак несохранённого, и список того, что именно изменится, и решение,
активна ли кнопка сохранения.

Прежняя схема была обратной: значения переносились в конфигурацию только
внутри обработчика «Сохранить», поэтому признак `is_dirty` до сохранения был
всегда ложным и предупреждение при закрытии окна не срабатывало ни разу.

Секреты сюда попадают уже зашифрованными: шифрование выполняет функция,
переданная в конструкторе (в приложении — ConfigManager с DPAPI). Расшифровка
не делается никогда — значение только перезаписывается новым.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import schema, validation
from .ini_document import IniDocument
from .types import Secret

#: сколько резервных копий конфигурации хранить
BACKUP_LIMIT = 10

SECRET_PREFIX = 'DPAPI:'


@dataclass(frozen=True)
class Change:
    """Одна несохранённая правка — для предпросмотра перед записью"""

    section: str
    key: str
    label: str
    before: str
    after: str
    secret: bool = False

    def describe(self) -> str:
        """Строка для показа оператору. Значения секретов не раскрываются."""
        if self.secret:
            state = 'задано' if self.after else 'очищено'
            return f'{self.label}: значение изменено ({state})'

        before = self.before or '— пусто —'
        after = self.after or '— пусто —'
        return f'{self.label}: {before} → {after}'


class SettingsStore:
    """Значения конфигурации с отслеживанием правок"""

    def __init__(self, document: Optional[IniDocument] = None,
                 encryptor: Optional[Callable[[str], str]] = None):
        self.document = document if document is not None else IniDocument()
        self.encryptor = encryptor
        self._pending: Dict[Tuple[str, str], str] = {}

    @classmethod
    def load(cls, path, encryptor: Optional[Callable[[str], str]] = None) -> 'SettingsStore':
        return cls(IniDocument.load(path), encryptor=encryptor)

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    def raw(self, section: str, key: str) -> str:
        """Строка, как она попадёт в файл: правка, затем файл, затем умолчание реестра"""
        ident = (section, key)
        if ident in self._pending:
            return self._pending[ident]

        stored = self.document.get(section, key)
        if stored is not None:
            return stored

        return self._default_text(section, key)

    def stored_raw(self, section: str, key: str) -> str:
        """Строка, записанная в файле сейчас — без учёта несохранённых правок"""
        stored = self.document.get(section, key)
        return stored if stored is not None else self._default_text(section, key)

    def value(self, section: str, key: str) -> Any:
        """Разобранное значение с типом из реестра"""
        setting = schema.get(section, key)
        if setting is None:
            return self.raw(section, key)
        return setting.type.parse(self.raw(section, key), setting.default)

    def is_set(self, section: str, key: str) -> bool:
        """Задано ли непустое значение"""
        return bool(self.raw(section, key).strip())

    def is_encrypted(self, section: str, key: str) -> bool:
        """Хранится ли значение зашифрованным"""
        return self.raw(section, key).strip().startswith(SECRET_PREFIX)

    def unknown_options(self) -> List[Tuple[str, str]]:
        """
        Ключи файла, которых нет в реестре.

        Их нельзя показать формой, но и трогать нельзя: при записи они
        остаются на месте. Список нужен, чтобы сказать об этом оператору.
        """
        found = []
        for section in self.document.sections():
            for key in self.document.options(section):
                if schema.get(section, key.lower()) is None:
                    found.append((section, key))
        return found

    # ------------------------------------------------------------------
    # Правка
    # ------------------------------------------------------------------

    def set_raw(self, section: str, key: str, text: Any) -> None:
        """Записывает строку как есть"""
        self._pending[(section, key)] = '' if text is None else str(text).strip()

    def set_value(self, section: str, key: str, value: Any) -> None:
        """Записывает значение, преобразуя его типом из реестра"""
        setting = schema.get(section, key)
        text = setting.type.format(value) if setting else ('' if value is None else str(value))
        self.set_raw(section, key, text)

    def set_secret(self, section: str, key: str, plaintext: str) -> None:
        """
        Шифрует и записывает секрет. Пустая строка очищает значение.

        Пустой ввод в форме означает «оставить прежний» — решение об этом
        принимает страница, сюда значение доходит уже осмысленным.
        """
        if not plaintext:
            self.set_raw(section, key, '')
            return

        setting = schema.get(section, key)
        needs_encryption = isinstance(setting.type, Secret) and setting.type.encrypted

        if not needs_encryption:
            self.set_raw(section, key, plaintext)
            return

        if self.encryptor is None:
            raise RuntimeError(
                'Шифрование недоступно: не установлен pywin32. '
                'Сохранить пароль в открытом виде приложение не будет.'
            )

        self.set_raw(section, key, self.encryptor(plaintext))

    def discard(self, section: str, key: str) -> None:
        """Отменяет правку одного параметра"""
        self._pending.pop((section, key), None)

    def reset(self) -> None:
        """Отменяет все правки"""
        self._pending.clear()

    # ------------------------------------------------------------------
    # Разница и проверка
    # ------------------------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        return bool(self.changes())

    def changes(self) -> List[Change]:
        """Что изменится при сохранении, в порядке реестра"""
        found = []

        for ident, after in self._pending.items():
            section, key = ident

            # Сравнение идёт с действующим значением, а не с содержимым файла:
            # ключа может не быть в config.ini, и тогда работает умолчание
            # реестра. Без этого запись умолчания обратно в поле считалась бы
            # правкой, и страница помечалась несохранённой на ровном месте.
            before = self.stored_raw(section, key)
            if after == before:
                continue

            setting = schema.get(section, key)
            found.append(Change(
                section=section,
                key=key,
                label=setting.label if setting else f'[{section}].{key}',
                before=before,
                after=after,
                secret=isinstance(setting.type, Secret) if setting else False,
            ))

        order = {s.ident: index for index, s in enumerate(schema.SETTINGS)}
        found.sort(key=lambda change: order.get((change.section, change.key), 10 ** 6))
        return found

    def changed_groups(self) -> List[str]:
        """Группы реестра, в которых есть несохранённые правки"""
        groups = []
        for change in self.changes():
            setting = schema.get(change.section, change.key)
            if setting and setting.group not in groups:
                groups.append(setting.group)
        return groups

    def problems(self) -> List[str]:
        """Претензии к конфигурации с учётом несохранённых правок"""
        return validation.problems(self.raw)

    def stored_problems(self) -> List[str]:
        """Претензии к тому, что записано в файле сейчас"""
        return validation.problems(self.stored_raw)

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------

    def apply(self) -> int:
        """
        Переносит правки в документ, не записывая файл. Возвращает их число.

        Ключ, отсутствующий в файле, дописывается в свою секцию — но только
        если значение отличается от умолчания реестра: иначе конфигурация
        разрасталась бы строками, которые ничего не меняют.
        """
        changes = self.changes()

        for change in changes:
            self.document.set(change.section, change.key, change.after)

        self._pending.clear()
        return len(changes)

    def save(self, path, backup_limit: int = BACKUP_LIMIT) -> Optional[Path]:
        """
        Записывает конфигурацию, сохранив резервную копию прежней.

        Возвращает путь копии или None, если файла ещё не было.
        """
        path = Path(path)
        backup = self._backup(path, backup_limit)

        self.apply()
        self.document.save(path)

        return backup

    # ------------------------------------------------------------------

    @staticmethod
    def _backup(path: Path, limit: int) -> Optional[Path]:
        """
        Копия конфигурации с меткой времени.

        Единственный `config.ini.bak` перезаписывался каждым сохранением,
        поэтому откатиться можно было ровно на один шаг. Здесь копии
        накапливаются, а лишние удаляются по возрасту.
        """
        if not path.exists() or limit <= 0:
            return None

        stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        backup = path.with_name(f'{path.name}.{stamp}.bak')

        backup.write_bytes(path.read_bytes())

        existing = sorted(
            path.parent.glob(f'{path.name}.*.bak'),
            key=lambda p: p.name,
            reverse=True
        )
        for stale in existing[limit:]:
            try:
                stale.unlink()
            except OSError:
                pass

        return backup

    @staticmethod
    def _default_text(section: str, key: str) -> str:
        setting = schema.get(section, key)
        if setting is None:
            return ''
        return setting.type.format(setting.default)
