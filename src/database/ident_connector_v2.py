"""
Модуль подключения к БД Ident и извлечения данных (Версия 2.0 - Оптимизированная)

ИСПРАВЛЕНИЯ:
- ✅ Автоопределение ODBC Driver
- ✅ Connection Pooling (переиспользование соединений)
- ✅ Retry логика при временных ошибках
- ✅ Оптимизация SQL (убран N+1 problem через OUTER APPLY)
- ✅ Валидация входных параметров
- ✅ Правильная обработка ошибок с сохранением stack trace
- ✅ Query timeout в connection string
- ✅ fetchmany вместо fetchall для больших объемов
"""

import pyodbc
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Generator
from contextlib import contextmanager
from functools import wraps
from queue import Queue, Empty
import threading
import logging

# Используем настроенный logger из custom_logger_v2
from src.logger.custom_logger_v2 import get_logger
logger = get_logger('ident_integration')


class ConnectionPool:
    """Thread-safe пул соединений для переиспользования"""

    def __init__(self, connection_string: str, pool_size: int = 3, max_lifetime: int = 3600):
        self.connection_string = connection_string
        self.pool_size = pool_size
        self.max_lifetime = max_lifetime  # Максимальное время жизни соединения (сек)
        self.pool = Queue(maxsize=pool_size)
        self.connection_times = {}  # Время создания каждого соединения
        self.lock = threading.Lock()

        # Предварительно создаем соединения
        for _ in range(pool_size):
            conn = self._create_connection()
            self.pool.put(conn)

    def _create_connection(self) -> pyodbc.Connection:
        """Создает новое соединение"""
        conn = pyodbc.connect(self.connection_string)
        conn_id = id(conn)
        self.connection_times[conn_id] = time.time()
        return conn

    def _is_connection_alive(self, conn: pyodbc.Connection) -> bool:
        """Проверяет живо ли соединение"""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except Exception:
            return False

    def _is_connection_expired(self, conn: pyodbc.Connection) -> bool:
        """Проверяет не истек ли срок жизни соединения"""
        conn_id = id(conn)
        if conn_id not in self.connection_times:
            return True

        age = time.time() - self.connection_times[conn_id]
        return age > self.max_lifetime

    @contextmanager
    def get_connection(self):
        """Получает соединение из пула"""
        conn = None
        try:
            # Получаем соединение из пула
            try:
                conn = self.pool.get(timeout=10)
            except Empty:
                raise RuntimeError("Connection pool exhausted. Не удалось получить соединение за 10 секунд.")

            # Проверяем соединение
            if not self._is_connection_alive(conn) or self._is_connection_expired(conn):
                logger.info("Соединение умерло или истекло, создаем новое")
                try:
                    conn.close()
                except Exception:
                    pass

                # Удаляем из словаря времен
                conn_id = id(conn)
                if conn_id in self.connection_times:
                    del self.connection_times[conn_id]

                # Создаем новое
                conn = self._create_connection()

            yield conn

        except Exception as e:
            # Если ошибка при работе с соединением - помечаем его как мертвое
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

                # Создаем новое взамен
                conn = self._create_connection()

            raise

        finally:
            # Возвращаем соединение в пул
            if conn:
                self.pool.put(conn)

    def close_all(self):
        """Закрывает все соединения в пуле"""
        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                conn.close()
            except Exception:
                pass


