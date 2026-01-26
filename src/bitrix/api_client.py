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


class Bitrix24NotFoundError(Bitrix24Error):
    """Сущность не найдена"""
    pass


def retry_on_api_error(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Декоратор для retry при ошибках API

    Args:
        max_attempts: Максимальное количество попыток
        delay: Начальная задержка в секундах
        backoff: Множитель для экспоненциальной задержки
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay

            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)

                except (requests.RequestException, Bitrix24RateLimitError) as e:
                    attempt += 1

                    if attempt >= max_attempts:
                        logger.error(f"API ошибка после {attempt} попыток: {e}")
                        raise

                    logger.warning(
                        f"API ошибка, попытка {attempt}/{max_attempts} "
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

    Лимиты:
    - 2 запроса в секунду
    - 120 запросов в минуту
    """

    def __init__(self, requests_per_second: float = 2.0, requests_per_minute: int = 120):
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
        max_retries: int = 3,
        enable_rate_limiting: bool = True
    ):
        """
        Инициализация клиента

        Args:
            webhook_url: URL входящего webhook
            request_timeout: Таймаут запроса в секундах
            max_retries: Максимальное количество повторных попыток
            enable_rate_limiting: Включить ли rate limiting
        """
        if not webhook_url or not webhook_url.startswith(('http://', 'https://')):
            raise ValueError(f"Невалидный webhook_url: {webhook_url}")

        self.webhook_url = webhook_url.rstrip('/')
        self.request_timeout = request_timeout
        self.max_retries = max_retries

        # Rate limiter
        self.rate_limiter = RateLimiter() if enable_rate_limiting else None

        logger.info(f"Bitrix24Client инициализирован: {self.webhook_url[:50]}...")

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
            response = requests.post(
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
                raise Bitrix24Error(f"Ошибка сервера Битрикс24: {response.status_code}")

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

        except requests.Timeout:
            raise Bitrix24Error(f"Таймаут запроса к API (>{self.request_timeout}с)")

        except requests.ConnectionError as e:
            raise Bitrix24Error(f"Ошибка соединения с Битрикс24: {e}")

        except requests.RequestException as e:
            raise Bitrix24Error(f"Ошибка HTTP запроса: {e}")

    @retry_on_api_error(max_attempts=3)
    def find_contact_by_phone_and_name(
        self,
        phone: str,
        name: str,
        last_name: str,
        second_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Ищет контакт по телефону И ФИО

        Это необходимо, т.к. по одному телефону может быть несколько людей
        (например, члены семьи используют один номер)

        Args:
            phone: Номер телефона (нормализованный)
            name: Имя
            last_name: Фамилия
            second_name: Отчество (опционально)

        Returns:
            Данные контакта или None если не найден
        """
        try:
            result = self._make_request(
                'crm.contact.list',
                {
                    'filter': {'PHONE': phone},
                    'select': ['ID', 'NAME', 'LAST_NAME', 'SECOND_NAME', 'PHONE']
                }
            )

            contacts = result.get('result', [])

            if not contacts:
                logger.info(f"Контакты не найдены для телефона {phone}")
                return None

            # Ищем контакт с совпадающим ФИО
            for contact in contacts:
                contact_name = (contact.get('NAME') or '').strip().lower()
                contact_last_name = (contact.get('LAST_NAME') or '').strip().lower()
                contact_second_name = (contact.get('SECOND_NAME') or '').strip().lower()

                # Нормализуем входные данные
                search_name = name.strip().lower() if name else ''
                search_last_name = last_name.strip().lower() if last_name else ''
                search_second_name = second_name.strip().lower() if second_name else ''

                # Пропускаем пустые имена/фамилии
                if not search_name or not search_last_name:
                    continue

                # Сравниваем ФИО (без учета регистра)
                name_match = contact_name == search_name
                last_name_match = contact_last_name == search_last_name

                # ✅ УЛУЧШЕННАЯ ЛОГИКА: Отчество должно совпадать точно
                # Если у одного есть отчество, а у другого нет - это РАЗНЫЕ люди
                second_name_match = contact_second_name == search_second_name

                if name_match and last_name_match and second_name_match:
                    # Маскируем телефон для логов
                    masked_phone = f"+7XXX***{phone[-4:]}" if len(phone) > 4 else "***"
                    logger.info(
                        f"Найден контакт ID={contact['ID']} для {search_last_name} {search_name} "
                        f"{search_second_name or ''} (тел: {masked_phone})"
                    )
                    return contact

            logger.info(
                f"Контакт с ФИО '{last_name} {name} {second_name or ''}' "
                f"не найден для телефона {phone} (найдено контактов: {len(contacts)})"
            )
            return None

        except Bitrix24Error as e:
            logger.error(f"Ошибка поиска контакта: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def find_contact_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет контакт по телефону

        Args:
            phone: Номер телефона (нормализованный)

        Returns:
            Данные контакта или None если не найден
        """
        try:
            result = self._make_request(
                'crm.contact.list',
                {
                    'filter': {'PHONE': phone},
                    'select': ['ID', 'NAME', 'LAST_NAME', 'PHONE']
                }
            )

            contacts = result.get('result', [])

            if contacts:
                contact = contacts[0]  # Берем первый найденный
                logger.info(f"Найден контакт ID={contact['ID']} для телефона {phone}")
                return contact

            logger.info(f"Контакт не найден для телефона {phone}")
            return None

        except Bitrix24Error as e:
            logger.error(f"Ошибка поиска контакта: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def find_lead_by_phone_and_name(
        self,
        phone: str,
        name: str,
        last_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Ищет лид по телефону И ФИО

        Args:
            phone: Номер телефона
            name: Имя
            last_name: Фамилия

        Returns:
            Данные лида или None
        """
        try:
            result = self._make_request(
                'crm.lead.list',
                {
                    'filter': {'PHONE': phone},
                    'select': ['ID', 'NAME', 'LAST_NAME', 'STATUS_ID']
                }
            )

            leads = result.get('result', [])

            if not leads:
                return None

            # Ищем лид с совпадающим ФИО
            for lead in leads:
                lead_name = (lead.get('NAME') or '').strip().lower()
                lead_last_name = (lead.get('LAST_NAME') or '').strip().lower()

                # Сравниваем ФИО (без учета регистра)
                name_match = lead_name == name.strip().lower()
                last_name_match = lead_last_name == last_name.strip().lower()

                if name_match and last_name_match:
                    logger.info(
                        f"Найден лид ID={lead['ID']} для {last_name} {name} "
                        f"(тел: {phone[:10]}...)"
                    )
                    return lead

            logger.info(
                f"Лид с ФИО '{last_name} {name}' не найден для телефона {phone}"
            )
            return None

        except Bitrix24Error as e:
            logger.error(f"Ошибка поиска лида: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def find_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет лид по телефону

        Args:
            phone: Номер телефона

        Returns:
            Данные лида или None
        """
        try:
            result = self._make_request(
                'crm.lead.list',
                {
                    'filter': {'PHONE': phone},
                    'select': ['ID', 'NAME', 'LAST_NAME', 'STATUS_ID']
                }
            )

            leads = result.get('result', [])

            if leads:
                lead = leads[0]
                logger.info(f"Найден лид ID={lead['ID']} для телефона {phone}")
                return lead

            return None

        except Bitrix24Error as e:
            logger.error(f"Ошибка поиска лида: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def update_lead_status(self, lead_id: int, status_id: str = 'CONVERTED') -> bool:
        """
        Обновляет статус лида (например, переводит в успешную стадию)

        Args:
            lead_id: ID лида
            status_id: Новый статус (по умолчанию CONVERTED - успешно обработан)

        Returns:
            True если обновлено успешно
        """
        try:
            result = self._make_request(
                'crm.lead.update',
                {
                    'id': lead_id,
                    'fields': {'STATUS_ID': status_id}
                }
            )

            logger.info(f"Обновлен статус лида ID={lead_id} на {status_id}")
            return True

        except Bitrix24Error as e:
            logger.error(f"Ошибка обновления статуса лида {lead_id}: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def create_contact(self, contact_data: Dict[str, Any]) -> int:
        """
        Создает новый контакт

        Args:
            contact_data: Данные контакта

        Returns:
            ID созданного контакта
        """
        try:
            # Формируем поля для API
            fields = {
                'NAME': contact_data.get('name', ''),
                'LAST_NAME': contact_data.get('last_name', ''),
                'SECOND_NAME': contact_data.get('second_name', ''),
                'TYPE_ID': contact_data.get('type_id', 'CLIENT'),
                'PHONE': [{'VALUE': contact_data['phone'], 'VALUE_TYPE': 'MOBILE'}],
                'UF_CRM_1769083788971': contact_data.get('UF_CRM_1769083788971', ''),  # Номер карты пациента
                'UF_CRM_1769087537061': contact_data.get('UF_CRM_1769087537061', '')   # Родитель/Опекун
            }

            result = self._make_request('crm.contact.add', {'fields': fields})

            contact_id = result.get('result')
            logger.info(
                f"Создан контакт ID={contact_id}: "
                f"{fields['LAST_NAME']} {fields['NAME']}, тел. {contact_data['phone']}"
            )

            return int(contact_id)

        except Bitrix24Error as e:
            logger.error(f"Ошибка создания контакта: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def find_deal_by_ident_id(self, ident_id: str) -> Optional[Dict[str, Any]]:
        """
        Ищет сделку по ID из Ident

        Args:
            ident_id: Уникальный идентификатор (F1_12345)

        Returns:
            Данные сделки или None
        """
        try:
            result = self._make_request(
                'crm.deal.list',
                {
                    'filter': {'UF_CRM_1769072841035': ident_id},  # ID из Ident
                    'select': ['ID', 'STAGE_ID', 'OPPORTUNITY', 'UF_CRM_1769072841035']
                }
            )

            deals = result.get('result', [])

            if deals:
                deal = deals[0]
                logger.info(f"Найдена сделка ID={deal['ID']} для {ident_id}")
                return deal

            return None

        except Bitrix24Error as e:
            logger.error(f"Ошибка поиска сделки: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def get_deal_treatment_plan_data(self, deal_id: int) -> Optional[Dict[str, str]]:
        """
        Получает данные плана лечения из сделки

        Args:
            deal_id: ID сделки

        Returns:
            Словарь с plan (JSON) и hash или None
        """
        try:
            result = self._make_request(
                'crm.deal.get',
                {
                    'id': deal_id
                }
            )

            deal = result.get('result', {})

            plan_json = deal.get('UF_CRM_1769167266723', '')
            plan_hash = deal.get('UF_CRM_1769167398642', '')

            if plan_json or plan_hash:
                return {
                    'plan': plan_json,
                    'hash': plan_hash
                }

            return None

        except Bitrix24Error as e:
            logger.warning(f"Ошибка получения данных плана для сделки {deal_id}: {e}")
            return None

    @retry_on_api_error(max_attempts=3)
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
            fields = {
                'TITLE': deal_data.get('title', 'Сделка'),
                'STAGE_ID': deal_data.get('stage_id', 'NEW'),
                'CONTACT_ID': contact_id,
                'OPPORTUNITY': deal_data.get('opportunity', 0),
                'CURRENCY_ID': deal_data.get('currency_id', 'RUB'),

                # Кастомные поля (актуальные ID из Bitrix24)
                'UF_CRM_1769008900': deal_data.get('UF_CRM_1769008900'),  # Дата начало приема
                'UF_CRM_1769008947': deal_data.get('UF_CRM_1769008947'),  # Дата окончания приема
                'UF_CRM_1769008996': deal_data.get('UF_CRM_1769008996'),  # Врач
                'UF_CRM_1769009098': deal_data.get('UF_CRM_1769009098'),  # Услуги
                'UF_CRM_1769009157': deal_data.get('UF_CRM_1769009157'),  # Статус записи
                'UF_CRM_1769083581481': deal_data.get('UF_CRM_1769083581481'),  # Номер карты пациента
                'UF_CRM_1769087458477': deal_data.get('UF_CRM_1769087458477'),  # Родитель/Опекун

                # Дополнительные поля (для внутреннего использования)
                'UF_CRM_1769072841035': deal_data.get('uf_crm_ident_id'),  # ID из Ident
                'UF_CRM_FILIAL': deal_data.get('uf_crm_filial'),
                'UF_CRM_ARMCHAIR': deal_data.get('uf_crm_armchair'),
                'UF_CRM_STATUS': deal_data.get('uf_crm_status'),
                'UF_CRM_CARD_NUMBER': deal_data.get('uf_crm_card_number'),
                'UF_CRM_ORDER_DATE': deal_data.get('uf_crm_order_date'),
                'UF_CRM_DOCTOR_SPECIALITY': deal_data.get('uf_crm_doctor_speciality'),

                # План лечения
                'UF_CRM_1769167266723': deal_data.get('uf_crm_treatment_plan'),  # JSON плана лечения
                'UF_CRM_1769167398642': deal_data.get('uf_crm_treatment_plan_hash'),  # MD5 хеш
            }

            # Удаляем None значения
            fields = {k: v for k, v in fields.items() if v is not None}

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

    @retry_on_api_error(max_attempts=3)
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
            fields = {
                'TITLE': deal_data.get('title'),
                'STAGE_ID': deal_data.get('stage_id'),
                'OPPORTUNITY': deal_data.get('opportunity'),

                # Кастомные поля (актуальные ID из Bitrix24)
                'UF_CRM_1769008900': deal_data.get('UF_CRM_1769008900'),  # Дата начало приема
                'UF_CRM_1769008947': deal_data.get('UF_CRM_1769008947'),  # Дата окончания приема
                'UF_CRM_1769008996': deal_data.get('UF_CRM_1769008996'),  # Врач
                'UF_CRM_1769009098': deal_data.get('UF_CRM_1769009098'),  # Услуги
                'UF_CRM_1769009157': deal_data.get('UF_CRM_1769009157'),  # Статус записи
                'UF_CRM_1769083581481': deal_data.get('UF_CRM_1769083581481'),  # Номер карты пациента
                'UF_CRM_1769087458477': deal_data.get('UF_CRM_1769087458477'),  # Родитель/Опекун
                'UF_CRM_STATUS': deal_data.get('uf_crm_status'),

                # План лечения
                'UF_CRM_1769167266723': deal_data.get('uf_crm_treatment_plan'),  # JSON плана лечения
                'UF_CRM_1769167398642': deal_data.get('uf_crm_treatment_plan_hash'),  # MD5 хеш
            }

            # Удаляем None значения
            fields = {k: v for k, v in fields.items() if v is not None}

            result = self._make_request(
                'crm.deal.update',
                {'id': deal_id, 'fields': fields}
            )

            logger.info(f"Обновлена сделка ID={deal_id}")
            return True

        except Bitrix24Error as e:
            logger.error(f"Ошибка обновления сделки {deal_id}: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
    def add_comment_to_deal(self, deal_id: int, comment_text: str) -> bool:
        """
        Добавляет комментарий к сделке через Timeline API

        Args:
            deal_id: ID сделки
            comment_text: Текст комментария

        Returns:
            True если комментарий добавлен успешно
        """
        try:
            if not comment_text:
                return True

            # Используем crm.timeline.comment.add с правильной структурой
            result = self._make_request(
                'crm.timeline.comment.add',
                {
                    'fields': {
                        'ENTITY_ID': deal_id,
                        'ENTITY_TYPE': 'deal',
                        'COMMENT': comment_text,
                        'AUTHOR_ID': 1  # ID пользователя (1 - администратор)
                    }
                }
            )

            comment_id = result.get('result')
            logger.info(f"Добавлен комментарий ID={comment_id} к сделке ID={deal_id}")
            return True

        except Bitrix24Error as e:
            logger.warning(f"Ошибка Timeline API для сделки {deal_id}: {e}")
            # Пробуем альтернативный метод через активность
            try:
                result = self._make_request(
                    'crm.activity.add',
                    {
                        'fields': {
                            'OWNER_TYPE_ID': 2,  # 2 = Deal (сделка)
                            'OWNER_ID': deal_id,
                            'PROVIDER_ID': 'CRM_TIMELINE',
                            'PROVIDER_TYPE_ID': 'COMMENT',
                            'SUBJECT': 'Комментарий из IDENT',
                            'DESCRIPTION': comment_text,
                            'DESCRIPTION_TYPE': 1,  # 1 = Plain text
                            'COMPLETED': 'Y',
                            'PRIORITY': 2,  # 2 = Medium
                            'RESPONSIBLE_ID': 1
                        }
                    }
                )

                activity_id = result.get('result')
                logger.info(f"Добавлена активность ID={activity_id} к сделке ID={deal_id}")
                return True

            except Bitrix24Error as e2:
                logger.error(f"Ошибка добавления комментария к сделке {deal_id}: Timeline={e}, Activity={e2}")
                # Не падаем, если комментарий не добавился
                return False

    def test_connection(self) -> bool:
        """
        Тестирует подключение к API

        Returns:
            True если подключение успешно
        """
        try:
            result = self._make_request('crm.contact.list', {'filter': {}, 'select': ['ID']})
            logger.info("✅ Подключение к Битрикс24 успешно")
            return True

        except Bitrix24AuthError as e:
            logger.error(f"❌ Ошибка аутентификации: {e}")
            raise

        except Bitrix24Error as e:
            logger.error(f"❌ Ошибка подключения: {e}")
            raise


if __name__ == "__main__":
    """Тестирование клиента"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python api_client.py <webhook_url>")
        sys.exit(1)

    webhook_url = sys.argv[1]

    print("🧪 Тестирование Bitrix24Client...")

    try:
        client = Bitrix24Client(webhook_url)

        # Тест 1: Подключение
        print("\n1️⃣ Тест подключения:")
        if client.test_connection():
            print("✅ Подключение успешно")

        # Тест 2: Поиск контакта
        print("\n2️⃣ Тест поиска контакта:")
        test_phone = "+79991234567"
        contact = client.find_contact_by_phone(test_phone)
        if contact:
            print(f"✅ Найден контакт: {contact}")
        else:
            print(f"ℹ️ Контакт не найден для {test_phone}")

        # Тест 3: Создание контакта
        print("\n3️⃣ Тест создания контакта:")
        test_contact = {
            'name': 'Тестовый',
            'last_name': 'Контакт',
            'second_name': 'Иванович',
            'phone': test_phone,
            'type_id': 'CLIENT'
        }

        # Раскомментируйте для реального создания
        # contact_id = client.create_contact(test_contact)
        # print(f"✅ Создан контакт ID={contact_id}")
        print("⏭️  Пропущено (раскомментируйте для реального теста)")

        print("\n✅ Все тесты пройдены!")

    except Bitrix24AuthError as e:
        print(f"\n❌ Ошибка аутентификации: {e}")
        sys.exit(1)

    except Bitrix24Error as e:
        print(f"\n❌ Ошибка API: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"\n❌ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
