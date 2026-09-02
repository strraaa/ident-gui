"""
Реестр настроек синхронизатора — единственный источник правды.

Одна декларация на параметр описывает всё, что о нём нужно знать: где он лежит
в config.ini, какого типа, что подставить по умолчанию, как подписать в
интерфейсе и что подсказать оператору. Из реестра собираются формы, проверки
и страница «Дополнительно», поэтому новая настройка добавляется правкой
одного файла, а не четырёх.

Состав реестра сверяется с config.example.ini тестом: ключ, появившийся
в образце, но забытый здесь, роняет проверку.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .types import (
    Choice, CsvList, Flag, Int, LeadStatusId, Secret, StageId,
    StatusMapping, Text, Url, UfField, ValueType
)


class Page:
    """Страницы настроек — на них раскладываются группы"""

    CONNECTIONS = 'connections'
    FIELDS = 'fields'
    STAGES = 'stages'
    SYNC = 'sync'
    ADVANCED = 'advanced'


@dataclass(frozen=True)
class Group:
    """Группа параметров: рамка с заголовком на странице"""

    key: str
    page: str
    title: str
    hint: str = ''


GROUPS: Tuple[Group, ...] = (
    Group('db', Page.CONNECTIONS, 'База данных Ident (SQL Server)'),
    Group('b24', Page.CONNECTIONS, 'Портал Битрикс24',
          'Адрес вебхука содержит секретный ключ портала. Права вебхука должны включать CRM.'),

    Group('fields_deal', Page.FIELDS, 'Поля сделки'),
    Group('fields_contact', Page.FIELDS, 'Поля контакта'),
    Group('fields_lead', Page.FIELDS, 'Поля лида'),

    Group('pipeline', Page.STAGES, 'Воронка'),
    Group('stages', Page.STAGES, 'Стадии и статусы',
          'Финальная стадия — сделка считается закрытой и больше не обновляется. '
          'Защищённая — поля обновляются, но стадию служба не переключает.'),

    Group('sync_cycle', Page.SYNC, 'Цикл синхронизации',
          'Интервал действует внутри работающего процесса службы. '
          'Сам процесс запускает задача планировщика.'),
    Group('sync_filial', Page.SYNC, 'Филиалы',
          'ID филиала входит в уникальный идентификатор записи (F1_12345). '
          'Менять его на работающей интеграции нельзя — в Битрикс24 появятся дубли сделок.'),
    Group('sync_queue', Page.SYNC, 'Очередь повторных попыток',
          'Задержка удваивается с каждой попыткой: чем больше попыток, '
          'тем дольше запись переживает недоступность портала.'),
    Group('sync_logging', Page.SYNC, 'Журналирование'),

    Group('monitoring', Page.ADVANCED, 'Мониторинг'),
    Group('notifications', Page.ADVANCED, 'Уведомления по электронной почте'),
    Group('performance', Page.ADVANCED, 'Производительность'),
    Group('security', Page.ADVANCED, 'Безопасность'),
    Group('advanced', Page.ADVANCED, 'Отладка'),
)


@dataclass(frozen=True)
class Setting:
    """Один параметр конфигурации"""

    section: str
    key: str
    type: ValueType
    default: Any
    label: str
    group: str
    hint: str = ''
    required: bool = False
    #: параметр не показывается в обычных формах — только в «Дополнительно»
    legacy: bool = False

    @property
    def ident(self) -> Tuple[str, str]:
        return self.section, self.key


def _uf(section: str, key: str, entity: str, label: str, group: str,
        hint: str = '', required: bool = False, legacy: bool = False) -> Setting:
    """Короткая запись для пользовательского поля Битрикс24"""
    return Setting(section, key, UfField(entity), '', label, group,
                   hint=hint, required=required, legacy=legacy)


SETTINGS: Tuple[Setting, ...] = (

    # ------------------------------------------------------------------
    # Подключение к базе Ident
    # ------------------------------------------------------------------

    Setting('Database', 'server', Text('например, SERVER\\SQLEXPRESS или 192.168.1.10'),
            'localhost', 'Сервер', 'db', required=True),
    Setting('Database', 'port', Int(1, 65535), 1433, 'Порт', 'db'),
    Setting('Database', 'database', Text(), 'IdentDB', 'База данных', 'db', required=True),
    Setting('Database', 'username', Text(), '', 'Пользователь', 'db', required=True),
    Setting('Database', 'password', Secret(), '', 'Пароль', 'db',
            hint='Хранится зашифрованным. Оставьте поле пустым, чтобы не менять.',
            required=True),
    Setting('Database', 'connection_timeout', Int(1, 600, ' с'), 10,
            'Таймаут подключения', 'db'),
    Setting('Database', 'query_timeout', Int(1, 3600, ' с'), 30,
            'Таймаут запроса', 'db'),

    # ------------------------------------------------------------------
    # Портал Битрикс24
    # ------------------------------------------------------------------

    Setting('bitrix', 'webhook_url', Url('https://portal.bitrix24.ru/rest/1/xxxxxxxx/'),
            '', 'Адрес вебхука', 'b24', required=True),
    Setting('bitrix', 'token', Secret(), '', 'Токен', 'b24',
            hint='Устаревший способ доступа. Заполняется, только если вебхук задан частями.',
            legacy=True),
    Setting('bitrix', 'request_timeout', Int(1, 600, ' с'), 30, 'Таймаут запроса', 'b24'),
    Setting('bitrix', 'max_retries', Int(1, 10), 3, 'Повторов при ошибке', 'b24'),
    Setting('bitrix', 'retry_delays', CsvList('секунды через запятую', numeric=True),
            '30,60,300', 'Задержки между повторами', 'b24',
            hint='Секунды до первой, второй и третьей повторной попытки.'),
    Setting('bitrix', 'rate_limit', Int(1, 10, ' запр./сек'), 2,
            'Ограничение частоты', 'b24',
            hint='Вебхук Битрикс24 держит два запроса в секунду. '
                 'При ошибках 429 уменьшите до 1.'),
    Setting('bitrix', 'default_assigned_by_id', Text('ID пользователя, пусто — владелец вебхука'),
            '', 'Ответственный по умолчанию', 'b24'),

    # ------------------------------------------------------------------
    # Воронка и стадии
    # ------------------------------------------------------------------

    Setting('pipelines', 'deal_category_id', Int(0, 10 ** 6), 0, 'Воронка сделок', 'pipeline',
            hint='0 — общая воронка.'),

    Setting('deal_defaults', 'default_stage_id', StageId(), 'NEW',
            'Стадия для несопоставленных статусов', 'stages', required=True),
    Setting('deal_stages', 'mapping', StatusMapping(), '',
            'Соответствие статусов приёма стадиям', 'stages', required=True),
    Setting('deal_stages', 'final', CsvList('идентификаторы стадий'), 'WON,LOSE',
            'Финальные стадии', 'stages'),
    Setting('deal_stages', 'protected', CsvList('идентификаторы стадий'), '',
            'Защищённые стадии', 'stages'),
    Setting('lead_statuses', 'closed', CsvList('идентификаторы статусов'), 'CONVERTED,JUNK',
            'Закрытые статусы лидов', 'stages',
            hint='Лиды в этих статусах не конвертируются в сделки.'),

    # ------------------------------------------------------------------
    # Поля сделки
    # ------------------------------------------------------------------

    _uf('deal_fields', 'ident_id', 'deal', 'Идентификатор записи Ident', 'fields_deal',
        hint='Ключевое поле: по нему находится ранее созданная сделка.', required=True),
    _uf('deal_fields', 'start_time', 'deal', 'Начало приёма', 'fields_deal'),
    _uf('deal_fields', 'end_time', 'deal', 'Окончание приёма', 'fields_deal'),
    _uf('deal_fields', 'doctor_name', 'deal', 'Врач', 'fields_deal'),
    _uf('deal_fields', 'doctor_speciality', 'deal', 'Специальность врача', 'fields_deal'),
    _uf('deal_fields', 'services', 'deal', 'Услуги приёма', 'fields_deal'),
    _uf('deal_fields', 'status', 'deal', 'Статус приёма', 'fields_deal'),
    _uf('deal_fields', 'status_text', 'deal', 'Статус текстом', 'fields_deal'),
    _uf('deal_fields', 'card_number', 'deal', 'Номер карты пациента', 'fields_deal'),
    _uf('deal_fields', 'parent_name', 'deal', 'Представитель пациента', 'fields_deal'),
    _uf('deal_fields', 'comment', 'deal', 'Комментарий', 'fields_deal'),
    _uf('deal_fields', 'cancel_reason_name', 'deal', 'Причина отмены приёма', 'fields_deal',
        hint='Из справочника Ident: «Отказ от приема», «Не пришел» и другие. '
             'Пустое значение — поле не выгружается.'),
    _uf('deal_fields', 'cancel_reason_comment', 'deal', 'Комментарий к отмене', 'fields_deal',
        hint='Свободный текст регистратора при отмене приёма.'),
    _uf('deal_fields', 'cancel_reason', 'deal', 'Комментарий к отмене (устаревший ключ)',
        'fields_deal',
        hint='Читается, только если cancel_reason_comment не заполнен. '
             'Не путать с причиной отмены: здесь хранится комментарий.',
        legacy=True),
    _uf('deal_fields', 'transfer_from', 'deal', 'Перенесена из записи', 'fields_deal',
        hint='Идентификатор исходной записи вида F1_12345, если приём перенесён.'),
    _uf('deal_fields', 'filial', 'deal', 'Филиал', 'fields_deal'),
    _uf('deal_fields', 'armchair', 'deal', 'Кресло', 'fields_deal'),
    _uf('deal_fields', 'order_date', 'deal', 'Дата заказа', 'fields_deal'),
    _uf('deal_fields', 'treatment_plan', 'deal', 'План лечения', 'fields_deal'),
    _uf('deal_fields', 'treatment_plan_hash', 'deal', 'Контрольная сумма плана лечения',
        'fields_deal', hint='Служебное поле: по нему определяется, изменился ли план.'),
    _uf('deal_fields', 'registrar_id', 'deal', 'Регистратор (кто завёл запись)', 'fields_deal',
        hint='Заполняется только при создании сделки.'),
    _uf('deal_fields', 'legacy_card_number', 'deal', 'Номер карты (устаревшее поле)',
        'fields_deal', legacy=True),
    _uf('deal_fields', 'lead_source_id', 'deal', 'ID исходного лида', 'fields_deal',
        hint='Куда бизнес-процесс записывает лид, из которого создана сделка.'),

    # ------------------------------------------------------------------
    # Поля контакта и лида
    # ------------------------------------------------------------------

    _uf('contact_fields', 'card_number', 'contact', 'Номер карты пациента', 'fields_contact'),
    _uf('contact_fields', 'parent_name', 'contact', 'Представитель пациента', 'fields_contact'),

    _uf('lead_fields', 'convert_trigger', 'lead', 'Поле-триггер конвертации лида', 'fields_lead',
        hint='В него записывается ID лида, чтобы бизнес-процесс создал сделку.'),

    # ------------------------------------------------------------------
    # Цикл синхронизации
    # ------------------------------------------------------------------

    Setting('Sync', 'interval_minutes', Int(1, 1440, ' мин'), 2, 'Интервал запуска', 'sync_cycle'),
    Setting('Sync', 'batch_size', Int(1, 1000), 50, 'Записей за цикл', 'sync_cycle'),
    Setting('Sync', 'initial_days', Int(1, 3650, ' дн'), 7, 'Глубина первой загрузки', 'sync_cycle'),
    Setting('Sync', 'enable_update_existing', Flag(), True,
            'Обновлять поля уже существующих сделок', 'sync_cycle'),
    Setting('Sync', 'filial_id', Int(1, 10), 1, 'ID филиала по умолчанию', 'sync_filial'),

    Setting('FilialFilter', 'enabled_filial_ids', CsvList('например, 4, 5', numeric=True, minimum=1, maximum=10),
            '', 'Синхронизировать филиалы', 'sync_filial',
            hint='Пусто — синхронизировать все филиалы.'),
    Setting('FilialFilter', 'default_filial_id', Int(0, 10), 0,
            'Филиал для записей без филиала', 'sync_filial',
            hint='0 — пропускать такие записи.'),

    # ------------------------------------------------------------------
    # Очередь
    # ------------------------------------------------------------------

    Setting('Queue', 'enabled', Flag(), True, 'Использовать очередь при ошибках отправки', 'sync_queue'),
    Setting('Queue', 'max_retry_attempts', Int(1, 20), 3, 'Всего попыток на запись', 'sync_queue'),
    Setting('Queue', 'retry_interval_minutes', Int(1, 600, ' мин'), 5,
            'Базовый интервал повтора', 'sync_queue'),
    Setting('Queue', 'max_size', Int(10, 100000), 1000, 'Максимальный размер очереди', 'sync_queue'),
    Setting('Queue', 'persistence_file', Text('queue.json'), 'queue.json',
            'Файл очереди', 'sync_queue',
            hint='Относительный путь считается от папки службы.'),

    # ------------------------------------------------------------------
    # Журналирование
    # ------------------------------------------------------------------

    Setting('Logging', 'level', Choice([
        ('DEBUG', 'DEBUG — всё подряд'),
        ('INFO', 'INFO — обычный режим'),
        ('WARNING', 'WARNING — только предупреждения'),
        ('ERROR', 'ERROR — только ошибки'),
        ('CRITICAL', 'CRITICAL — только отказы'),
    ]), 'INFO', 'Уровень подробности', 'sync_logging'),
    Setting('Logging', 'log_dir', Text('logs'), 'logs', 'Папка журналов', 'sync_logging',
            hint='Относительный путь считается от папки службы.'),
    Setting('Logging', 'rotation_days', Int(1, 365, ' дн'), 30, 'Хранить журналы', 'sync_logging'),
    Setting('Logging', 'max_log_size_mb', Int(1, 1000, ' МБ'), 20,
            'Размер файла до ротации', 'sync_logging'),
    Setting('Logging', 'max_backup_files', Int(1, 100), 5, 'Файлов в архиве', 'sync_logging'),
    Setting('Logging', 'mask_personal_data', Flag(), True,
            'Маскировать персональные данные в журнале', 'sync_logging'),

    # ------------------------------------------------------------------
    # Мониторинг
    # ------------------------------------------------------------------

    Setting('Monitoring', 'enable_web_interface', Flag(), True,
            'Веб-интерфейс мониторинга', 'monitoring'),
    Setting('Monitoring', 'web_host', Text('localhost'), 'localhost', 'Хост', 'monitoring'),
    Setting('Monitoring', 'web_port', Int(1, 65535), 8080, 'Порт', 'monitoring'),
    Setting('Monitoring', 'enable_metrics', Flag(), True, 'Собирать метрики', 'monitoring'),

    # ------------------------------------------------------------------
    # Уведомления
    # ------------------------------------------------------------------

    Setting('Notifications', 'admin_email', Text('admin@example.com'), '',
            'Адрес администратора', 'notifications'),
    Setting('Notifications', 'smtp_server', Text('smtp.example.ru'), '', 'Сервер SMTP', 'notifications'),
    Setting('Notifications', 'smtp_port', Int(1, 65535), 587, 'Порт SMTP', 'notifications'),
    Setting('Notifications', 'smtp_username', Text(), '', 'Пользователь SMTP', 'notifications'),
    Setting('Notifications', 'smtp_password', Secret(), '', 'Пароль SMTP', 'notifications',
            hint='Хранится зашифрованным.'),
    Setting('Notifications', 'error_threshold_percent', Int(1, 100, ' %'), 10,
            'Порог ошибок за час', 'notifications'),
    Setting('Notifications', 'error_threshold_count', Int(1, 10000), 5,
            'Минимум ошибок для уведомления', 'notifications'),

    # ------------------------------------------------------------------
    # Производительность
    # ------------------------------------------------------------------

    Setting('Performance', 'max_processing_time', Int(1, 600, ' с'), 2,
            'Время на одну запись', 'performance'),
    Setting('Performance', 'batch_pause', Int(0, 600, ' с'), 1, 'Пауза между пакетами', 'performance'),
    Setting('Performance', 'db_pool_size', Int(2, 20), 2, 'Подключений к базе', 'performance',
            hint='Минимум два: генератор записей держит одно соединение, '
                 'а планы лечения запрашиваются параллельно вторым.'),

    # ------------------------------------------------------------------
    # Безопасность
    # ------------------------------------------------------------------

    Setting('Security', 'encryption_key', Secret(encrypted=False), '', 'Ключ шифрования', 'security',
            hint='Создаётся автоматически при первом запуске. Менять вручную не нужно.',
            legacy=True),
    Setting('Security', 'min_tls_version', Choice([('1.2', '1.2'), ('1.3', '1.3')]), '1.2',
            'Минимальная версия TLS', 'security'),
    Setting('Security', 'verify_ssl', Flag(), True, 'Проверять сертификаты SSL', 'security'),

    # ------------------------------------------------------------------
    # Отладка
    # ------------------------------------------------------------------

    Setting('Advanced', 'debug_mode', Flag(), False, 'Режим отладки', 'advanced'),
    Setting('Advanced', 'dry_run', Flag(), False, 'Сухой прогон', 'advanced',
            hint='Данные в Битрикс24 не отправляются, всё только записывается в журнал.'),
    Setting('Advanced', 'skip_validation', Flag(), False, 'Пропускать проверку конфигурации',
            'advanced', hint='На боевом сервере включать не следует.'),
)


# ----------------------------------------------------------------------
# Доступ к реестру
# ----------------------------------------------------------------------

_BY_IDENT: Dict[Tuple[str, str], Setting] = {s.ident: s for s in SETTINGS}
_GROUPS_BY_KEY: Dict[str, Group] = {g.key: g for g in GROUPS}


def get(section: str, key: str) -> Optional[Setting]:
    """Описание параметра или None, если такого в реестре нет"""
    return _BY_IDENT.get((section, key))


def group(key: str) -> Optional[Group]:
    return _GROUPS_BY_KEY.get(key)


def groups_of_page(page: str) -> List[Group]:
    """Группы страницы в порядке объявления"""
    return [g for g in GROUPS if g.page == page]


def settings_of_group(key: str, include_legacy: bool = True) -> List[Setting]:
    """Параметры группы в порядке объявления"""
    return [
        s for s in SETTINGS
        if s.group == key and (include_legacy or not s.legacy)
    ]


def settings_of_page(page: str, include_legacy: bool = True) -> List[Setting]:
    keys = {g.key for g in groups_of_page(page)}
    return [s for s in SETTINGS if s.group in keys and (include_legacy or not s.legacy)]


def secret_idents() -> List[Tuple[str, str]]:
    """Параметры, которые служба хранит зашифрованными"""
    return [s.ident for s in SETTINGS if isinstance(s.type, Secret) and s.type.encrypted]


def hidden_idents() -> List[Tuple[str, str]]:
    """Параметры, значение которых не показывается оператору"""
    return [s.ident for s in SETTINGS if isinstance(s.type, Secret)]


def sections() -> List[str]:
    """Секции конфигурации в порядке появления в реестре"""
    seen = []
    for setting in SETTINGS:
        if setting.section not in seen:
            seen.append(setting.section)
    return seen
