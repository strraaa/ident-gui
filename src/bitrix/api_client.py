"""
Модуль взаимодействия с API Битрикс24

Функции:
- Поиск контактов по телефону
- Создание контактов
- Конвертация лидов в контакты и сделки
- Создание/обновление сделок
- Retry логика при ошибках API
- Rate limiting для соблюдения лимитов API
"""

import time
import logging
import threading
import requests
from typing import Dict, Any, Optional, List, Tuple
from functools import wraps
from datetime import datetime, timedelta
from urllib.parse import quote

# Используем настроенный logger из custom_logger_v2
from src.logger.custom_logger_v2 import get_logger
logger = get_logger('ident_integration')


class Bitrix24Error(Exception):
    """Базовая ошибка API Битрикс24"""
    pass


class Bitrix24AuthError(Bitrix24Error):
    """Ошибка аутентификации"""
    pass


class Bitrix24RateLimitError(Bitrix24Error):
    """Превышен лимит запросов"""
    pass


class Bitrix24TransientError(Bitrix24Error):
    """Временная ошибка (таймауты, 5xx, сбои соединения)"""
    pass


class Bitrix24NotFoundError(Bitrix24Error):
    """Сущность не найдена"""
    pass


def retry_on_api_error(max_attempts: Optional[int] = None, delay: float = 2.0, backoff: float = 2.5):
    """
    Декоратор для retry при ошибках API

    ИСПРАВЛЕНИЕ: Дефолты увеличены до 5/2.0/2.5; если задан self.max_retries, используем его.
    Предотвращает ConnectionResetError: более агрессивный retry с большими паузами

    Args:
        max_attempts: Максимальное количество попыток (если None, используем self.max_retries или 5)
        delay: Начальная задержка в секундах (2.0 вместо 1.0)
        backoff: Множитель для экспоненциальной задержки (2.5 вместо 2.0)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            effective_attempts = max_attempts
            if effective_attempts is None and args:
                effective_attempts = getattr(args[0], 'max_retries', None)
            if effective_attempts is None:
                effective_attempts = 5
            if effective_attempts < 1:
                raise ValueError("max_attempts must be >= 1")

            attempt = 0
            current_delay = delay

            while attempt < effective_attempts:
                try:
                    return func(*args, **kwargs)

                except (requests.RequestException, Bitrix24RateLimitError, Bitrix24TransientError) as e:
                    attempt += 1

                    if attempt >= effective_attempts:
                        logger.error(f"API ошибка после {attempt} попыток: {e}")
                        raise

                    logger.warning(
                        f"API ошибка, попытка {attempt}/{effective_attempts} "
                        f"через {current_delay:.1f}с: {e}"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

                except Bitrix24AuthError:
                    # Ошибки аутентификации не ретраим
                    raise

            return None

        return wrapper
    return decorator


class RateLimiter:
    """
    Rate limiter для соблюдения лимитов API Битрикс24

    Лимиты (по умолчанию):
    - 1 запрос в секунду
    - 60 запросов в минуту
    """

    def __init__(self, requests_per_second: float = 1.0, requests_per_minute: int = 60):
        # ИСПРАВЛЕНИЕ: Уменьшено с 2.0 до 1.0 req/sec и 120 до 60 req/min
        # Предотвращает ConnectionResetError от Bitrix24 при высокой нагрузке
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0")

        self.requests_per_second = requests_per_second
        self.requests_per_minute = requests_per_minute

        self.last_request_time = 0
        self.requests_this_minute: List[float] = []
        self.lock = threading.Lock()

    def wait_if_needed(self):
        """Ожидает если нужно соблюсти rate limit"""
        with self.lock:
            now = time.time()

            # Лимит per second
            time_since_last = now - self.last_request_time
            if time_since_last < (1.0 / self.requests_per_second):
                sleep_time = (1.0 / self.requests_per_second) - time_since_last
                time.sleep(sleep_time)
                now = time.time()

            # Лимит per minute
            # Удаляем запросы старше минуты
            cutoff = now - 60.0
            self.requests_this_minute = [t for t in self.requests_this_minute if t > cutoff]

            # Если превышен лимит - ждем
            if len(self.requests_this_minute) >= self.requests_per_minute:
                oldest = self.requests_this_minute[0]
                wait_until = oldest + 60.0
                sleep_time = wait_until - now
                if sleep_time > 0:
                    logger.warning(f"Rate limit: ожидание {sleep_time:.1f}с")
                    time.sleep(sleep_time)
                    now = time.time()

            # Записываем текущий запрос
            self.last_request_time = now
            self.requests_this_minute.append(now)


class Bitrix24Client:
    """
    Клиент для работы с API Битрикс24 через входящий webhook
    """

    def __init__(
        self,
        webhook_url: str,
        request_timeout: int = 30,
        max_retries: int = 5,
        enable_rate_limiting: bool = True,
        default_assigned_by_id: Optional[int] = None
    ):
        """
        Инициализация клиента

        Args:
            webhook_url: URL входящего webhook
            request_timeout: Таймаут запроса в секундах
            max_retries: Максимальное количество повторных попыток
            enable_rate_limiting: Включить ли rate limiting
            default_assigned_by_id: ID ответственного по умолчанию (опционально)
        """
        if not webhook_url or not webhook_url.startswith(('http://', 'https://')):
            raise ValueError(f"Невалидный webhook_url: {webhook_url}")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be > 0")
        if max_retries is not None and max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        self.webhook_url = webhook_url.rstrip('/')
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.default_assigned_by_id = default_assigned_by_id
        # Переиспользуем HTTP-сессию для стабильности и производительности
        self._session = requests.Session()

        # Rate limiter
        self.rate_limiter = RateLimiter() if enable_rate_limiting else None

        # ИСПРАВЛЕНИЕ: Маскируем токен в webhook_url для безопасности (не логируем секретный токен)
        # Формат webhook: https://domain.bitrix24.ru/rest/123/SECRET_TOKEN/
        masked_url = self.webhook_url.split('/rest/')[0] + '/rest/***' if '/rest/' in self.webhook_url else self.webhook_url[:30] + '***'

        if default_assigned_by_id:
            logger.info(f"Bitrix24Client инициализирован: {masked_url} (ответственный: {default_assigned_by_id})")
        else:
            logger.info(f"Bitrix24Client инициализирован: {masked_url}")

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """
        Нормализует телефон к формату +7XXXXXXXXXX (если это возможно).

        Для нестандартных номеров возвращает очищенную строку (без пробелов/скобок/дефисов),
        чтобы можно было безопасно использовать значение как ключ кеша/поиска.
        """
        if not phone or not isinstance(phone, str):
            return ''

        digits = ''.join(c for c in phone if c.isdigit())

        if len(digits) == 11 and digits.startswith('8'):
            return f'+7{digits[1:]}'
        if len(digits) == 11 and digits.startswith('7'):
            return f'+{digits}'
        if len(digits) == 10:
            return f'+7{digits}'

        # Нестандартный формат: возвращаем "очищенную" версию исходного значения
        cleaned = phone.strip().replace(' ', '').replace('(', '').replace(')', '').replace('-', '')
        return cleaned

    @staticmethod
    def _require_value(value: Any, field_name: str) -> Any:
        """Базовая валидация обязательных полей."""
        if value is None:
            raise ValueError(f"{field_name} is required")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"{field_name} is required")
        return value

    @staticmethod
    def _clean_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
        """Убирает None и пустые строки, оставляет 0/False."""
        return {k: v for k, v in fields.items() if v is not None and v != ''}

    @staticmethod
    def _generate_phone_variants(phone: str) -> List[str]:
        """
        Генерирует все возможные варианты формата телефона для поиска дублей.

        Проблема: в Битриксе контакты могут быть созданы с номерами вида:
        +79991234567, 79991234567, 89991234567, +89991234567, 9991234567

        Стандартный поиск по одному формату не находит контакты в другом формате,
        что приводит к созданию дублей.

        Args:
            phone: Нормализованный телефон (+79991234567)

        Returns:
            Список вариантов формата: ['+79991234567', '79991234567', '89991234567', '+89991234567']

        Examples:
            '+79991234567' → ['+79991234567', '79991234567', '89991234567', '+89991234567']
        """
        if not phone or not isinstance(phone, str):
            return []

        normalized = Bitrix24Client._normalize_phone(phone)
        if not normalized:
            return []

        # Извлекаем чистые цифры
        digits = ''.join(c for c in normalized if c.isdigit())

        # Для российских номеров (11 цифр начиная с 7)
        if len(digits) == 11 and digits.startswith('7'):
            base_digits = digits[1:]  # 9991234567
            return [
                f'+7{base_digits}',  # +79991234567 (нормализованный)
                f'7{base_digits}',   # 79991234567 (без +)
                f'8{base_digits}',   # 89991234567 (8 вместо 7)
                f'+8{base_digits}'   # +89991234567 (+8)
            ]

        # Если телефон в нестандартном формате — возвращаем нормализованный вариант как есть
        return [normalized]

    def _get_field_map(self) -> Dict[str, str]:
        from src.config.config_manager_v2 import get_config
        return get_config().get_bitrix_field_map()

    def _make_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Выполняет запрос к API Битрикс24

        Args:
            method: Метод API (например, 'crm.contact.list')
            params: Параметры запроса

        Returns:
            Результат запроса

        Raises:
            Bitrix24Error: При ошибке API
        """
        # Rate limiting
        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()

        url = f"{self.webhook_url}/{method}"

        try:
            response = self._session.post(
                url,
                json=params or {},
                timeout=self.request_timeout
            )

            # Проверяем статус код
            if response.status_code == 401 or response.status_code == 403:
                raise Bitrix24AuthError(
                    f"Ошибка аутентификации (код {response.status_code}). "
                    f"Проверьте webhook токен и права доступа."
                )

            if response.status_code == 429:
                raise Bitrix24RateLimitError("Превышен лимит запросов API")

            if response.status_code >= 500:
                raise Bitrix24TransientError(f"Ошибка сервера Битрикс24: {response.status_code}")

            response.raise_for_status()

            # Парсим JSON
            try:
                data = response.json()
            except ValueError as e:  # JSONDecodeError является подклассом ValueError
                logger.error(
                    f"Битрикс24 вернул невалидный JSON. "
                    f"Status: {response.status_code}, "
                    f"Content: {response.text[:500]}..."
                )
                raise Bitrix24Error(f"Невалидный JSON в ответе от Битрикс24: {e}")

            # Проверяем наличие ошибки в ответе
            if 'error' in data:
                error_code = data.get('error')
                error_description = data.get('error_description', 'Нет описания')

                if error_code == 'QUERY_LIMIT_EXCEEDED':
                    raise Bitrix24RateLimitError(error_description)

                raise Bitrix24Error(f"API ошибка: {error_code} - {error_description}")

            return data

        except requests.Timeout as e:
            raise Bitrix24TransientError(f"Таймаут запроса к API (>{self.request_timeout}с): {e}")

        except requests.ConnectionError as e:
            raise Bitrix24TransientError(f"Ошибка соединения с Битрикс24: {e}")

        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response else "unknown"
            raise Bitrix24Error(f"Ошибка HTTP {status_code}: {e}")

        except requests.RequestException as e:
            raise Bitrix24TransientError(f"Ошибка HTTP запроса: {e}")

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def _find_contact_by_duplicate_api(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет контакт через Duplicate API.
        """
        try:
            normalized = self._normalize_phone(phone)
            result = self._make_request(
                'crm.duplicate.findbycomm',
                {
                    'type': 'PHONE',
                    'values': [normalized],
                    'entity_type': 'CONTACT'
                }
            )

            duplicates = result.get('result', {})
            contact_ids = duplicates.get('CONTACT', []) if isinstance(duplicates, dict) else []
            if not contact_ids:
                return None

            contact_id = contact_ids[0]
            contact_result = self._make_request('crm.contact.get', {'id': contact_id})
            contact = contact_result.get('result')
            return contact if isinstance(contact, dict) else None
        except Exception as e:
            logger.debug(f"Duplicate API error for {phone}: {e}")
            return None

    @retry_on_api_error()
    def _find_lead_by_duplicate_api(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет лид через crm.duplicate.findbycomm (entity_type=LEAD).

        В отличие от crm.lead.list?filter[PHONE]=..., этот метод находит лиды
        и в случае, когда телефон записан непосредственно в самом лиде
        (CONTACT_ID может отсутствовать). Возвращает первый не-закрытый лид.
        """
        try:
            normalized = self._normalize_phone(phone)
            result = self._make_request(
                'crm.duplicate.findbycomm',
                {
                    'type': 'PHONE',
                    'values': [normalized],
                    'entity_type': 'LEAD'
                }
            )

            duplicates = result.get('result', {})
            lead_ids = duplicates.get('LEAD', []) if isinstance(duplicates, dict) else []
            if not lead_ids:
                return None

            from src.config.config_manager_v2 import get_config
            closed_statuses = set(get_config().get_lead_status_config().get('closed', []))

            for lead_id in lead_ids:
                lead_result = self._make_request('crm.lead.get', {'id': lead_id})
                lead = lead_result.get('result')
                if not isinstance(lead, dict):
                    continue
                if lead.get('STATUS_ID') in closed_statuses:
                    continue
                return lead

            return None
        except Exception as e:
            logger.debug(f"Duplicate API (LEAD) error for {phone}: {e}")
            return None

    @retry_on_api_error()
    def find_contact_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет первый контакт по телефону (с поддержкой разных форматов).

        Для предотвращения дублей ищет контакт по всем возможным вариантам формата:
        +79991234567, 79991234567, 89991234567, +89991234567

        Args:
            phone: Телефон (в любом формате, желательно нормализованный)

        Returns:
            Первый найденный контакт или None
        """
        phone = self._require_value(phone, 'phone')

        # Генерируем все варианты формата телефона
        phone_variants = self._generate_phone_variants(phone)

        if not phone_variants:
            logger.warning(f"Не удалось сгенерировать варианты для телефона: {phone}")
            return None

        # Если только один вариант — делаем обычный запрос (оптимизация)
        if len(phone_variants) == 1:
            result = self._make_request(
                'crm.contact.list',
                {
                    'filter': {'PHONE': phone},
                    'select': ['ID', 'NAME', 'LAST_NAME', 'PHONE'],
                    'order': {'DATE_CREATE': 'ASC'}
                }
            )
            contacts = result.get('result', [])
            if contacts:
                contact = contacts[0]
                logger.info(
                    f"Найден контакт {contact['ID']} "
                    f"({contact.get('LAST_NAME', '')} {contact.get('NAME', '')})"
                )
                return contact
            return None

        # Batch-поиск по всем вариантам (защита от дублей)
        commands = {}
        for variant in phone_variants:
            safe_variant = quote(variant, safe='')
            commands[variant] = f"crm.contact.list?filter[PHONE]={safe_variant}&select[]=ID&select[]=NAME&select[]=LAST_NAME&select[]=PHONE&order[DATE_CREATE]=ASC"

        try:
            results = self.batch_execute(commands, raise_on_error=False)
        except Bitrix24Error as e:
            logger.error(f"Batch поиск контакта по телефону завершился ошибкой: {e}")
            return None

        # Ищем первый вариант с результатом
        for variant in phone_variants:
            if variant in results:
                contact_list = results[variant] if isinstance(results[variant], list) else []
                if contact_list:
                    contact = contact_list[0]
                    logger.info(
                        f"Найден контакт {contact['ID']} по варианту {variant} "
                        f"({contact.get('LAST_NAME', '')} {contact.get('NAME', '')})"
                    )
                    return contact

        return None

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def find_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет первый лид по телефону.

        Двухпроходный поиск:
        1) Прямой поиск через crm.duplicate.findbycomm (entity_type=LEAD) —
           находит лиды, у которых телефон записан в самом лиде, даже без контакта.
        2) Fallback: ищем контакт по телефону и берём лид по CONTACT_ID
           (filter[PHONE] для crm.lead.list ненадёжен, поэтому именно через контакт).

        Закрытые статусы (см. [lead_statuses].closed) исключаются.
        """
        phone = self._require_value(phone, 'phone')
        logger.debug(f"Поиск лида по телефону: {phone}")

        # 1) Прямой поиск через Duplicate API
        direct_lead = self._find_lead_by_duplicate_api(phone)
        if direct_lead:
            logger.debug(
                f"Лид {direct_lead.get('ID')} найден через Duplicate API "
                f"(STATUS_ID={direct_lead.get('STATUS_ID')}, CONTACT_ID={direct_lead.get('CONTACT_ID')})"
            )
            return direct_lead

        # 2) Fallback: через контакт
        contact = self.find_contact_by_phone(phone)
        if not contact:
            logger.debug(f"Контакт не найден для {phone}")
            return None

        contact_id = contact.get('ID')
        if not contact_id:
            logger.debug(f"У контакта нет ID для {phone}")
            return None

        # Ищем лид по CONTACT_ID
        logger.debug(f"Найден контакт {contact_id}, ищем лид...")
        result = self._make_request(
            'crm.lead.list',
            {
                'filter': {'CONTACT_ID': contact_id},
                'select': ['ID', 'STATUS_ID', 'CONTACT_ID']
            }
        )

        leads = result.get('result', [])
        logger.debug(f"Найдено лидов для контакта {contact_id}: {len(leads)}")

        # Фильтруем закрытые статусы
        from src.config.config_manager_v2 import get_config
        closed_statuses = set(get_config().get_lead_status_config().get('closed', []))
        leads = [l for l in leads if l.get('STATUS_ID') not in closed_statuses]

        logger.debug(f"Лидов после фильтрации закрытых статусов: {len(leads)}")
        return leads[0] if leads else None

    @retry_on_api_error()
    def get_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Получает данные лида по ID"""
        result = self._make_request('crm.lead.get', {'id': lead_id})
        return result.get('result')

    @retry_on_api_error()
    def update_lead(self, lead_id: int, fields: Dict[str, Any]) -> bool:
        """Обновляет поля лида"""
        result = self._make_request('crm.lead.update', {'id': lead_id, 'fields': fields})
        return bool(result.get('result'))

    def convert_lead(self, lead_id: int, contact_id: int, deal_data: Dict[str, Any]) -> Optional[int]:
        """
        Конвертирует лид в сделку: создаёт сделку и помечает лид как конвертированный.

        crm.lead.convert не доступен в данном аккаунте (404), поэтому конвертация
        реализована как три последовательных запроса:
          1. crm.lead.get — читаем SOURCE_ID и SOURCE_DESCRIPTION из лида
          2. crm.deal.add — создание сделки (с SOURCE_ID из лида)
          3. crm.lead.update — установка STATUS_ID из конфига (closed[0])

        Args:
            lead_id: ID лида для конвертации
            contact_id: ID контакта (уже существует)
            deal_data: Поля сделки (передаются в create_deal)

        Returns:
            ID созданной сделки или None при ошибке
        """
        # Копируем deal_data — convert_lead добавляет SOURCE_ID и не должен мутировать dict вызывающего
        deal_data = dict(deal_data)

        # Читаем лид для переноса SOURCE_ID в сделку
        lead = self.get_lead(lead_id)
        if lead:
            for field in ('SOURCE_ID', 'SOURCE_DESCRIPTION'):
                value = lead.get(field)
                if value:
                    deal_data[field] = value
                    logger.debug(f"Перенос из лида {lead_id}: {field}={value}")

        deal_id = self.create_deal(deal_data, contact_id)

        if not deal_id:
            logger.warning(f"Конвертация лида {lead_id}: create_deal не вернул ID сделки")
            return None

        try:
            # STATUS_ID берём из конфига (closed содержит CONVERTED) — значение case-sensitive
            from src.config.config_manager_v2 import get_config
            closed = get_config().get_lead_status_config().get('closed', [])
            converted_status = next((s for s in closed if 'CONVERT' in s.upper()), 'Converted')

            self.update_lead(lead_id, {'STATUS_ID': converted_status})
            logger.info(f"Лид {lead_id} конвертирован в сделку {deal_id} (статус={converted_status})")
        except Bitrix24Error as e:
            # Сделка уже создана — не откатываем, просто логируем
            logger.warning(f"Сделка {deal_id} создана, но обновление статуса лида {lead_id} не удалось: {e}")

        return deal_id

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def get_deal(self, deal_id: int) -> Optional[Dict[str, Any]]:
        """Получает информацию о сделке"""
        result = self._make_request('crm.deal.get', {'id': deal_id})
        return result.get('result')

    @staticmethod
    def _is_closed_flag(value: Any) -> bool:
        """Parse Bitrix CLOSED marker (Y/N, true/false, 1/0)."""
        if value is None:
            return False
        return str(value).strip().upper() in {'Y', 'YES', 'TRUE', '1'}

    def _is_deal_closed(self, deal: Optional[Dict[str, Any]]) -> bool:
        """
        Determine if a deal is closed.
        Prefer explicit CLOSED flag, fallback to STAGE_ID checks.
        """
        if not isinstance(deal, dict):
            return False

        closed_flag = deal.get('CLOSED')
        if closed_flag not in (None, ''):
            return self._is_closed_flag(closed_flag)

        from src.transformer.data_transformer import StageMapper
        stage_id = deal.get('STAGE_ID')
        if StageMapper.is_stage_final(stage_id):
            return True

        # Support category-prefixed stages like C4:WON when config has WON.
        if isinstance(stage_id, str) and ':' in stage_id:
            return StageMapper.is_stage_final(stage_id.split(':', 1)[1])

        return False

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def create_contact(self, contact_data: Dict[str, Any]) -> int:
        """Создает новый контакт"""
        if not isinstance(contact_data, dict):
            raise ValueError("contact_data must be a dict")
        phone = self._require_value(contact_data.get('phone'), 'contact.phone')

        field_map = self._get_field_map()

        contact_card = field_map.get('contact_card_number')
        contact_parent = field_map.get('contact_parent')

        fields = {
            'NAME': contact_data.get('name', ''),
            'LAST_NAME': contact_data.get('last_name', ''),
            'SECOND_NAME': contact_data.get('second_name', ''),
            'TYPE_ID': contact_data.get('type_id', 'CLIENT'),
            'PHONE': [{'VALUE': phone, 'VALUE_TYPE': 'MOBILE'}],
            contact_card: contact_data.get(contact_card, ''),
            contact_parent: contact_data.get(contact_parent, '')
        }

        if self.default_assigned_by_id:
            fields['ASSIGNED_BY_ID'] = self.default_assigned_by_id

        result = self._make_request('crm.contact.add', {'fields': fields})
        contact_id = result.get('result')

        logger.info(
            f"Создан контакт {contact_id}: "
            f"{fields['LAST_NAME']} {fields['NAME']}"
        )

        return int(contact_id)

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def find_deal_by_ident_id(self, ident_id: str) -> Optional[Dict[str, Any]]:
        """Ищет сделку по IDENT ID.

        Returns:
            - Open deal dict (preferred, newest first) if any exist
            - Closed deal dict with _is_closed=True if ONLY closed deals exist
              (caller must not update it, but must not create a duplicate either)
            - None if no deals exist at all
        """
        field_map = self._get_field_map()
        ident_field = field_map.get('ident_field')

        result = self._make_request(
            'crm.deal.list',
            {
                'filter': {ident_field: ident_id},
                'select': ['ID', 'STAGE_ID', 'CLOSED', 'CONTACT_ID', ident_field],
                'order': {'ID': 'DESC'}  # Newest first
            }
        )

        deals = result.get('result', [])

        if not deals:
            return None

        # Prefer open deal
        for deal in deals:
            if not self._is_deal_closed(deal):
                return deal

        # All deals are closed — return newest with marker so caller knows
        # it must NOT create a duplicate, but also must NOT update
        closed_deal = deals[0].copy()
        closed_deal['_is_closed'] = True
        logger.info(
            f"Все сделки по IDENT ID {ident_id} закрыты "
            f"({len(deals)} шт.), обновление и создание не требуются"
        )
        return closed_deal

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def find_deals_by_contact_without_ident_id(
        self,
        contact_id: int,
        exclude_final: bool = True
    ) -> List[Dict[str, Any]]:
        """Ищет сделки контакта без IDENT ID"""
        field_map = self._get_field_map()
        ident_field = field_map.get('ident_field')

        result = self._make_request(
            'crm.deal.list',
            {
                'filter': {
                    'CONTACT_ID': contact_id,
                    f'={ident_field}': False
                },
                'select': ['ID', 'STAGE_ID', 'CLOSED', 'DATE_CREATE', ident_field],
                'order': {'DATE_CREATE': 'DESC'}
            }
        )

        deals = result.get('result', [])

        if exclude_final:
            deals = [d for d in deals if not self._is_deal_closed(d)]

        return deals

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def create_deal(self, deal_data: Dict[str, Any], contact_id: int) -> int:
        """
        Создает новую сделку

        Args:
            deal_data: Данные сделки
            contact_id: ID контакта

        Returns:
            ID созданной сделки
        """
        try:
            # Формируем поля
            field_map = self._get_field_map()

            deal_start_field = field_map.get('deal_start')
            deal_end_field = field_map.get('deal_end')
            deal_doctor_field = field_map.get('deal_doctor')
            deal_services_field = field_map.get('deal_services')
            deal_status_field = field_map.get('deal_status')
            deal_card_field = field_map.get('deal_card_number')
            deal_parent_field = field_map.get('deal_parent')
            deal_comment_field = field_map.get('deal_comment')
            ident_field = field_map.get('ident_field')
            filial_field = field_map.get('filial')
            armchair_field = field_map.get('armchair')
            status_field = field_map.get('status_field')
            treatment_plan_field = field_map.get('treatment_plan')
            treatment_plan_hash_field = field_map.get('treatment_plan_hash')
            legacy_card_field = field_map.get('legacy_card_number') or None
            order_date_field = field_map.get('order_date') or None
            doctor_speciality_field = field_map.get('doctor_speciality') or None

            from src.config.config_manager_v2 import get_config
            default_stage = get_config().get_deal_defaults().get('default_stage_id')
            fields = {
                'TITLE': deal_data.get('title', 'Сделка'),
                'STAGE_ID': deal_data.get('stage_id', default_stage),
                'CONTACT_ID': contact_id,
                'OPPORTUNITY': deal_data.get('opportunity', 0),
                'CURRENCY_ID': deal_data.get('currency_id', 'RUB'),
                'SOURCE_ID': deal_data.get('SOURCE_ID'),
                'SOURCE_DESCRIPTION': deal_data.get('SOURCE_DESCRIPTION'),

                # Кастомные поля (из конфига)
                deal_start_field: deal_data.get(deal_start_field),  # Дата начало приема
                deal_end_field: deal_data.get(deal_end_field),  # Дата окончания приема
                deal_doctor_field: deal_data.get(deal_doctor_field),  # Врач
                deal_services_field: deal_data.get(deal_services_field),  # Услуги
                deal_status_field: deal_data.get(deal_status_field),  # Статус записи
                deal_card_field: deal_data.get(deal_card_field),  # Номер карты пациента
                deal_parent_field: deal_data.get(deal_parent_field),  # Родитель/Опекун
                deal_comment_field: deal_data.get(deal_comment_field),  #  Комментарий из IDENT

                # Дополнительные поля (для внутреннего использования)
                ident_field: deal_data.get('uf_crm_ident_id'),  # ID из Ident
                filial_field: deal_data.get('uf_crm_filial'),
                armchair_field: deal_data.get('uf_crm_armchair'),
                status_field: deal_data.get('uf_crm_status'),
                **({legacy_card_field: deal_data.get('uf_crm_card_number')} if legacy_card_field else {}),
                **({order_date_field: deal_data.get('uf_crm_order_date')} if order_date_field else {}),
                **({doctor_speciality_field: deal_data.get('uf_crm_doctor_speciality')} if doctor_speciality_field else {}),

                # План лечения
                treatment_plan_field: deal_data.get('uf_crm_treatment_plan'),  # JSON плана лечения
                treatment_plan_hash_field: deal_data.get('uf_crm_treatment_plan_hash'),  # MD5 хеш
            }

            # Устанавливаем воронку для сделки, если задано
            try:
                from src.config.config_manager_v2 import get_config
                category_id = get_config().get_pipeline_config().get('deal_category_id')
                if category_id is not None:
                    fields['CATEGORY_ID'] = category_id
            except Exception as e:
                logger.warning(f"Не удалось получить deal_category_id: {e}")

            # Устанавливаем ответственного если указан в конфиге
            if self.default_assigned_by_id:
                fields['ASSIGNED_BY_ID'] = self.default_assigned_by_id

            # ИСПРАВЛЕНИЕ: Удаляем None и пустые строки (бесполезные для Bitrix24)
            # Оставляем 0 и False (валидные значения)
            fields = self._clean_fields(fields)

            result = self._make_request('crm.deal.add', {'fields': fields})

            deal_id = result.get('result')
            logger.info(
                f"Создана сделка ID={deal_id}: {fields['TITLE']}, "
                f"стадия={fields['STAGE_ID']}, сумма={fields['OPPORTUNITY']}"
            )

            return int(deal_id)

        except Bitrix24Error as e:
            logger.error(f"Ошибка создания сделки: {e}")
            raise

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def update_deal(self, deal_id: int, deal_data: Dict[str, Any]) -> bool:
        """
        Обновляет существующую сделку

        Args:
            deal_id: ID сделки
            deal_data: Новые данные

        Returns:
            True если обновлено успешно
        """
        try:
            # Формируем поля (аналогично create_deal)
            live_deal = self.get_deal(deal_id)
            if live_deal and self._is_deal_closed(live_deal):
                logger.warning(
                    f"BLOCKED: сделка {deal_id} закрыта "
                    f"(STAGE_ID={live_deal.get('STAGE_ID')}, CLOSED={live_deal.get('CLOSED')}). "
                    f"Обновление пропущено."
                )
                return True

            field_map = self._get_field_map()

            deal_start_field = field_map.get('deal_start')
            deal_end_field = field_map.get('deal_end')
            deal_doctor_field = field_map.get('deal_doctor')
            deal_services_field = field_map.get('deal_services')
            deal_status_field = field_map.get('deal_status')
            deal_card_field = field_map.get('deal_card_number')
            deal_parent_field = field_map.get('deal_parent')
            deal_comment_field = field_map.get('deal_comment')
            ident_field = field_map.get('ident_field')
            status_field = field_map.get('status_field')
            treatment_plan_field = field_map.get('treatment_plan')
            treatment_plan_hash_field = field_map.get('treatment_plan_hash')

            # SAFETY: STAGE_ID is deliberately excluded from update payloads.
            # Stage transitions must never happen implicitly via sync.
            # Closed deals are immutable; stage changes require explicit business logic.
            fields = {
                'TITLE': deal_data.get('title'),
                'OPPORTUNITY': deal_data.get('opportunity'),

                # Кастомные поля (из конфига)
                deal_start_field: deal_data.get(deal_start_field),  # Дата начало приема
                deal_end_field: deal_data.get(deal_end_field),  # Дата окончания приема
                deal_doctor_field: deal_data.get(deal_doctor_field),  # Врач
                deal_services_field: deal_data.get(deal_services_field),  # Услуги
                deal_status_field: deal_data.get(deal_status_field),  # Статус записи
                deal_card_field: deal_data.get(deal_card_field),  # Номер карты пациента
                deal_parent_field: deal_data.get(deal_parent_field),  # Родитель/Опекун
                deal_comment_field: deal_data.get(deal_comment_field),  #  Комментарий из IDENT

                # Дополнительные поля (для внутреннего использования)
                ident_field: deal_data.get('uf_crm_ident_id'),  # ID из Ident
                status_field: deal_data.get('uf_crm_status'),

                # План лечения
                treatment_plan_field: deal_data.get('uf_crm_treatment_plan'),  # JSON плана лечения
                treatment_plan_hash_field: deal_data.get('uf_crm_treatment_plan_hash'),  # MD5 хеш
            }

            # ИСПРАВЛЕНИЕ: Удаляем None и пустые строки (бесполезные для Bitrix24)
            # Оставляем 0 и False (валидные значения)
            fields = self._clean_fields(fields)

            # HARD GUARD: STAGE_ID must NEVER appear in update payloads.
            # This is a last-resort safety net — even if caller passes it, we strip it.
            if 'STAGE_ID' in fields:
                logger.error(
                    f"BLOCKED: Попытка обновить STAGE_ID сделки {deal_id}! "
                    f"Значение '{fields['STAGE_ID']}' удалено из payload."
                )
                del fields['STAGE_ID']

            # ИСПРАВЛЕНИЕ: Логируем если поля пустые (помогает диагностировать "фантомные" обновления)
            if not fields:
                logger.warning(
                    f"Попытка обновить сделку {deal_id} с ПУСТЫМИ полями! "
                    f"Все значения были None. Bitrix24 примет запрос, но ничего не изменит."
                )
                return True  # Технически "успешно", но бесполезно

            # ДИАГНОСТИКА: Детальное логирование обновляемых полей
            field_names = list(fields.keys())
            logger.info(
                f"Обновление сделки {deal_id}: {len(fields)} полей "
                f"[{', '.join(field_names[:5])}{'...' if len(field_names) > 5 else ''}]"
            )

            # Логируем ключевые поля для диагностики "пустых" обновлений
            key_fields = ['TITLE', 'OPPORTUNITY', deal_start_field, deal_doctor_field]
            key_values = {k: fields.get(k, '<отсутствует>') for k in key_fields if k in fields}
            if key_values:
                logger.debug(f"Ключевые поля сделки {deal_id}: {key_values}")

            result = self._make_request(
                'crm.deal.update',
                {'id': deal_id, 'fields': fields}
            )

            logger.info(f"✓ Обновлена сделка ID={deal_id} ({len(fields)} полей)")
            return True

        except Bitrix24Error as e:
            logger.error(f"Ошибка обновления сделки {deal_id}: {e}")
            raise

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def batch_execute(self, commands: Dict[str, str], halt_on_error: bool = False, raise_on_error: bool = True) -> Dict[str, Any]:
        """
         BATCH ОПТИМИЗАЦИЯ: Выполняет несколько команд за один запрос

        Args:
            commands: Словарь {command_name: "method?params"}
                     Например: {"contact": "crm.contact.get?id=123"}
            halt_on_error: Остановить выполнение при первой ошибке

        Returns:
            Словарь результатов {command_name: result}

        Example:
            results = client.batch_execute({
                "find_contact": "crm.contact.list?filter[PHONE]=+79991234567",
                "find_deal": "crm.deal.list?filter[{IDENT_FIELD}]=F1_12345"
            })
            contact = results['find_contact']['result'][0]
            deal = results['find_deal']['result'][0]

        Note:
            - Максимум 50 команд в одном batch запросе
            - Команды выполняются параллельно (быстрее чем последовательно)
            - Экономит rate limit (1 запрос вместо N)
        """
        if not commands:
            return {}

        if len(commands) > 50:
            raise ValueError("Batch поддерживает максимум 50 команд за раз")

        try:
            result = self._make_request(
                'batch',
                {
                    'halt': 1 if halt_on_error else 0,
                    'cmd': commands
                }
            )

            batch_result = result.get('result', {})
            result_data = batch_result.get('result', {})

            # Логируем и поднимаем ошибки если есть
            if 'result_error' in batch_result:
                result_error = batch_result['result_error']
                errors_summary = []
                # result_error может быть словарём или списком в зависимости от версии API
                if isinstance(result_error, dict):
                    for cmd_name, error in result_error.items():
                        msg = f"Batch команда '{cmd_name}' завершилась с ошибкой: {error}"
                        errors_summary.append(msg)
                        logger.warning(msg)
                elif isinstance(result_error, list):
                    for error in result_error:
                        msg = f"Batch ошибка: {error}"
                        errors_summary.append(msg)
                        logger.warning(msg)
                else:
                    msg = f"Batch содержит ошибки: {result_error}"
                    errors_summary.append(msg)
                    logger.warning(msg)

                # Считаем batch неуспешным, чтобы избежать тихих потерь данных
                if raise_on_error:
                    raise Bitrix24Error("Batch завершился с ошибками: " + "; ".join(errors_summary))

            logger.debug(f"Batch выполнен: {len(commands)} команд, успешно: {len(result_data)}")

            return result_data

        except Bitrix24Error as e:
            logger.error(f"Ошибка batch запроса: {e}")
            raise

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def batch_find_contacts_by_phones(self, phones: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        BATCH ОПТИМИЗАЦИЯ: Ищет несколько контактов по телефонам за один запрос.

        Для предотвращения дублей ищет каждый телефон по всем вариантам формата:
        +79991234567, 79991234567, 89991234567, +89991234567

        Args:
            phones: Список телефонов

        Returns:
            Словарь {phone: contact_data или None}
        """
        if not phones:
            return {}

        contacts = {}

        # Обрабатываем по чанкам с учетом что каждый телефон генерирует ~4 варианта
        # Лимит Битрикс24 — 50 команд, поэтому берем по 12 телефонов за раз (12*4=48 < 50)
        chunk_size = 12
        for i in range(0, len(phones), chunk_size):
            chunk = phones[i:i + chunk_size]

            # Формируем batch команды для всех вариантов телефонов
            commands = {}
            phone_to_variants = {}

            for phone in chunk:
                variants = self._generate_phone_variants(phone)
                phone_to_variants[phone] = variants

                for variant in variants:
                    safe_variant = quote(str(variant), safe='')
                    # Ключ должен быть уникальным: используем phone::variant
                    key = f"{phone}::{variant}"
                    commands[key] = f"crm.contact.list?filter[PHONE]={safe_variant}&select[]=ID&select[]=NAME&select[]=LAST_NAME&select[]=SECOND_NAME&select[]=PHONE&order[DATE_CREATE]=ASC"

            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.error(f"Batch поиск контактов завершился ошибкой: {e}")
                # Возвращаем None для всех телефонов из chunk
                for phone in chunk:
                    contacts[phone] = None
                continue

            # Парсим результаты для текущего чанка
            for phone in chunk:
                variants = phone_to_variants.get(phone, [])
                found_contact = None

                # Ищем первый вариант с результатом
                for variant in variants:
                    key = f"{phone}::{variant}"
                    if key in results:
                        contact_list = results[key] if isinstance(results[key], list) else []
                        if contact_list:
                            found_contact = contact_list[0]
                            logger.debug(f"Контакт для {phone} найден по варианту {variant}: ID={found_contact.get('ID')}")
                            break

                contacts[phone] = found_contact

        logger.info(f"Batch поиск контактов: запрошено {len(phones)}, найдено {sum(1 for c in contacts.values() if c)}")

        return contacts

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def batch_find_deals_by_ident_ids(self, ident_ids: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
         BATCH ОПТИМИЗАЦИЯ: Ищет несколько сделок по ID из Ident за один запрос

        Args:
            ident_ids: Список уникальных идентификаторов из Ident

        Returns:
            Словарь {ident_id: deal_data или None}
        """
        if not ident_ids:
            return {}

        deals = {}

        field_map = self._get_field_map()
        ident_field = field_map.get('ident_field')

        # Обрабатываем по 50 элементов за раз (лимит Битрикс24)
        for i in range(0, len(ident_ids), 50):
            chunk = ident_ids[i:i + 50]

            # Формируем batch команды для текущего чанка
            commands = {}
            for ident_id in chunk:
                commands[ident_id] = (
                    f"crm.deal.list?filter[{ident_field}]={ident_id}"
                    f"&select[]=ID&select[]=STAGE_ID&select[]=CLOSED&select[]=OPPORTUNITY&select[]={ident_field}"
                    f"&order[ID]=DESC"
                )

            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.error(f"Batch поиск сделок завершился ошибкой: {e}")
                return {ident_id: None for ident_id in ident_ids}

            # Парсим результаты для текущего чанка
            for ident_id in chunk:
                if ident_id in results:
                    deal_list = results[ident_id] if isinstance(results[ident_id], list) else []
                    # Prefer open deal
                    open_deals = [d for d in deal_list if not self._is_deal_closed(d)]
                    if open_deals:
                        deals[ident_id] = open_deals[0]
                    elif deal_list:
                        # All closed — mark so caller skips (no update AND no duplicate)
                        closed = deal_list[0].copy() if isinstance(deal_list[0], dict) else {'ID': deal_list[0]}
                        closed['_is_closed'] = True
                        deals[ident_id] = closed
                    else:
                        deals[ident_id] = None
                else:
                    deals[ident_id] = None

        found_total = sum(1 for d in deals.values() if d)
        found_open = sum(1 for d in deals.values() if d and not d.get('_is_closed'))
        found_closed_only = sum(1 for d in deals.values() if d and d.get('_is_closed'))
        logger.info(
            f"Batch поиск сделок: запрошено {len(ident_ids)}, "
            f"найдено {found_total} (открытых {found_open}, только закрытых {found_closed_only})"
        )
        return deals

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def batch_find_leads_by_contact_ids(self, contact_ids: List[int]) -> Dict[int, Optional[Dict[str, Any]]]:
        """
        BATCH ОПТИМИЗАЦИЯ: Ищет лиды по CONTACT_ID

        Args:
            contact_ids: Список ID контактов

        Returns:
            Словарь {contact_id: lead_data или None}
        """
        if not contact_ids:
            return {}

        leads = {}

        # Обрабатываем по 50 элементов за раз (лимит Битрикс24)
        for i in range(0, len(contact_ids), 50):
            chunk = contact_ids[i:i + 50]

            # Формируем batch команды для текущего чанка
            commands = {}
            for contact_id in chunk:
                commands[str(contact_id)] = f"crm.lead.list?filter[CONTACT_ID]={contact_id}&select[]=ID&select[]=STATUS_ID&select[]=CONTACT_ID"

            logger.debug(f"Batch поиск лидов по CONTACT_ID: чанк {len(chunk)} контактов")

            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.error(f"Batch поиск лидов завершился ошибкой: {e}")
                return {contact_id: None for contact_id in contact_ids}

            # Парсим результаты для текущего чанка
            for contact_id in chunk:
                key = str(contact_id)
                if key in results:
                    lead_list = results[key] if isinstance(results[key], list) else []
                    from src.config.config_manager_v2 import get_config
                    closed_statuses = set(get_config().get_lead_status_config().get('closed', []))
                    filtered = [l for l in lead_list if l.get('STATUS_ID') not in closed_statuses]
                    found_lead = filtered[0] if filtered else None
                    leads[contact_id] = found_lead

                    if found_lead:
                        logger.debug(f"Найден лид {found_lead.get('ID')} для контакта {contact_id}")
                else:
                    leads[contact_id] = None

        logger.info(f"Batch поиск лидов по CONTACT_ID: запрошено {len(contact_ids)}, найдено {sum(1 for l in leads.values() if l)}")

        return leads

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def batch_find_leads_by_phones(self, phones: List[str], contacts_map: Optional[Dict[str, Optional[Dict[str, Any]]]] = None) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        BATCH ОПТИМИЗАЦИЯ: Ищет несколько лидов по телефонам.

        Двухпроходный поиск:
        1) Batch crm.duplicate.findbycomm (entity_type=LEAD) по каждому телефону
           через batch_execute — находит лиды с телефоном, записанным
           непосредственно в самом лиде (даже без контакта).
        2) Для телефонов, по которым прямой поиск ничего не дал — старый путь
           через контакт: phone → CONTACT_ID → crm.lead.list?filter[CONTACT_ID]=...

        Закрытые статусы (см. [lead_statuses].closed) исключаются.

        Args:
            phones: Список телефонов
            contacts_map: Опциональный словарь уже найденных контактов {phone: contact_data}

        Returns:
            Словарь {phone: lead_data или None}
        """
        if not phones:
            return {}

        from src.config.config_manager_v2 import get_config
        closed_statuses = set(get_config().get_lead_status_config().get('closed', []))

        leads: Dict[str, Optional[Dict[str, Any]]] = {phone: None for phone in phones}

        # 1) Batch прямой поиск через Duplicate API (entity_type=LEAD)
        #    crm.duplicate.findbycomm вызывается отдельно на каждый телефон,
        #    но всё уезжает одним HTTP-запросом через batch_execute (чанками по 50).
        direct_lead_ids: Dict[str, List[int]] = {}
        for i in range(0, len(phones), 50):
            chunk = phones[i:i + 50]
            commands = {}
            for phone in chunk:
                try:
                    normalized = self._normalize_phone(phone)
                except Exception:
                    normalized = phone
                safe_value = quote(normalized, safe='')
                commands[phone] = (
                    f"crm.duplicate.findbycomm?type=PHONE"
                    f"&entity_type=LEAD&values[]={safe_value}"
                )

            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.warning(f"Batch Duplicate API (LEAD) завершился ошибкой: {e}")
                results = {}

            for phone in chunk:
                payload = results.get(phone)
                lead_ids: List[int] = []
                if isinstance(payload, dict):
                    raw_ids = payload.get('LEAD', []) or []
                    for raw_id in raw_ids:
                        try:
                            lead_ids.append(int(raw_id))
                        except (ValueError, TypeError):
                            continue
                if lead_ids:
                    direct_lead_ids[phone] = lead_ids

        # Дочитываем лиды одним батчем
        unique_lead_ids = sorted({lid for ids in direct_lead_ids.values() for lid in ids})
        lead_details: Dict[int, Dict[str, Any]] = {}
        for i in range(0, len(unique_lead_ids), 50):
            chunk = unique_lead_ids[i:i + 50]
            commands = {str(lid): f"crm.lead.get?id={lid}" for lid in chunk}
            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.warning(f"Batch crm.lead.get завершился ошибкой: {e}")
                results = {}
            for lid in chunk:
                lead = results.get(str(lid))
                if isinstance(lead, dict) and lead.get('ID'):
                    lead_details[lid] = lead

        # Заполняем результат по телефонам, по которым нашли «прямой» лид
        for phone, lead_ids in direct_lead_ids.items():
            for lid in lead_ids:
                lead = lead_details.get(lid)
                if not lead:
                    continue
                if lead.get('STATUS_ID') in closed_statuses:
                    continue
                leads[phone] = lead
                break

        direct_found = sum(1 for v in leads.values() if v)
        logger.debug(f"Batch Duplicate API (LEAD): найдено {direct_found}/{len(phones)} прямых совпадений")

        # 2) Fallback через контакт — только для тех телефонов, где прямой поиск ничего не дал
        fallback_phones = [phone for phone in phones if leads[phone] is None]
        if fallback_phones:
            if contacts_map is None:
                logger.debug("Контакты не переданы, ищем самостоятельно")
                contacts_map = self.batch_find_contacts_by_phones(fallback_phones)

            phone_to_contact_id: Dict[str, Optional[int]] = {}
            contact_ids: List[int] = []

            for phone in fallback_phones:
                contact = contacts_map.get(phone)
                if contact and contact.get('ID'):
                    try:
                        contact_id = int(contact['ID'])
                        phone_to_contact_id[phone] = contact_id
                        contact_ids.append(contact_id)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Некорректный CONTACT_ID для {phone}: {contact.get('ID')} - {e}")
                        phone_to_contact_id[phone] = None
                else:
                    phone_to_contact_id[phone] = None

            logger.debug(
                f"Fallback: из {len(fallback_phones)} телефонов найдено "
                f"{len(contact_ids)} контактов с ID"
            )

            if contact_ids:
                leads_by_contact_id = self.batch_find_leads_by_contact_ids(contact_ids)
            else:
                leads_by_contact_id = {}

            for phone in fallback_phones:
                contact_id = phone_to_contact_id.get(phone)
                if contact_id:
                    leads[phone] = leads_by_contact_id.get(contact_id)

        logger.info(
            f"Batch поиск лидов: запрошено {len(phones)}, найдено {sum(1 for l in leads.values() if l)} "
            f"(прямых через Duplicate API: {direct_found})"
        )

        return leads

    def test_connection(self) -> bool:
        """
        Тестирует подключение к API

        Returns:
            True если подключение успешно
        """
        try:
            result = self._make_request('crm.contact.list', {'filter': {}, 'select': ['ID']})
            logger.info(" Подключение к Битрикс24 успешно")
            return True

        except Bitrix24AuthError as e:
            logger.error(f"ERROR: Ошибка аутентификации: {e}")
            raise

        except Bitrix24Error as e:
            logger.error(f"ERROR: Ошибка подключения: {e}")
            raise


if __name__ == "__main__":
    """Тестирование клиента"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python api_client.py <webhook_url>")
        sys.exit(1)

    webhook_url = sys.argv[1]

    print("Testing Bitrix24Client...")

    try:
        client = Bitrix24Client(webhook_url)

        # Тест 1: Подключение
        print("\n1. Тест подключения:")
        if client.test_connection():
            print(" Подключение успешно")

        # Тест 2: Поиск контакта
        print("\n2. Тест поиска контакта:")
        test_phone = "+79991234567"
        contact = client.find_contact_by_phone(test_phone)
        if contact:
            print(f" Найден контакт: {contact}")
        else:
            print(f"INFO: Контакт не найден для {test_phone}")

        # Тест 3: Создание контакта
        print("\n3. Тест создания контакта:")
        test_contact = {
            'name': 'Тестовый',
            'last_name': 'Контакт',
            'second_name': 'Иванович',
            'phone': test_phone,
            'type_id': 'CLIENT'
        }

        # Раскомментируйте для реального создания
        # contact_id = client.create_contact(test_contact)
        # print(f" Создан контакт ID={contact_id}")
        print("SKIP:  Пропущено (раскомментируйте для реального теста)")

        print("\n Все тесты пройдены!")

    except Bitrix24AuthError as e:
        print(f"\nERROR: Ошибка аутентификации: {e}")
        sys.exit(1)

    except Bitrix24Error as e:
        print(f"\nERROR: Ошибка API: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"\nERROR: Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