def retry_on_db_error(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Декоратор для retry при временных ошибках БД

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

                except pyodbc.Error as e:
                    # Коды временных ошибок SQL Server
                    retryable_codes = [
                        '08S01',  # Communication link failure
                        '40001',  # Deadlock
                        'HYT00',  # Timeout
                        '08001',  # Unable to connect
                        '40197',  # Service temporarily unavailable
                        '40501',  # Service busy
                        '40613',  # Database unavailable
                    ]

                    error_code = e.args[0] if e.args else None
                    is_retryable = any(code in str(e) for code in retryable_codes)

                    if is_retryable and attempt < max_attempts - 1:
                        attempt += 1
                        logger.warning(
                            f"БД ошибка (код: {error_code}), попытка {attempt}/{max_attempts} "
                            f"через {current_delay:.1f}с: {e}"
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        # Не временная ошибка или исчерпаны попытки
                        logger.error(f"БД ошибка после {attempt + 1} попыток: {e}", exc_info=True)
                        raise

                except Exception as e:
                    # Неожиданная ошибка - не ретраим
                    logger.error(f"Неожиданная ошибка: {e}", exc_info=True)
                    raise

        return wrapper
    return decorator


class IdentConnector:
    """Коннектор к БД Ident с автоопределением драйвера и connection pooling"""

    def __init__(
        self,
        server: str,
        database: str,
        username: str,
        password: str,
        port: int = 1433,
        connection_timeout: int = 10,
        query_timeout: int = 30,
        pool_size: int = 3
    ):
        self.server = server
        self.database = database
        self.username = username
        self.password = password
        self.port = port
        self.connection_timeout = connection_timeout
        self.query_timeout = query_timeout
        self.pool_size = pool_size

        # Автоопределение ODBC Driver
        self.driver = self._detect_available_driver()
        logger.info(f"Используется ODBC Driver: {self.driver}")

        # Формируем connection string
        # Для именованных экземпляров (с \) не указываем порт - используется Named Pipes
        if '\\' in self.server:
            # Именованный экземпляр - Named Pipes (без порта)
            server_string = self.server
            logger.info(f"Именованный экземпляр обнаружен, используется Named Pipes: {server_string}")
        else:
            # Стандартный экземпляр или IP - TCP/IP с портом
            server_string = f"{self.server},{self.port}"
            logger.info(f"Стандартный экземпляр, используется TCP/IP: {server_string}")

        self.connection_string = (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={server_string};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            f"Connection Timeout={self.connection_timeout};"
            f"Query Timeout={self.query_timeout};"
        )

        # Создаем connection pool
        self.pool = ConnectionPool(
            connection_string=self.connection_string,
            pool_size=pool_size,
            max_lifetime=3600  # 1 час
        )

    def _detect_available_driver(self) -> str:
        """
        Автоматически определяет доступный ODBC драйвер для SQL Server

        Returns:
            Название доступного драйвера

        Raises:
            RuntimeError: Если не найден ни один подходящий драйвер
        """
        # Список драйверов в порядке предпочтения
        preferred_drivers = [
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "ODBC Driver 11 for SQL Server",
            "SQL Server Native Client 11.0",
            "SQL Server Native Client 10.0",
            "SQL Server"
        ]

        # Получаем список установленных драйверов
        available_drivers = pyodbc.drivers()
        logger.debug(f"Установленные ODBC драйверы: {available_drivers}")

        # Ищем первый подходящий
        for preferred in preferred_drivers:
            if preferred in available_drivers:
                return preferred

        # Если не нашли из preferred, ищем любой SQL Server драйвер
        for driver in available_drivers:
            if 'SQL Server' in driver:
                logger.warning(
                    f"Используется неожиданный драйвер: {driver}. "
                    f"Рекомендуется установить ODBC Driver 17 for SQL Server."
                )
                return driver

        # Совсем ничего не нашли
        raise RuntimeError(
            f"Не найден ODBC драйвер для SQL Server!\n"
            f"Установленные драйверы: {', '.join(available_drivers)}\n"
            f"Установите драйвер: https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
        )

    @contextmanager
    def get_connection(self):
        """Получает соединение из пула"""
        with self.pool.get_connection() as conn:
            yield conn

    def test_connection(self) -> bool:
        """
        Тестирует подключение к БД

        Returns:
            True если подключение успешно

        Raises:
            ConnectionError: Если не удалось подключиться
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
            return True
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к БД Ident: {e}") from e

    def _validate_batch_size(self, batch_size: int):
        """Валидирует размер пакета"""
        if batch_size <= 0:
            raise ValueError(f"batch_size должен быть > 0, получено: {batch_size}")
        if batch_size > 1000:
            raise ValueError(f"batch_size не должен превышать 1000, получено: {batch_size}")

    def _validate_initial_days(self, initial_days: int):
        """Валидирует глубину начальной синхронизации"""
        if initial_days <= 0:
            raise ValueError(f"initial_days должен быть > 0, получено: {initial_days}")
        if initial_days > 365:
            raise ValueError(f"initial_days не должен превышать 365, получено: {initial_days}")

    def _validate_last_sync_time(self, last_sync_time: Optional[datetime]):
        """Валидирует время последней синхронизации"""
        if last_sync_time and last_sync_time > datetime.now():
            raise ValueError(f"last_sync_time не может быть в будущем: {last_sync_time}")

    @retry_on_db_error(max_attempts=3, delay=1.0, backoff=2.0)
    def get_receptions(
        self,
        last_sync_time: Optional[datetime] = None,
        batch_size: int = 50,
        initial_days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Извлекает записи из БД Ident (ОПТИМИЗИРОВАННАЯ ВЕРСИЯ)

        Args:
            last_sync_time: Время последней синхронизации
            batch_size: Размер пакета (1-1000)
            initial_days: Глубина начальной синхронизации в днях (1-365)

        Returns:
            Список записей

        Raises:
            ValueError: При невалидных параметрах
            RuntimeError: При ошибке БД
        """
        # Валидация входных параметров
        self._validate_batch_size(batch_size)
        self._validate_initial_days(initial_days)
        self._validate_last_sync_time(last_sync_time)

        # Если это первая синхронизация
        if last_sync_time is None:
            last_sync_time = datetime.now() - timedelta(days=initial_days)
            logger.info(f"Первая синхронизация: загружаем данные за последние {initial_days} дней")

        # ОПТИМИЗИРОВАННЫЙ ЗАПРОС: убран N+1 problem через OUTER APPLY
        query = """
        SELECT TOP (?)
            -- Данные записи
            r.ID AS ReceptionID,
            r.PlanStart AS StartTime,
            r.PlanEnd AS EndTime,

            -- Пациент
            p.Surname + ' ' + p.Name + ISNULL(' ' + p.Patronymic, '') AS PatientFullName,
            p.Surname AS PatientSurname,
            p.Name AS PatientName,
            p.Patronymic AS PatientPatronymic,
            pat.CardNumber AS CardNumber,
            p.MobilePhone AS PatientPhone,
            pat.ParentSNP AS ParentFullName,  -- Родитель/Опекун (ФИО)

            -- Врач
            ps.Surname + ' ' + ps.Name + ISNULL(' ' + ps.Patronymic, '') AS DoctorFullName,
            ps.Surname AS DoctorSurname,
            ps.Name AS DoctorName,
            ps.Patronymic AS DoctorPatronymic,
            pn.NameProfession AS Speciality,

            -- Филиал и кабинет
            COALESCE(oc_order.Name, oc_armchair.Name, 'Не указан') AS Filial,
            a.NameArmchair AS Armchair,

            -- ✅ ОПТИМИЗАЦИЯ: Агрегированные услуги через OUTER APPLY
            services_agg.ServicesText AS Services,
            services_agg.TotalAmount AS TotalAmount,

            -- Статус
            CASE
                WHEN r.ReceptionCanceled IS NOT NULL THEN 'Отменен'
                WHEN r.CheckIssued IS NOT NULL THEN 'Завершен (счет выдан)'
                WHEN r.ReceptionEnded IS NOT NULL THEN 'Завершен'
                WHEN r.ReceptionStarted IS NOT NULL THEN 'В процессе'
                WHEN r.PatientAppeared IS NOT NULL THEN 'Пациент пришел'
                ELSE 'Запланирован'
            END AS Status,

            -- Временные метки
            r.PatientAppeared AS PatientAppeared,
            r.ReceptionStarted AS ReceptionStarted,
            r.ReceptionEnded AS ReceptionEnded,
            r.ReceptionCanceled AS ReceptionCanceled,
            r.CheckIssued AS CheckIssued,

            -- Даты заказа
            o.DateTimeOrder AS OrderDate,
            o.DateTimeBillFormed AS BillFormedDate,

            -- Комментарий
            r.Comment AS Comment,

            -- Метки времени для инкрементальной синхронизации
            r.DateTimeAdded AS CreatedAt,
            r.DateTimeChanged AS ChangedAt

        FROM Receptions r
            -- Пациент
            INNER JOIN Patients pat ON r.ID_Patients = pat.ID_Persons
            INNER JOIN Persons p ON pat.ID_Persons = p.ID

            -- Врач
            LEFT JOIN Staffs s ON r.ID_Staffs = s.ID_Persons
            LEFT JOIN Persons ps ON s.ID_Persons = ps.ID
            LEFT JOIN Items i ON s.ID_Persons = i.ID_Staffs
            LEFT JOIN ProfessionNames pn ON i.ID_ProfessionNames = pn.ID

            -- Филиал через кабинет
            LEFT JOIN Armchairs a ON r.ID_Armchairs = a.ID
            LEFT JOIN OwnCompanies oc_armchair ON a.ID_OwnCompanies = oc_armchair.ID

            -- Заказы
            LEFT JOIN Orders o ON r.ID = o.ID_Receptions

            -- Филиал через заказ
            LEFT JOIN OwnCompanies oc_order ON o.ID_OwnCompanies = oc_order.ID

            -- ✅ ОПТИМИЗАЦИЯ: OUTER APPLY вместо подзапросов
            OUTER APPLY (
                SELECT
                    -- Для SQL Server 2012-2016 (совместимость)
                    STUFF((
                        SELECT ', ' + si_inner.Name
                        FROM OrderServiceRelation osr_inner
                        INNER JOIN ServiceItemPrices sip_inner ON osr_inner.ID_ServicePrices = sip_inner.ID
                        INNER JOIN ServiceItems si_inner ON sip_inner.ID_ServiceItems = si_inner.ID
                        WHERE osr_inner.ID_Orders = o.ID
                        FOR XML PATH(''), TYPE
                    ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') AS ServicesText,
                    SUM(osr.CountService * sip.Price - ISNULL(osr.DiscountSum, 0)) AS TotalAmount
                FROM OrderServiceRelation osr
                INNER JOIN ServiceItemPrices sip ON osr.ID_ServicePrices = sip.ID
                WHERE osr.ID_Orders = o.ID
            ) services_agg

        WHERE
            -- Инкрементальная выборка
            (
                r.DateTimeAdded > ?
                OR r.DateTimeChanged > ?
                OR r.PatientAppeared > ?
                OR r.ReceptionStarted > ?
                OR r.ReceptionEnded > ?
                OR r.ReceptionCanceled > ?
            )

        ORDER BY r.PlanStart DESC, r.ID DESC
        """

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    query,
                    (
                        batch_size,
                        last_sync_time, last_sync_time, last_sync_time,
                        last_sync_time, last_sync_time, last_sync_time
                    )
                )

                columns = [column[0] for column in cursor.description]
                results = []

                # ✅ ОПТИМИЗАЦИЯ: fetchmany вместо fetchall для больших объемов
                while True:
                    rows = cursor.fetchmany(100)
                    if not rows:
                        break
                    results.extend(dict(zip(columns, row)) for row in rows)

                cursor.close()
                logger.info(f"Извлечено записей: {len(results)}")
                return results

        except pyodbc.Error as e:
            logger.error(f"Ошибка БД при извлечении записей: {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при извлечении записей из БД Ident: {e}") from e
        except Exception as e:
            logger.error(f"Неожиданная ошибка при извлечении записей: {e}", exc_info=True)
            raise

    @retry_on_db_error(max_attempts=3, delay=1.0, backoff=2.0)
    def get_treatment_plans_by_patient_name(
        self,
        patient_full_name: str
    ) -> List[Dict[str, Any]]:
        """
        Извлекает планы лечения пациента по ФИО

        Args:
            patient_full_name: Полное ФИО пациента

        Returns:
            Список планов лечения
        """
        # Валидация
        if not patient_full_name or not patient_full_name.strip():
            raise ValueError("patient_full_name не может быть пустым")

        # Разбиваем ФИО
        name_parts = patient_full_name.strip().split()
        surname = name_parts[0] if len(name_parts) > 0 else ""
        name = name_parts[1] if len(name_parts) > 1 else ""
        patronymic = name_parts[2] if len(name_parts) > 2 else ""

        query = """
        SELECT
            -- ПЛАН
            tp.ID AS PlanID,
            tp.Name AS PlanName,
            tp.DateTimeCreated AS CreatedAt,
            tp.IsActive AS IsActive,

            -- ПАЦИЕНТ
            p.Surname + ' ' + p.Name + ISNULL(' ' + p.Patronymic, '') AS PatientFullName,
            pat.CardNumber AS CardNumber,

            -- ВРАЧ
            ps_plan.Surname + ' ' + ps_plan.Name + ISNULL(' ' + ps_plan.Patronymic, '') AS DoctorFullName,
            pn_plan.NameProfession AS DoctorSpeciality,

            -- ЭТАПЫ
            tps.ID AS StageID,
            tps.Name AS StageName,
            tps.[Order] AS StageOrder,

            -- ЭЛЕМЕНТЫ
            tpe.ID AS ElementID,
            tpe.Name AS ElementName,
            tpe.[Order] AS ElementOrder,

            -- УСЛУГИ
            si.ID AS ServiceID,
            si.Name AS ServiceName,
            sc.Name AS ServiceCategory,
            sf.Name AS ServiceFolder,

            -- ЦЕНЫ
            sip.Price AS Price,
            tper.DiscountSum AS DiscountAmount,
            (sip.Price - ISNULL(tper.DiscountSum, 0)) AS TotalAmount,

            -- ЗУБЫ
            tper.TeethLong AS TeethMask,

            -- СТАТУС
            CASE
                WHEN osr.ID IS NOT NULL AND o.DateTimeBillFormed IS NOT NULL THEN 'Выполнено и оплачено'
                WHEN osr.ID IS NOT NULL THEN 'Выполнено'
                ELSE 'Не выполнено'
            END AS Status,

            -- ДЕТАЛИ ВЫПОЛНЕНИЯ
            o.ID AS OrderID,
            o.DateTimeOrder AS ExecutionDate,
            r.ID AS ReceptionID,
            r.PlanStart AS ReceptionDate

        FROM TreatmentPlans tp
            INNER JOIN Patients pat ON tp.ID_Patients = pat.ID_Persons
            INNER JOIN Persons p ON pat.ID_Persons = p.ID
            LEFT JOIN Staffs s_plan ON tp.ID_Staffs = s_plan.ID_Persons
            LEFT JOIN Persons ps_plan ON s_plan.ID_Persons = ps_plan.ID
            LEFT JOIN Items i_plan ON s_plan.ID_Persons = i_plan.ID_Staffs
            LEFT JOIN ProfessionNames pn_plan ON i_plan.ID_ProfessionNames = pn_plan.ID
            LEFT JOIN TreatmentPlanElementRelations tper ON tp.ID = tper.ID_TreatmentPlans
            LEFT JOIN TreatmentPlanStages tps ON tper.ID_TreatmentPlanStages = tps.ID
            LEFT JOIN TreatmentPlanElements tpe ON tper.ID_TreatmentPlanElements = tpe.ID
            LEFT JOIN ServiceItemPrices sip ON tper.ID_ServicePrices = sip.ID
            LEFT JOIN ServiceItems si ON sip.ID_ServiceItems = si.ID
            LEFT JOIN ServiceItemContents sic ON si.ID = sic.ID_ServiceItems AND sic.DateTimeTo IS NULL
            LEFT JOIN ServiceFolders sf ON sic.ID_ServiceFoldersParent = sf.ID
            LEFT JOIN ServiceCategories sc ON sf.ID_ServiceCategories = sc.ID
            LEFT JOIN OrderServiceRelation osr ON tper.ID = osr.ID_TreatmentPlanElementRelations
            LEFT JOIN Orders o ON osr.ID_Orders = o.ID
            LEFT JOIN Receptions r ON o.ID_Receptions = r.ID

        WHERE
            p.Surname = ?
            AND p.Name = ?
            AND (? = '' OR p.Patronymic = ?)
            AND tp.IsActive = 1

        ORDER BY
            tp.DateTimeCreated DESC,
            tps.[Order],
            tpe.[Order],
            si.Name
        """

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (surname, name, patronymic, patronymic))

                columns = [column[0] for column in cursor.description]
                results = []

                while True:
                    rows = cursor.fetchmany(100)
                    if not rows:
                        break
                    results.extend(dict(zip(columns, row)) for row in rows)

                cursor.close()
                return results

        except pyodbc.Error as e:
            logger.error(f"Ошибка БД при извлечении планов лечения: {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при извлечении планов лечения из БД Ident: {e}") from e
        except Exception as e:
            logger.error(f"Неожиданная ошибка при извлечении планов: {e}", exc_info=True)
            raise

    @retry_on_db_error(max_attempts=3, delay=1.0, backoff=2.0)
    def get_treatment_plan_by_id(self, plan_id: int) -> List[Dict[str, Any]]:
        """
        Извлекает конкретный план лечения по ID

        Args:
            plan_id: ID плана лечения

        Returns:
            Список элементов плана лечения

        Raises:
            ValueError: При невалидном plan_id
            RuntimeError: При ошибке БД
        """
        if plan_id <= 0:
            raise ValueError(f"plan_id должен быть > 0, получено: {plan_id}")

        query = """
        SELECT
            -- ПЛАН
            tp.ID AS PlanID,
            tp.Name AS PlanName,
            tp.DateTimeCreated AS CreatedAt,
            tp.IsActive AS IsActive,

            -- ПАЦИЕНТ
            p.Surname + ' ' + p.Name + ISNULL(' ' + p.Patronymic, '') AS PatientFullName,
            pat.CardNumber AS CardNumber,

            -- ВРАЧ
            ps_plan.Surname + ' ' + ps_plan.Name + ISNULL(' ' + ps_plan.Patronymic, '') AS DoctorFullName,
            pn_plan.NameProfession AS DoctorSpeciality,

            -- ЭТАПЫ
            tps.ID AS StageID,
            tps.Name AS StageName,
            tps.[Order] AS StageOrder,

            -- ЭЛЕМЕНТЫ
            tpe.ID AS ElementID,
            tpe.Name AS ElementName,
            tpe.[Order] AS ElementOrder,

            -- УСЛУГИ
            si.ID AS ServiceID,
            si.Name AS ServiceName,
            sc.Name AS ServiceCategory,
            sf.Name AS ServiceFolder,

            -- ЦЕНЫ
            sip.Price AS Price,
            tper.DiscountSum AS DiscountAmount,
            (sip.Price - ISNULL(tper.DiscountSum, 0)) AS TotalAmount,

            -- ЗУБЫ
            tper.TeethLong AS TeethMask,

            -- СТАТУС
            CASE
                WHEN osr.ID IS NOT NULL AND o.DateTimeBillFormed IS NOT NULL THEN 'Выполнено и оплачено'
                WHEN osr.ID IS NOT NULL THEN 'Выполнено'
                ELSE 'Не выполнено'
            END AS Status,

            -- ДЕТАЛИ ВЫПОЛНЕНИЯ
            o.ID AS OrderID,
            o.DateTimeOrder AS ExecutionDate,
            r.ID AS ReceptionID,
            r.PlanStart AS ReceptionDate

        FROM TreatmentPlans tp
            INNER JOIN Patients pat ON tp.ID_Patients = pat.ID_Persons
            INNER JOIN Persons p ON pat.ID_Persons = p.ID
            LEFT JOIN Staffs s_plan ON tp.ID_Staffs = s_plan.ID_Persons
            LEFT JOIN Persons ps_plan ON s_plan.ID_Persons = ps_plan.ID
            LEFT JOIN Items i_plan ON s_plan.ID_Persons = i_plan.ID_Staffs
            LEFT JOIN ProfessionNames pn_plan ON i_plan.ID_ProfessionNames = pn_plan.ID
            LEFT JOIN TreatmentPlanElementRelations tper ON tp.ID = tper.ID_TreatmentPlans
            LEFT JOIN TreatmentPlanStages tps ON tper.ID_TreatmentPlanStages = tps.ID
            LEFT JOIN TreatmentPlanElements tpe ON tper.ID_TreatmentPlanElements = tpe.ID
            LEFT JOIN ServiceItemPrices sip ON tper.ID_ServicePrices = sip.ID
            LEFT JOIN ServiceItems si ON sip.ID_ServiceItems = si.ID
            LEFT JOIN ServiceItemContents sic ON si.ID = sic.ID_ServiceItems AND sic.DateTimeTo IS NULL
            LEFT JOIN ServiceFolders sf ON sic.ID_ServiceFoldersParent = sf.ID
            LEFT JOIN ServiceCategories sc ON sf.ID_ServiceCategories = sc.ID
            LEFT JOIN OrderServiceRelation osr ON tper.ID = osr.ID_TreatmentPlanElementRelations
            LEFT JOIN Orders o ON osr.ID_Orders = o.ID
            LEFT JOIN Receptions r ON o.ID_Receptions = r.ID

        WHERE
            tp.ID = ?
            AND tp.IsActive = 1

        ORDER BY
            tps.[Order],
            tpe.[Order],
            si.Name
        """

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (plan_id,))

                columns = [column[0] for column in cursor.description]
                results = []

                while True:
                    rows = cursor.fetchmany(100)
                    if not rows:
                        break
                    results.extend(dict(zip(columns, row)) for row in rows)

                cursor.close()
                return results

        except pyodbc.Error as e:
            logger.error(f"Ошибка БД при извлечении плана лечения #{plan_id}: {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при извлечении плана лечения #{plan_id} из БД Ident: {e}") from e
        except Exception as e:
            logger.error(f"Неожиданная ошибка при извлечении плана #{plan_id}: {e}", exc_info=True)
            raise

    @retry_on_db_error(max_attempts=3, delay=1.0, backoff=2.0)
    def get_treatment_plans_by_card_number(
        self,
        card_number: str
    ) -> List[Dict[str, Any]]:
        """
        Извлекает планы лечения пациента по номеру карты

        Args:
            card_number: Номер карты пациента

        Returns:
            Список планов лечения
        """
        # Валидация
        if not card_number or not str(card_number).strip():
            raise ValueError("card_number не может быть пустым")

        query = """
        SELECT
            -- ПЛАН
            tp.ID AS PlanID,
            tp.Name AS PlanName,
            tp.DateTimeCreated AS CreatedAt,
            tp.IsActive AS IsActive,

            -- ПАЦИЕНТ
            p.Surname + ' ' + p.Name + ISNULL(' ' + p.Patronymic, '') AS PatientFullName,
            pat.CardNumber AS CardNumber,

            -- ВРАЧ
            ps_plan.Surname + ' ' + ps_plan.Name + ISNULL(' ' + ps_plan.Patronymic, '') AS DoctorFullName,
            pn_plan.NameProfession AS DoctorSpeciality,

            -- ЭТАПЫ
            tps.ID AS StageID,
            tps.Name AS StageName,
            tps.[Order] AS StageOrder,

            -- ЭЛЕМЕНТЫ
            tpe.ID AS ElementID,
            tpe.Name AS ElementName,
            tpe.[Order] AS ElementOrder,

            -- УСЛУГИ
            si.ID AS ServiceID,
            si.Name AS ServiceName,
            sc.Name AS ServiceCategory,
            sf.Name AS ServiceFolder,

            -- ЦЕНЫ
            sip.Price AS Price,
            tper.DiscountSum AS DiscountAmount,
            (sip.Price - ISNULL(tper.DiscountSum, 0)) AS TotalAmount,

            -- ЗУБЫ
            tper.TeethLong AS TeethMask,

            -- СТАТУС
            CASE
                WHEN osr.ID IS NOT NULL AND o.DateTimeBillFormed IS NOT NULL THEN 'Выполнено и оплачено'
                WHEN osr.ID IS NOT NULL THEN 'Выполнено'
                ELSE 'Не выполнено'
            END AS Status,

            -- ДЕТАЛИ ВЫПОЛНЕНИЯ
            o.ID AS OrderID,
            o.DateTimeOrder AS ExecutionDate,
            r.ID AS ReceptionID,
            r.PlanStart AS ReceptionDate

        FROM TreatmentPlans tp
            INNER JOIN Patients pat ON tp.ID_Patients = pat.ID_Persons
            INNER JOIN Persons p ON pat.ID_Persons = p.ID
            LEFT JOIN Staffs s_plan ON tp.ID_Staffs = s_plan.ID_Persons
            LEFT JOIN Persons ps_plan ON s_plan.ID_Persons = ps_plan.ID
            LEFT JOIN Items i_plan ON s_plan.ID_Persons = i_plan.ID_Staffs
            LEFT JOIN ProfessionNames pn_plan ON i_plan.ID_ProfessionNames = pn_plan.ID
            LEFT JOIN TreatmentPlanElementRelations tper ON tp.ID = tper.ID_TreatmentPlans
            LEFT JOIN TreatmentPlanStages tps ON tper.ID_TreatmentPlanStages = tps.ID
            LEFT JOIN TreatmentPlanElements tpe ON tper.ID_TreatmentPlanElements = tpe.ID
            LEFT JOIN ServiceItemPrices sip ON tper.ID_ServicePrices = sip.ID
            LEFT JOIN ServiceItems si ON sip.ID_ServiceItems = si.ID
            LEFT JOIN ServiceItemContents sic ON si.ID = sic.ID_ServiceItems AND sic.DateTimeTo IS NULL
            LEFT JOIN ServiceFolders sf ON sic.ID_ServiceFoldersParent = sf.ID
            LEFT JOIN ServiceCategories sc ON sf.ID_ServiceCategories = sc.ID
            LEFT JOIN OrderServiceRelation osr ON tper.ID = osr.ID_TreatmentPlanElementRelations
            LEFT JOIN Orders o ON osr.ID_Orders = o.ID
            LEFT JOIN Receptions r ON o.ID_Receptions = r.ID

        WHERE
            pat.CardNumber = ?
            AND tp.IsActive = 1

        ORDER BY
            tp.DateTimeCreated DESC,
            tps.[Order],
            tpe.[Order],
            si.Name
        """

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (str(card_number),))

                columns = [column[0] for column in cursor.description]
                results = []

                while True:
                    rows = cursor.fetchmany(100)
                    if not rows:
                        break
                    results.extend(dict(zip(columns, row)) for row in rows)

                cursor.close()
                return results

        except pyodbc.Error as e:
            logger.error(f"Ошибка БД при извлечении планов лечения по карте {card_number}: {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при извлечении планов лечения из БД Ident: {e}") from e
        except Exception as e:
            logger.error(f"Неожиданная ошибка при извлечении планов по карте {card_number}: {e}", exc_info=True)
            raise

    @retry_on_db_error(max_attempts=2, delay=0.5, backoff=2.0)
    def get_statistics(self) -> Dict[str, int]:
        """Возвращает статистику БД"""
        queries = {
            'total_receptions': "SELECT COUNT(*) FROM Receptions",
            'total_patients': "SELECT COUNT(*) FROM Patients",
            'total_treatment_plans': "SELECT COUNT(*) FROM TreatmentPlans",
            'receptions_today': """
                SELECT COUNT(*) FROM Receptions
                WHERE CAST(PlanStart AS DATE) = CAST(GETDATE() AS DATE)
            """,
            'receptions_this_week': """
                SELECT COUNT(*) FROM Receptions
                WHERE PlanStart >= DATEADD(WEEK, DATEDIFF(WEEK, 0, GETDATE()), 0)
            """
        }

        stats = {}

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                for key, query in queries.items():
                    cursor.execute(query)
                    stats[key] = cursor.fetchone()[0]

                cursor.close()
                return stats

        except Exception as e:
            logger.error(f"Ошибка при получении статистики БД: {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при получении статистики БД: {e}") from e

    def close(self):
        """Закрывает все соединения в пуле"""
        self.pool.close_all()

    def __del__(self):
        """Деструктор - закрываем пул при удалении объекта"""
        try:
            self.close()
        except Exception:
            pass


