"""
Типы значений конфигурации.

Каждый тип знает три вещи: как прочитать строку из ini в значение Python,
как записать значение обратно и какие претензии предъявить к тому, что
написано в файле. Из этого же описания страница настроек собирает виджет —
поэтому у типов есть `kind` и параметры вроде диапазона или списка вариантов.

Проверка работает по строке, а не по разобранному значению: в файл попадает
именно строка, и оператору нужно сказать про неё, а не про то, во что она
превратилась после подстановки значения по умолчанию.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

TRUE_WORDS = ('true', 'yes', '1', 'on', 'да')
FALSE_WORDS = ('false', 'no', '0', 'off', 'нет')


class ValueType:
    """Базовый тип значения"""

    #: как рисовать редактор: text, int, flag, choice, list, secret, uf_field, stage, mapping
    kind = 'text'

    def parse(self, raw: Optional[str], default: Any = None) -> Any:
        """Строка из файла в значение. Не выбрасывает исключений."""
        if raw is None:
            return default
        return raw.strip()

    def format(self, value: Any) -> str:
        """Значение в строку для записи в файл"""
        return '' if value is None else str(value)

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        """Что не так с записанным значением. Пустой список — всё в порядке."""
        return []

    def editor_options(self) -> Dict[str, Any]:
        """Параметры редактора для страницы настроек"""
        return {}


class Text(ValueType):
    """Строка"""

    kind = 'text'

    def __init__(self, placeholder: str = '', max_length: int = 0):
        self.placeholder = placeholder
        self.max_length = max_length

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if self.max_length and len(value) > self.max_length:
            return [f'«{label}»: значение длиннее {self.max_length} символов']
        return []

    def editor_options(self) -> Dict[str, Any]:
        return {'placeholder': self.placeholder}


class Int(ValueType):
    """Целое число с диапазоном"""

    kind = 'int'

    def __init__(self, minimum: int = 0, maximum: int = 2 ** 31 - 1, suffix: str = ''):
        self.minimum = minimum
        self.maximum = maximum
        self.suffix = suffix

    def parse(self, raw: Optional[str], default: Any = None) -> Any:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if not value:
            return []

        try:
            number = int(value)
        except ValueError:
            return [f'«{label}»: должно быть числом, указано «{value}»']

        if not self.minimum <= number <= self.maximum:
            return [f'«{label}»: допустимы значения от {self.minimum} до {self.maximum}, указано {number}']

        return []

    def editor_options(self) -> Dict[str, Any]:
        return {'minimum': self.minimum, 'maximum': self.maximum, 'suffix': self.suffix}


class Flag(ValueType):
    """Да или нет"""

    kind = 'flag'

    def parse(self, raw: Optional[str], default: Any = None) -> Any:
        value = (raw or '').strip().lower()
        if value in TRUE_WORDS:
            return True
        if value in FALSE_WORDS:
            return False
        return default

    def format(self, value: Any) -> str:
        return 'True' if value else 'False'

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip().lower()
        if value and value not in TRUE_WORDS + FALSE_WORDS:
            return [f'«{label}»: ожидается True или False, указано «{raw.strip()}»']
        return []


class Choice(ValueType):
    """Выбор из списка"""

    kind = 'choice'

    def __init__(self, options: Sequence[Tuple[str, str]], case_sensitive: bool = False):
        self.options = list(options)     # пары (значение, подпись)
        self.case_sensitive = case_sensitive

    def _values(self) -> List[str]:
        return [value for value, _ in self.options]

    def parse(self, raw: Optional[str], default: Any = None) -> Any:
        value = (raw or '').strip()
        if not value:
            return default

        for option in self._values():
            if option == value or (not self.case_sensitive and option.lower() == value.lower()):
                return option

        return default

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if not value:
            return []
        if self.parse(value) is None:
            allowed = ', '.join(self._values())
            return [f'«{label}»: допустимы значения {allowed}, указано «{value}»']
        return []

    def editor_options(self) -> Dict[str, Any]:
        return {'options': list(self.options)}


class CsvList(ValueType):
    """Список значений через запятую"""

    kind = 'list'

    def __init__(self, item_hint: str = '', numeric: bool = False,
                 minimum: int = 0, maximum: int = 2 ** 31 - 1):
        self.item_hint = item_hint
        self.numeric = numeric
        self.minimum = minimum
        self.maximum = maximum

    def parse(self, raw: Optional[str], default: Any = None) -> List[str]:
        if raw is None:
            return list(default or [])
        return [part.strip() for part in raw.split(',') if part.strip()]

    def format(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return ','.join(str(part).strip() for part in (value or []) if str(part).strip())

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        items = self.parse(raw)
        found = []

        for item in items:
            if self.numeric:
                if not item.isdigit():
                    found.append(f'«{label}»: «{item}» — не число')
                    continue
                if not self.minimum <= int(item) <= self.maximum:
                    found.append(
                        f'«{label}»: «{item}» вне диапазона {self.minimum}–{self.maximum}'
                    )

        duplicates = {item for item in items if items.count(item) > 1}
        if duplicates:
            found.append(f'«{label}»: повторяются значения {", ".join(sorted(duplicates))}')

        return found

    def editor_options(self) -> Dict[str, Any]:
        return {'item_hint': self.item_hint, 'numeric': self.numeric}


class Secret(ValueType):
    """
    Пароль или токен.

    Значение никогда не читается и не показывается: приложение видит только
    факт «задано» и умеет перезаписать его новым.

    `encrypted=False` — для значений, которые прятать нужно, а шифровать нечем:
    ключ шифрования сам собой не шифруется.
    """

    kind = 'secret'

    def __init__(self, encrypted: bool = True):
        self.encrypted = encrypted

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        return []

    def editor_options(self) -> Dict[str, Any]:
        return {'encrypted': self.encrypted}


class Url(ValueType):
    """Адрес http или https"""

    kind = 'text'

    def __init__(self, placeholder: str = ''):
        self.placeholder = placeholder

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if value and not value.startswith(('http://', 'https://')):
            return [f'«{label}»: адрес должен начинаться с http:// или https://']
        return []

    def editor_options(self) -> Dict[str, Any]:
        return {'placeholder': self.placeholder}


class UfField(ValueType):
    """
    Идентификатор пользовательского поля Битрикс24.

    Значение выбирается из справочника портала, но ручной ввод остаётся:
    настраивать приходится и при недоступном портале.
    """

    kind = 'uf_field'

    def __init__(self, entity: str):
        self.entity = entity     # deal, contact, lead, company

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if value and not value.upper().startswith('UF_CRM'):
            return [f'«{label}»: идентификатор должен начинаться с UF_CRM, указано «{value}»']
        return []

    def editor_options(self) -> Dict[str, Any]:
        return {'entity': self.entity}


class StageId(ValueType):
    """Идентификатор стадии сделки — NEW, WON, C2:UC_XXXX"""

    kind = 'stage'

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if ',' in value:
            return [f'«{label}»: запятая разделяет значения в конфигурации и в стадии недопустима']
        return []


class LeadStatusId(ValueType):
    """Идентификатор статуса лида"""

    kind = 'stage'

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        value = (raw or '').strip()
        if ',' in value:
            return [f'«{label}»: запятая разделяет значения в конфигурации']
        return []


class StatusMapping(ValueType):
    """
    Соответствие «статус приёма Ident : стадия сделки», пары через запятую.

    Разделителем служит первое двоеточие, поэтому стадии дополнительных
    воронок вида C2:NEW записываются без потерь.
    """

    kind = 'mapping'

    def parse(self, raw: Optional[str], default: Any = None) -> List[Tuple[str, str]]:
        pairs = []
        for chunk in (raw or '').split(','):
            chunk = chunk.strip()
            if not chunk or ':' not in chunk:
                continue
            status, stage = chunk.split(':', 1)
            pairs.append((status.strip(), stage.strip()))
        return pairs

    def format(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return ','.join(f'{status}:{stage}' for status, stage in value or [] if status and stage)

    def problems(self, raw: Optional[str], label: str) -> List[str]:
        found = []
        statuses = []

        for chunk in (raw or '').split(','):
            chunk = chunk.strip()
            if not chunk:
                continue

            if ':' not in chunk:
                found.append(f'«{label}»: пара «{chunk}» записана без двоеточия')
                continue

            status, stage = (part.strip() for part in chunk.split(':', 1))

            if not status:
                found.append(f'«{label}»: в паре «{chunk}» не указан статус приёма')
            if not stage:
                found.append(f'«{label}»: для статуса «{status}» не указана стадия сделки')

            statuses.append(status)

        duplicates = {status for status in statuses if status and statuses.count(status) > 1}
        for status in sorted(duplicates):
            found.append(f'«{label}»: статус «{status}» указан несколько раз')

        return found
