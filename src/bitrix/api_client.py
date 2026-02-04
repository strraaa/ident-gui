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
    def find_contact_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Ищет первый контакт по телефону"""
        phone = self._require_value(phone, 'phone')
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

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def find_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет первый лид по телефону

        ВАЖНО: filter[PHONE] для лидов НЕ РАБОТАЕТ если телефон в контакте!
        Поэтому ищем через контакт: phone → CONTACT_ID → lead
        """
        phone = self._require_value(phone, 'phone')
        logger.debug(f"Поиск лида по телефону: {phone}")

        # Сначала ищем контакт
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
        """Ищет сделку по IDENT ID"""
        field_map = self._get_field_map()
        ident_field = field_map.get('ident_field')

        result = self._make_request(
            'crm.deal.list',
            {
                'filter': {ident_field: ident_id},
                'select': ['ID', 'STAGE_ID', 'CONTACT_ID', ident_field]
            }
        )

        deals = result.get('result', [])
        return deals[0] if deals else None

    @retry_on_api_error()  # ИСПРАВЛЕНИЕ: Используем дефолтные значения (max_attempts=5, delay=2.0, backoff=2.5)
    def find_deals_by_contact_without_ident_id(
        self,
        contact_id: int,
        exclude_final: bool = True
    ) -> List[Dict[str, Any]]:
        """Ищет сделки контакта без IDENT ID"""
        from src.transformer.data_transformer import StageMapper

        field_map = self._get_field_map()
        ident_field = field_map.get('ident_field')

        result = self._make_request(
            'crm.deal.list',
            {
                'filter': {
                    'CONTACT_ID': contact_id,
                    f'={ident_field}': False
                },
                'select': ['ID', 'STAGE_ID', 'DATE_CREATE', ident_field],
                'order': {'DATE_CREATE': 'DESC'}
            }
        )

        deals = result.get('result', [])

        if exclude_final:
            deals = [d for d in deals if not StageMapper.is_stage_final(d.get('STAGE_ID'))]

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

            fields = {
                'TITLE': deal_data.get('title'),
                'STAGE_ID': deal_data.get('stage_id'),
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
            key_fields = ['TITLE', 'STAGE_ID', 'OPPORTUNITY', deal_start_field, deal_doctor_field]
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
         BATCH ОПТИМИЗАЦИЯ: Ищет несколько контактов по телефонам за один запрос

        Args:
            phones: Список телефонов

        Returns:
            Словарь {phone: contact_data или None}
        """
        if not phones:
            return {}

        contacts = {}

        # Обрабатываем по 50 элементов за раз (лимит Битрикс24)
        for i in range(0, len(phones), 50):
            chunk = phones[i:i + 50]

            # Формируем batch команды для текущего чанка
            commands = {}
            for phone in chunk:
                # Экранируем специальные символы в телефоне для использования в query string
                safe_phone = quote(str(phone), safe='')
                commands[phone] = f"crm.contact.list?filter[PHONE]={safe_phone}&select[]=ID&select[]=NAME&select[]=LAST_NAME&select[]=SECOND_NAME&select[]=PHONE"

            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.error(f"Batch поиск контактов завершился ошибкой: {e}")
                return {phone: None for phone in phones}

            # Парсим результаты для текущего чанка
            for phone in chunk:
                if phone in results:
                    # Результат уже является списком контактов
                    contact_list = results[phone] if isinstance(results[phone], list) else []
                    contacts[phone] = contact_list[0] if contact_list else None
                else:
                    contacts[phone] = None

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
                commands[ident_id] = f"crm.deal.list?filter[{ident_field}]={ident_id}&select[]=ID&select[]=STAGE_ID&select[]=OPPORTUNITY&select[]={ident_field}"

            try:
                results = self.batch_execute(commands, raise_on_error=False)
            except Bitrix24Error as e:
                logger.error(f"Batch поиск сделок завершился ошибкой: {e}")
                return {ident_id: None for ident_id in ident_ids}

            # Парсим результаты для текущего чанка
            for ident_id in chunk:
                if ident_id in results:
                    # Результат уже является списком сделок
                    deal_list = results[ident_id] if isinstance(results[ident_id], list) else []
                    deals[ident_id] = deal_list[0] if deal_list else None
                else:
                    deals[ident_id] = None

        logger.info(f"Batch поиск сделок: запрошено {len(ident_ids)}, найдено {sum(1 for d in deals.values() if d)}")

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
        BATCH ОПТИМИЗАЦИЯ: Ищет несколько лидов по телефонам

        ВАЖНО: filter[PHONE] для лидов НЕ РАБОТАЕТ если телефон в контакте!
        Поэтому используем двухэтапный поиск:
        1. Находим контакты по телефонам (если не переданы)
        2. Ищем лиды по CONTACT_ID из найденных контактов

        Args:
            phones: Список телефонов
            contacts_map: Опциональный словарь уже найденных контактов {phone: contact_data}

        Returns:
            Словарь {phone: lead_data или None}
        """
        if not phones:
            return {}

        # Если контакты не переданы - находим сами
        if contacts_map is None:
            logger.debug("Контакты не переданы, ищем самостоятельно")
            contacts_map = self.batch_find_contacts_by_phones(phones)

        # Собираем CONTACT_ID из найденных контактов
        phone_to_contact_id = {}
        contact_ids = []

        for phone in phones:
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

        logger.debug(f"Из {len(phones)} телефонов найдено {len(contact_ids)} контактов с ID")

        # Ищем лиды по CONTACT_ID
        if contact_ids:
            leads_by_contact_id = self.batch_find_leads_by_contact_ids(contact_ids)
        else:
            leads_by_contact_id = {}

        # Формируем результат: {phone: lead_data}
        leads = {}
        for phone in phones:
            contact_id = phone_to_contact_id.get(phone)
            if contact_id:
                leads[phone] = leads_by_contact_id.get(contact_id)
            else:
                leads[phone] = None

        logger.info(f"Batch поиск лидов: запрошено {len(phones)}, найдено {sum(1 for l in leads.values() if l)}")

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
