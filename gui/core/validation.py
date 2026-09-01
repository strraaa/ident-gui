"""
Проверки конфигурации.

Два вида. Первый — по одному параметру, его выполняет сам тип значения
(число в диапазоне, адрес со схемой, пара «статус:стадия» с двоеточием).
Второй — перекрёстный: правила, которые видно только целиком.

Перекрёстные правила берутся из поведения службы. Стадия, одновременно
финальная и защищённая, или одно поле портала под два назначения — это не
опечатка в отдельной строке, а сочетание, из-за которого записи молча
перестают обновляться.
"""

from typing import Callable, Dict, List, Tuple

from . import schema
from .types import CsvList, Secret, UfField

#: чем читаются значения — функция (секция, ключ) -> строка
Reader = Callable[[str, str], str]


def problems(read: Reader) -> List[str]:
    """Все претензии к конфигурации: и по параметрам, и перекрёстные"""
    found = list(setting_problems(read))
    found.extend(cross_problems(read))
    return found


def setting_problems(read: Reader) -> List[str]:
    """Проверка каждого параметра его собственным типом"""
    found = []

    for setting in schema.SETTINGS:
        raw = read(setting.section, setting.key)

        if setting.required and not (raw or '').strip():
            found.append(f'Не заполнено обязательное поле «{setting.label}»')
            continue

        found.extend(setting.type.problems(raw, setting.label))

    return found


def cross_problems(read: Reader) -> List[str]:
    """Правила, которые проверяются только по нескольким параметрам сразу"""
    found = []
    found.extend(_duplicate_portal_fields(read))
    found.extend(_final_and_protected_overlap(read))
    found.extend(_default_filial_outside_filter(read))
    return found


# ----------------------------------------------------------------------


def _duplicate_portal_fields(read: Reader) -> List[str]:
    """
    Одно поле портала под два назначения одной сущности.

    Данные молча затирают друг друга: последняя запись в crm.deal.update
    побеждает, а в журнале ничего не видно.
    """
    seen: Dict[Tuple[str, str], str] = {}
    found = []

    for setting in schema.SETTINGS:
        if not isinstance(setting.type, UfField):
            continue

        value = (read(setting.section, setting.key) or '').strip().upper()
        if not value:
            continue

        key = (setting.type.entity, value)
        if key in seen:
            found.append(
                f'Поле {value} назначено дважды: «{seen[key]}» и «{setting.label}»'
            )
        else:
            seen[key] = setting.label

    return found


def _final_and_protected_overlap(read: Reader) -> List[str]:
    """
    Стадия одновременно финальная и защищённая.

    Списки означают разное — «не трогать сделку вообще» и «не менять стадию», —
    и защита стадии в такой паре не значит ничего: до неё дело не доходит.
    """
    final = _stage_set(read, 'deal_stages', 'final')
    protected = _stage_set(read, 'deal_stages', 'protected')

    overlap = final & protected
    if not overlap:
        return []

    return ['Стадии одновременно финальные и защищённые: ' + ', '.join(sorted(overlap))]


def _default_filial_outside_filter(read: Reader) -> List[str]:
    """
    Филиал для записей без филиала не входит в список синхронизируемых.

    Такие записи будут размечены филиалом, который отбор тут же отбросит,
    то есть просто пропадут.
    """
    enabled = (read('FilialFilter', 'enabled_filial_ids') or '').strip()
    if not enabled:
        return []

    default = (read('FilialFilter', 'default_filial_id') or '').strip()
    if not default or default == '0':
        return []

    allowed = {part.strip() for part in enabled.split(',') if part.strip()}
    if default in allowed:
        return []

    return [
        f'Филиал для записей без филиала ({default}) не входит в список '
        f'синхронизируемых ({", ".join(sorted(allowed))}) — такие записи будут пропущены'
    ]


def _stage_set(read: Reader, section: str, key: str) -> set:
    setting = schema.get(section, key)
    values = setting.type.parse(read(section, key)) if setting else []
    return {str(value).strip().upper() for value in values if str(value).strip()}
