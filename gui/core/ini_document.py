"""
Чтение и запись config.ini без потери комментариев.

configparser разбирает файл в словарь, и обратная запись через `write()` выводит
только секции и пары ключ-значение: комментарии, порядок и отступы теряются.
Для боевого конфига это потеря документации — в нём 92 строки комментариев
на 82 ключа.

Здесь файл хранится как список строк. Изменение значения правит ровно ту строку,
где ключ найден, сохраняя всё остальное: комментарии, пустые строки, отступы,
регистр имён и секции, о которых приложение не знает.

Разбор намеренно повторяет поведение configparser в той конфигурации, в которой
его использует служба (`ConfigParser(interpolation=None)`):

- разделители ключа и значения — `=` и `:`, берётся самый левый;
- комментарий — только строка целиком, начинающаяся с `#` или `;`;
  `key = value # текст` даёт значение `value # текст`, как и у службы;
- имена ключей сравниваются без учёта регистра, имена секций — с учётом;
- строка с отступом продолжает значение предыдущего ключа.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DELIMITERS = ('=', ':')
COMMENT_PREFIXES = ('#', ';')

_SECTION_RE = re.compile(r'^\[(?P<name>[^]]+)\]')


class IniFormatError(ValueError):
    """Файл не разбирается как ini"""


class _Entry:
    """Ключ, найденный в файле, и его место в списке строк"""

    __slots__ = ('section', 'key', 'raw_key', 'prefix', 'start', 'end')

    def __init__(self, section: str, key: str, raw_key: str, prefix: str, start: int, end: int):
        self.section = section    # имя секции как написано в файле
        self.key = key            # имя ключа в нижнем регистре — для поиска
        self.raw_key = raw_key    # имя ключа как написано в файле
        self.prefix = prefix      # текст строки по разделитель включительно
        self.start = start        # индекс строки с ключом
        self.end = end            # индекс последней строки значения (продолжения)


class IniDocument:
    """
    Ini-файл как список строк с доступом по секции и ключу.

    Всё, что не тронуто явным вызовом set/remove, при сохранении остаётся
    байт в байт таким же, включая перевод строки и BOM.
    """

    def __init__(self, lines: Optional[List[str]] = None,
                 newline: str = '\n', bom: bool = False,
                 trailing_newline: bool = True):
        self._lines: List[str] = list(lines or [])
        self.newline = newline
        self.bom = bom
        self.trailing_newline = trailing_newline

        self._entries: Dict[Tuple[str, str], _Entry] = {}
        self._section_order: List[str] = []
        self._section_header_line: Dict[str, int] = {}
        self._reindex()

    # ------------------------------------------------------------------
    # Загрузка и сохранение
    # ------------------------------------------------------------------

    @classmethod
    def loads(cls, text: str) -> 'IniDocument':
        """Разбирает текст конфигурации"""
        bom = text.startswith('﻿')
        if bom:
            text = text[1:]

        newline = '\r\n' if '\r\n' in text else '\n'
        trailing_newline = text.endswith('\n') or text == ''

        lines = text.splitlines()
        return cls(lines, newline=newline, bom=bom, trailing_newline=trailing_newline)

    @classmethod
    def load(cls, path) -> 'IniDocument':
        """
        Читает файл.

        Кодировка utf-8-sig: Блокнот и PowerShell 5.1 сохраняют config.ini с BOM,
        и служба читает его так же (см. ConfigManager).
        """
        raw = Path(path).read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            # Конфиг, сохранённый в кодировке Windows — читаем, чтобы дать
            # оператору починить файл, а не показать ошибку декодирования.
            text = raw.decode('cp1251', errors='replace')

        document = cls.loads(text)
        document.bom = raw.startswith(b'\xef\xbb\xbf')
        return document

    def dumps(self) -> str:
        """Собирает текст файла"""
        text = self.newline.join(self._lines)
        if self.trailing_newline and text:
            text += self.newline
        if self.bom:
            text = '﻿' + text
        return text

    def save(self, path):
        """
        Записывает файл атомарно: сначала во временный, затем подмена.

        При сбое на середине записи не остаётся обрезанного конфига —
        служба при следующем старте прочитает прежний файл целиком.
        """
        path = Path(path)
        temp_path = path.with_name(path.name + '.tmp')

        temp_path.write_text(self.dumps(), encoding='utf-8', newline='')
        temp_path.replace(path)

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    def sections(self) -> List[str]:
        """Имена секций в порядке появления в файле"""
        return list(self._section_order)

    def has_section(self, section: str) -> bool:
        return section in self._section_header_line

    def has_option(self, section: str, key: str) -> bool:
        return (section, key.strip().lower()) in self._entries

    def options(self, section: str) -> List[str]:
        """Ключи секции в порядке появления, как написаны в файле"""
        return [
            entry.raw_key
            for entry in sorted(self._entries.values(), key=lambda e: e.start)
            if entry.section == section
        ]

    def get(self, section: str, key: str, fallback: Optional[str] = None) -> Optional[str]:
        """Значение ключа. Многострочные значения склеиваются через перевод строки."""
        entry = self._entries.get((section, key.strip().lower()))
        if entry is None:
            return fallback

        value = self._lines[entry.start][len(entry.prefix):].strip()

        if entry.end > entry.start:
            parts = [value] if value else []
            parts.extend(self._lines[i].strip() for i in range(entry.start + 1, entry.end + 1))
            value = '\n'.join(part for part in parts if part)

        return value

    def items(self, section: str) -> Dict[str, str]:
        """Все пары ключ-значение секции"""
        return {
            entry.raw_key: self.get(section, entry.key, '')
            for entry in sorted(self._entries.values(), key=lambda e: e.start)
            if entry.section == section
        }

    # ------------------------------------------------------------------
    # Изменение
    # ------------------------------------------------------------------

    def set(self, section: str, key: str, value) -> None:
        """
        Записывает значение.

        Существующий ключ — правится строка на месте, комментарий над ней и
        отступы сохраняются. Новый ключ дописывается в конец своей секции,
        новая секция — в конец файла.
        """
        text = '' if value is None else str(value)
        if '\n' in text or '\r' in text:
            raise ValueError(
                f'Значение [{section}].{key} содержит перевод строки — '
                f'такое значение не записывается в ini'
            )

        entry = self._entries.get((section, key.strip().lower()))

        if entry is not None:
            # Продолжения многострочного значения заменяются одной строкой
            self._lines[entry.start:entry.end + 1] = [entry.prefix + text]
            self._reindex()
            return

        if not self.has_section(section):
            self._append_section(section)

        self._lines.insert(self._insert_position(section), self._format_line(section, key, text))
        self._reindex()

    def remove_option(self, section: str, key: str) -> bool:
        """Удаляет ключ вместе с продолжениями значения. Комментарии не трогает."""
        entry = self._entries.get((section, key.strip().lower()))
        if entry is None:
            return False

        del self._lines[entry.start:entry.end + 1]
        self._reindex()
        return True

    def add_section(self, section: str) -> bool:
        """Создаёт пустую секцию, если её нет"""
        if self.has_section(section):
            return False
        self._append_section(section)
        return True

    # ------------------------------------------------------------------
    # Разбор
    # ------------------------------------------------------------------

    def _reindex(self) -> None:
        """
        Перестраивает указатель на ключи.

        Файл конфигурации — пара сотен строк, поэтому полный разбор после
        каждой правки дешевле, чем аккуратный сдвиг индексов.
        """
        self._entries = {}
        self._section_order = []
        self._section_header_line = {}

        section: Optional[str] = None
        current: Optional[_Entry] = None

        for index, line in enumerate(self._lines):
            stripped = line.strip()

            if not stripped:
                current = None
                continue

            if stripped.startswith(COMMENT_PREFIXES):
                current = None
                continue

            # Отступ продолжает значение предыдущего ключа
            if line[:1].isspace():
                if current is not None:
                    current.end = index
                continue

            match = _SECTION_RE.match(stripped)
            if match:
                section = match.group('name').strip()
                if section not in self._section_header_line:
                    self._section_order.append(section)
                    self._section_header_line[section] = index
                current = None
                continue

            if section is None:
                # Строки до первой секции configparser считает ошибкой;
                # мы их сохраняем как есть и не индексируем.
                current = None
                continue

            parsed = self._split_option(line)
            if parsed is None:
                current = None
                continue

            raw_key, prefix = parsed
            key = raw_key.strip().lower()

            entry = _Entry(section, key, raw_key.strip(), prefix, index, index)
            current = entry

            # Дубль ключа: configparser берёт первый, поведение повторяем
            self._entries.setdefault((section, key), entry)

    @staticmethod
    def _split_option(line: str) -> Optional[Tuple[str, str]]:
        """
        Делит строку на имя ключа и префикс — текст по разделитель включительно
        вместе с пробелами после него.

        Возвращает None, если разделителя нет: такая строка значением не является.
        """
        positions = [line.find(d) for d in DELIMITERS]
        positions = [p for p in positions if p > 0]
        if not positions:
            return None

        cut = min(positions)
        raw_key = line[:cut]

        rest = line[cut + 1:]
        spaces = len(rest) - len(rest.lstrip(' \t'))

        return raw_key, line[:cut + 1 + spaces]

    # ------------------------------------------------------------------
    # Вставка
    # ------------------------------------------------------------------

    def _append_section(self, section: str) -> None:
        """Дописывает заголовок секции в конец файла, отделяя пустой строкой"""
        if self._lines and self._lines[-1].strip():
            self._lines.append('')
        self._lines.append(f'[{section}]')
        self._reindex()

    def _insert_position(self, section: str) -> int:
        """
        Куда вставить новый ключ секции.

        После последней строки значения этой секции, а если значений нет —
        сразу за заголовком. Комментарии и пустые строки перед следующей
        секцией остаются на месте: они относятся к ней, а не к текущей.
        """
        last = None
        for entry in self._entries.values():
            if entry.section == section and (last is None or entry.end > last):
                last = entry.end

        if last is not None:
            return last + 1

        return self._section_header_line[section] + 1

    def _format_line(self, section: str, key: str, value: str) -> str:
        """Новая строка ключа в том же оформлении, что уже принято в секции"""
        for entry in sorted(self._entries.values(), key=lambda e: e.start):
            if entry.section != section:
                continue
            if entry.prefix.endswith((' ', '\t')):
                return f'{key.strip()} = {value}'
            return f'{key.strip()}={value}'

        return f'{key.strip()} = {value}'