if __name__ == "__main__":
    # Тестирование модуля
    from src.config.config_manager import get_config

    try:
        config = get_config("config.example.ini")
        db_config = config.get_database_config()

        connector = IdentConnector(
            server=db_config['server'],
            database=db_config['database'],
            username=db_config['username'],
            password=db_config['password'],
            port=db_config['port'],
            connection_timeout=db_config['connection_timeout'],
            query_timeout=db_config['query_timeout']
        )

        print("✅ Тестирование подключения...")
        if connector.test_connection():
            print("✓ Подключение установлено успешно")

            print("\n✅ Получение статистики БД...")
            stats = connector.get_statistics()
            for key, value in stats.items():
                print(f"  {key}: {value}")

            print("\n✅ Получение записей (последние 5)...")
            import time
            start = time.time()
            receptions = connector.get_receptions(batch_size=5)
            elapsed = time.time() - start
            print(f"  Найдено записей: {len(receptions)}")
            print(f"  Время выполнения: {elapsed:.3f}с")

            if receptions:
                print("\n  Пример записи:")
                first = receptions[0]
                print(f"    ID: {first['ReceptionID']}")
                print(f"    Пациент: {first['PatientFullName']}")
                print(f"    Врач: {first['DoctorFullName']}")
                print(f"    Филиал: {first['Filial']}")
                print(f"    Услуги: {first['Services']}")

        connector.close()

    except FileNotFoundError as e:
        print(f"❌ Ошибка: {e}")
    except ConnectionError as e:
        print(f"❌ Ошибка подключения: {e}")
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
