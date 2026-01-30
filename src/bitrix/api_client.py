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

        self.webhook_url = webhook_url.rstrip('/')
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.default_assigned_by_id = default_assigned_by_id

        # Rate limiter
        self.rate_limiter = RateLimiter() if enable_rate_limiting else None

        if default_assigned_by_id:
            logger.info(f"Bitrix24Client инициализирован: {self.webhook_url[:50]}... (ответственный: {default_assigned_by_id})")
        else:
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
    def find_contact_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Ищет первый контакт по телефону"""
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

    @retry_on_api_error(max_attempts=3)
    def find_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        Ищет первый лид по телефону

        ВАЖНО: filter[PHONE] для лидов НЕ РАБОТАЕТ если телефон в контакте!
        Поэтому ищем через контакт: phone → CONTACT_ID → lead
        """
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
        return leads[0] if leads else None

    @retry_on_api_error(max_attempts=3)
    def convert_lead(self, lead_id: int, contact_id: Optional[int] = None) -> Optional[int]:
        """Конвертирует лид в сделку"""
        params = {
            'LEAD_ID': lead_id,
            'CREATE_CONTACT': 'N' if contact_id else 'Y',
            'CREATE_COMPANY': 'N',
            'CREATE_DEAL': 'Y'
        }

        if contact_id:
            params['CONTACT_ID'] = contact_id

        result = self._make_request('crm.lead.convert', params)
        deal_id = result.get('result', {}).get('DEAL_ID')

        if deal_id:
            logger.info(f"Лид {lead_id} конвертирован в сделку {deal_id}")
            return int(deal_id)

        logger.warning(f"Конвертация лида {lead_id} не вернула ID сделки")
        return None

    @retry_on_api_error(max_attempts=3)
    def get_deal(self, deal_id: int) -> Optional[Dict[str, Any]]:
        """Получает информацию о сделке"""
        result = self._make_request('crm.deal.get', {'id': deal_id})
        return result.get('result')

    @retry_on_api_error(max_attempts=3)
    def create_contact(self, contact_data: Dict[str, Any]) -> int:
        """Создает новый контакт"""
        fields = {
            'NAME': contact_data.get('name', ''),
            'LAST_NAME': contact_data.get('last_name', ''),
            'SECOND_NAME': contact_data.get('second_name', ''),
            'TYPE_ID': contact_data.get('type_id', 'CLIENT'),
            'PHONE': [{'VALUE': contact_data['phone'], 'VALUE_TYPE': 'MOBILE'}],
            'UF_CRM_1769083788971': contact_data.get('UF_CRM_1769083788971', ''),
            'UF_CRM_1769087537061': contact_data.get('UF_CRM_1769087537061', '')
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

    @retry_on_api_error(max_attempts=3)
    def find_deal_by_ident_id(self, ident_id: str) -> Optional[Dict[str, Any]]:
        """Ищет сделку по IDENT ID"""
        result = self._make_request(
            'crm.deal.list',
            {
                'filter': {'UF_CRM_1769072841035': ident_id},
                'select': ['ID', 'STAGE_ID', 'CONTACT_ID', 'UF_CRM_1769072841035']
            }
        )

        deals = result.get('result', [])
        return deals[0] if deals else None

    @retry_on_api_error(max_attempts=3)
    def find_deals_by_contact_without_ident_id(
        self,
        contact_id: int,
        exclude_final: bool = True
    ) -> List[Dict[str, Any]]:
        """Ищет сделки контакта без IDENT ID"""
        from src.transformer.data_transformer import StageMapper

        result = self._make_request(
            'crm.deal.list',
            {
                'filter': {
                    'CONTACT_ID': contact_id,
                    '=UF_CRM_1769072841035': False
                },
                'select': ['ID', 'STAGE_ID', 'DATE_CREATE', 'UF_CRM_1769072841035'],
                'order': {'DATE_CREATE': 'DESC'}
            }
        )

        deals = result.get('result', [])

        if exclude_final:
            deals = [d for d in deals if not StageMapper.is_stage_final(d.get('STAGE_ID'))]

        return deals

    @retry_on_api_error(max_attempts=3)
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
                'UF_CRM_1769494714842': deal_data.get('UF_CRM_1769494714842'),  #  Комментарий из IDENT

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

            # Устанавливаем ответственного если указан в конфиге
            if self.default_assigned_by_id:
                fields['ASSIGNED_BY_ID'] = self.default_assigned_by_id

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
                'UF_CRM_1769494714842': deal_data.get('UF_CRM_1769494714842'),  #  Комментарий из IDENT

                # Дополнительные поля (для внутреннего использования)
                'UF_CRM_1769072841035': deal_data.get('uf_crm_ident_id'),  # ID из Ident
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
    @retry_on_api_error(max_attempts=3)
    def batch_execute(self, commands: Dict[str, str], halt_on_error: bool = False) -> Dict[str, Any]:
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
                "find_deal": "crm.deal.list?filter[UF_CRM_1769072841035]=F1_12345"
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

            # Логируем ошибки если есть
            if 'result_error' in batch_result:
                result_error = batch_result['result_error']
                # result_error может быть словарём или списком в зависимости от версии API
                if isinstance(result_error, dict):
                    for cmd_name, error in result_error.items():
                        logger.warning(f"Batch команда '{cmd_name}' завершилась с ошибкой: {error}")
                elif isinstance(result_error, list):
                    for error in result_error:
                        logger.warning(f"Batch ошибка: {error}")
                else:
                    logger.warning(f"Batch содержит ошибки: {result_error}")

            logger.debug(f"Batch выполнен: {len(commands)} команд, успешно: {len(result_data)}")

            return result_data

        except Bitrix24Error as e:
            logger.error(f"Ошибка batch запроса: {e}")
            raise

    @retry_on_api_error(max_attempts=3)
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
                safe_phone = phone.replace('+', '%2B')
                commands[phone] = f"crm.contact.list?filter[PHONE]={safe_phone}&select[]=ID&select[]=NAME&select[]=LAST_NAME&select[]=SECOND_NAME&select[]=PHONE"

            results = self.batch_execute(commands)

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

    @retry_on_api_error(max_attempts=3)
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

        # Обрабатываем по 50 элементов за раз (лимит Битрикс24)
        for i in range(0, len(ident_ids), 50):
            chunk = ident_ids[i:i + 50]

            # Формируем batch команды для текущего чанка
            commands = {}
            for ident_id in chunk:
                commands[ident_id] = f"crm.deal.list?filter[UF_CRM_1769072841035]={ident_id}&select[]=ID&select[]=STAGE_ID&select[]=OPPORTUNITY&select[]=UF_CRM_1769072841035"

            results = self.batch_execute(commands)

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

    @retry_on_api_error(max_attempts=3)
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

            results = self.batch_execute(commands)

            # Парсим результаты для текущего чанка
            for contact_id in chunk:
                key = str(contact_id)
                if key in results:
                    lead_list = results[key] if isinstance(results[key], list) else []
                    found_lead = lead_list[0] if lead_list else None
                    leads[contact_id] = found_lead

                    if found_lead:
                        logger.debug(f"Найден лид {found_lead.get('ID')} для контакта {contact_id}")
                else:
                    leads[contact_id] = None

        logger.info(f"Batch поиск лидов по CONTACT_ID: запрошено {len(contact_ids)}, найдено {sum(1 for l in leads.values() if l)}")

        return leads

    @retry_on_api_error(max_attempts=3)
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
