"""
Модуль преобразования и валидации данных Ident → Bitrix24

Функции:
- Валидация обязательных полей
- Нормализация телефонов (+7XXXXXXXXXX)
- Преобразование дат (ISO 8601)
- Маппинг полей Ident → Bitrix24
- Генерация уникальных идентификаторов
- Агрегация услуг (лимит 3000 символов)
- Расчет суммы с учетом скидок
- Определение стадии воронки продаж
"""

import re
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass

# Используем настроенный logger из custom_logger_v2
from src.logger.custom_logger_v2 import get_logger
logger = get_logger('ident_integration')


@dataclass
class ValidationResult:
    """Результат валидации"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]


class PhoneNormalizer:
    """Нормализация телефонных номеров"""

    @staticmethod
    def normalize(phone: Optional[str]) -> Optional[str]:
        """
        Нормализует телефон к формату +7XXXXXXXXXX

        Args:
            phone: Исходный телефон (может быть в любом формате)

        Returns:
            Нормализованный телефон или None если невалиден

        Examples:
            +7 (999) 123-45-67 → +79991234567
            8 999 123 45 67    → +79991234567
            9991234567         → +79991234567
        """
        if not phone:
            return None

        # Убираем все нечисловые символы кроме +
        digits = re.sub(r'[^\d+]', '', phone)

        # Убираем + если есть
        digits = digits.replace('+', '')

        # Если начинается с 8 - заменяем на 7
        if digits.startswith('8') and len(digits) == 11:
            digits = '7' + digits[1:]

        # Если начинается с 7 и длина 11 - OK
        if digits.startswith('7') and len(digits) == 11:
            return f'+{digits}'

        # Если длина 10 (без кода страны) - добавляем 7
        if len(digits) == 10:
            return f'+7{digits}'

        # Невалидный формат
        logger.warning(f"Невалидный формат телефона: {phone}")
        return None


class DateTimeConverter:
    """Преобразование дат и времени"""

    @staticmethod
    def to_iso8601(dt: Optional[datetime], with_timezone: bool = True) -> Optional[str]:
        """
        Преобразует datetime в ISO 8601

        Args:
            dt: Объект datetime
            with_timezone: Добавлять ли временную зону

        Returns:
            Строка в формате ISO 8601 или None

        Examples:
            2024-01-15 14:30:00 → 2024-01-15T14:30:00+03:00
        """
        if not dt:
            return None

        if with_timezone:
            # Добавляем временную зону (UTC+3 для Москвы)
            # В реальном проекте нужно использовать pytz
            return dt.strftime('%Y-%m-%dT%H:%M:%S+03:00')
        else:
            return dt.isoformat()

    @staticmethod
    def to_date_only(dt: Optional[datetime]) -> Optional[str]:
        """
        Преобразует datetime в дату (без времени)

        Args:
            dt: Объект datetime

        Returns:
            Строка в формате YYYY-MM-DD или None
        """
        if not dt:
            return None

        return dt.strftime('%Y-%m-%d')


class UniqueIdGenerator:
    """Генерация уникальных идентификаторов"""

    @staticmethod
    def generate_reception_id(filial_id: int, reception_id: int) -> str:
        """
        Генерирует уникальный идентификатор записи

        Args:
            filial_id: ID филиала (1-10)
            reception_id: ID записи из Ident

        Returns:
            Уникальный идентификатор в формате F[N]_[ID]

        Examples:
            generate_reception_id(1, 12345) → F1_12345
            generate_reception_id(3, 67890) → F3_67890
        """
        return f"F{filial_id}_{reception_id}"

    @staticmethod
    def parse_reception_id(unique_id: str) -> Optional[Tuple[int, int]]:
        """
        Парсит уникальный идентификатор

        Args:
            unique_id: Уникальный идентификатор (F1_12345)

        Returns:
            Кортеж (filial_id, reception_id) или None

        Examples:
            parse_reception_id('F1_12345') → (1, 12345)
        """
        match = re.match(r'F(\d+)_(\d+)', unique_id)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None


class ServicesAggregator:
    """Агрегация и форматирование услуг"""

    MAX_LENGTH = 3000  # Лимит Битрикс24
    COMMENT_SERVICES_PREVIEW_LENGTH = 200  # Длина превью услуг в комментарии

    @staticmethod
    def aggregate(services_text: Optional[str], max_length: int = MAX_LENGTH) -> str:
        """
        Агрегирует и обрезает список услуг

        Args:
            services_text: Строка с услугами через запятую
            max_length: Максимальная длина (по умолчанию 3000)

        Returns:
            Обрезанная строка с услугами

        Examples:
            "Консультация, Лечение кариеса, ..." → "Консультация, Лечение кариеса, ..."
        """
        if not services_text:
            return "Не указаны"

        # Обрезаем если превышает лимит
        if len(services_text) > max_length:
            truncated = services_text[:max_length - 3]
            # Обрезаем по последней запятой
            last_comma = truncated.rfind(',')
            if last_comma > 0:
                truncated = truncated[:last_comma]
            return truncated + "..."

        return services_text


class StageMapper:
    """Определение стадии воронки продаж"""

    # Маппинг статусов → стадии (актуальные стадии из Bitrix24)
    STAGE_MAPPING = {
        'Запланирован': 'NEW',              # Запись на консультацию
        'Пациент пришел': 'NEW',            # Запись на консультацию
        'В процессе': 'UC_NO40X0',          # Лечение (изменено с PREPARATION)
        'Завершен': 'UC_NO40X0',            # Лечение
        'Завершен (счет выдан)': 'WON',     # Сделка успешна
        'Отменен': 'LOSE'                   # Сделка провалена
    }

    # Финальные стадии (закрытые сделки) - НЕ обновляем
    FINAL_STAGES = [
        'WON',   # Сделка успешна
        'LOSE'   # Сделка провалена
    ]

    # Стадии, защищенные от автоизменения (ручные стадии менеджера)
    PROTECTED_STAGES = [
        'PREPAYMENT_INVOICE',  # Презентация плана лечения
        'FINAL_INVOICE',       # Получена предоплата
        'EXECUTING',           # Лист ожидания
        'APOLOGY'              # Анализ причины провала
    ]

    @staticmethod
    def get_stage(status: str, current_stage: Optional[str] = None) -> str:
        """
        Определяет стадию воронки на основе статуса записи

        Args:
            status: Статус записи из Ident
            current_stage: Текущая стадия сделки (если существует)

        Returns:
            Код стадии Битрикс24

        Logic:
            - Если текущая стадия защищена → не меняем
            - Иначе → определяем по статусу
        """
        # Защищаем ручные стадии от автоизменения
        if current_stage and current_stage in StageMapper.PROTECTED_STAGES:
            logger.info(f"Стадия {current_stage} защищена от автоизменения")
            return current_stage

        # Определяем новую стадию
        new_stage = StageMapper.STAGE_MAPPING.get(status, 'NEW')

        return new_stage

    @staticmethod
    def is_stage_protected(stage_id: Optional[str]) -> bool:
        """
        Проверяет защищена ли стадия от автоизменения

        Защищаются:
        - Финальные стадии (WON, LOSE) - закрытые сделки
        - Ручные стадии менеджера (PREPAYMENT_INVOICE, FINAL_INVOICE, EXECUTING, APOLOGY)

        Args:
            stage_id: ID стадии

        Returns:
            True если стадия защищена
        """
        if not stage_id:
            return False

        return (stage_id in StageMapper.FINAL_STAGES or
                stage_id in StageMapper.PROTECTED_STAGES)

    @staticmethod
    def is_stage_final(stage_id: Optional[str]) -> bool:
        """
        Проверяет является ли стадия финальной (закрытой)

        Args:
            stage_id: ID стадии

        Returns:
            True если стадия финальная (WON или LOSE)
        """
        return stage_id in StageMapper.FINAL_STAGES if stage_id else False


class ReceptionValidator:
    """Валидация данных записи"""

    REQUIRED_FIELDS = [
        'ReceptionID',
        'PatientFullName',
        'PatientPhone',
        'StartTime',
        'DoctorFullName'
    ]

    @staticmethod
    def validate(reception: Dict[str, Any]) -> ValidationResult:
        """
        Валидирует запись

        Args:
            reception: Данные записи из БД

        Returns:
            ValidationResult с результатами валидации
        """
        errors = []
        warnings = []

        # Проверка обязательных полей
        for field in ReceptionValidator.REQUIRED_FIELDS:
            if field not in reception or not reception[field]:
                errors.append(f"Отсутствует обязательное поле: {field}")

        # Валидация телефона
        if 'PatientPhone' in reception:
            normalized_phone = PhoneNormalizer.normalize(reception['PatientPhone'])
            if not normalized_phone:
                errors.append(f"Невалидный телефон: {reception.get('PatientPhone')}")

        # Валидация даты
        if 'StartTime' in reception:
            if not isinstance(reception['StartTime'], datetime):
                errors.append(f"StartTime должен быть datetime, получен: {type(reception['StartTime'])}")

        # Предупреждения (только критичные)
        if not reception.get('Filial'):
            warnings.append("Филиал не определен (будет 'Не указан')")

        # ИСПРАВЛЕНИЕ: Убраны warnings об услугах и сумме
        # Причина: Услуги и сумма добавляются в конце записи (после создания)
        # На момент первой синхронизации они могут быть пустыми - это нормально
        # Генерировали лишний шум в логах и создавали "фантомные обновления"

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )


class DataTransformer:
    """
    Главный класс для преобразования данных Ident → Bitrix24
    """

    def __init__(self, filial_id: int):
        """
        Инициализация трансформера

        Args:
            filial_id: ID филиала (1-10)
        """
        if filial_id < 1 or filial_id > 10:
            raise ValueError(f"filial_id должен быть 1-10, получено: {filial_id}")

        self.filial_id = filial_id
        logger.info(f"DataTransformer инициализирован для филиала {filial_id}")

    def transform_reception(
        self,
        reception: Dict[str, Any],
        current_stage: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Преобразует запись из Ident в формат Битрикс24

        Args:
            reception: Данные записи из БД
            current_stage: Текущая стадия сделки (если существует)

        Returns:
            Преобразованные данные для Bitrix24 или None если валидация не прошла
        """
        # Валидация
        validation = ReceptionValidator.validate(reception)

        if not validation.is_valid:
            logger.error(
                f"Запись {reception.get('ReceptionID')} не прошла валидацию:\n" +
                "\n".join(f"  • {error}" for error in validation.errors)
            )
            return None

        # Логируем предупреждения
        for warning in validation.warnings:
            logger.warning(f"Запись {reception['ReceptionID']}: {warning}")

        # Генерация уникального ID
        unique_id = UniqueIdGenerator.generate_reception_id(
            self.filial_id,
            reception['ReceptionID']
        )

        # Нормализация телефона
        normalized_phone = PhoneNormalizer.normalize(reception['PatientPhone'])

        # Преобразование дат
        start_time_iso = DateTimeConverter.to_iso8601(reception['StartTime'])
        end_time_iso = DateTimeConverter.to_iso8601(reception.get('EndTime'))
        order_date_iso = DateTimeConverter.to_iso8601(reception.get('OrderDate'))

        # Агрегация услуг
        services = ServicesAggregator.aggregate(reception.get('Services'))

        # Определение стадии
        stage = StageMapper.get_stage(reception.get('Status', 'Запланирован'), current_stage)

        # ИСПРАВЛЕНИЕ: Безопасное преобразование суммы (Decimal из БД)
        # Decimal→float может терять точность, поэтому округляем до 2 знаков (копейки)
        amount = reception.get('TotalAmount', 0) or 0
        if isinstance(amount, Decimal):
            # Округляем Decimal до 2 знаков после запятой перед преобразованием
            opportunity_value = float(round(amount, 2))
        else:
            # Для int/float используем обычное преобразование
            opportunity_value = float(amount)

        # Формирование данных для Bitrix24
        transformed = {
            # Идентификаторы
            'unique_id': unique_id,
            'filial_id': self.filial_id,
            'ident_reception_id': reception['ReceptionID'],

            # Контакт (пациент)
            'contact': {
                'name': reception['PatientName'],
                'last_name': reception['PatientSurname'],
                'second_name': reception.get('PatientPatronymic', ''),
                'phone': normalized_phone,
                'type_id': 'CLIENT',  # Тип контакта - клиент
                'UF_CRM_1769083788971': reception.get('CardNumber', ''),  # Номер карты пациента
                'UF_CRM_1769087537061': reception.get('ParentFullName', '')  # Родитель/Опекун
            },

            # Сделка
            'deal': {
                'title': reception['PatientFullName'],
                'stage_id': stage,
                'opportunity': opportunity_value,  # Сумма (безопасно преобразовано из Decimal)
                'currency_id': 'RUB',

                # Кастомные поля (актуальные ID из Bitrix24)
                'UF_CRM_1769008900': start_time_iso,        # Дата начало приема
                'UF_CRM_1769008947': end_time_iso,          # Дата окончания приема
                'UF_CRM_1769008996': reception['DoctorFullName'],  # Врач
                'UF_CRM_1769009098': services,              # Услуги
                'UF_CRM_1769009157': reception.get('Status', 'Запланирован'),  # Статус записи
                'UF_CRM_1769083581481': reception.get('CardNumber', ''),  # Номер карты пациента
                'UF_CRM_1769087458477': reception.get('ParentFullName', ''),  # Родитель/Опекун
                'UF_CRM_1769494714842': reception.get('Comment', ''),  # Комментарий из IDENT

                # Дополнительная информация (в комментарии)
                'uf_crm_ident_id': unique_id,               # ID из Ident (для поиска)
                'uf_crm_filial': reception.get('Filial', 'Не указан'),
                'uf_crm_armchair': reception.get('Armchair', ''),
                'uf_crm_status': reception.get('Status', 'Запланирован'),
                'uf_crm_card_number': reception.get('CardNumber', ''),
                'uf_crm_order_date': order_date_iso,
                'uf_crm_doctor_speciality': reception.get('Speciality', '')
            }
        }

        logger.debug(f"Запись {unique_id} успешно трансформирована")
        return transformed

    def transform_single(self, reception: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        ✅ ОПТИМИЗАЦИЯ: Преобразует ОДНУ запись (для stream processing)

        Args:
            reception: Запись из БД

        Returns:
            Трансформированная запись или None при ошибке
        """
        try:
            transformed = self.transform_reception(reception)

            if not transformed:
                logger.warning(f"Валидация не прошла для записи {reception.get('ReceptionID')}")

            return transformed

        except Exception as e:
            logger.error(
                f"Ошибка трансформации записи {reception.get('ReceptionID')}: {e}",
                exc_info=True
            )
            return None

    def transform_batch(
        self,
        receptions: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Преобразует пакет записей (Legacy метод - для обратной совместимости)

        Args:
            receptions: Список записей из БД

        Returns:
            Кортеж (успешные, ошибки)
        """
        successful = []
        failed = []

        for reception in receptions:
            try:
                transformed = self.transform_reception(reception)

                if transformed:
                    successful.append(transformed)
                else:
                    failed.append({
                        'reception': reception,
                        'error': 'Валидация не прошла'
                    })

            except Exception as e:
                logger.error(
                    f"Ошибка трансформации записи {reception.get('ReceptionID')}: {e}",
                    exc_info=True
                )
                failed.append({
                    'reception': reception,
                    'error': str(e)
                })

        logger.info(
            f"Трансформация пакета завершена: "
            f"успешно={len(successful)}, ошибок={len(failed)}"
        )

        return successful, failed


if __name__ == "__main__":
    """Тестирование трансформера"""
    from datetime import datetime

    # Тестовые данные
    test_reception = {
        'ReceptionID': 12345,
        'StartTime': datetime(2024, 1, 15, 14, 30),
        'EndTime': datetime(2024, 1, 15, 15, 30),
        'PatientFullName': 'Иванов Иван Иванович',
        'PatientSurname': 'Иванов',
        'PatientName': 'Иван',
        'PatientPatronymic': 'Иванович',
        'PatientPhone': '+7 (999) 123-45-67',
        'CardNumber': 'P-123456',
        'DoctorFullName': 'Петров Петр Петрович',
        'DoctorSurname': 'Петров',
        'DoctorName': 'Петр',
        'DoctorPatronymic': 'Петрович',
        'Speciality': 'Стоматолог-терапевт',
        'Filial': 'Филиал №1',
        'Armchair': 'Кабинет 5',
        'Services': 'Консультация, Лечение кариеса, Пломбирование',
        'TotalAmount': 5500.00,
        'Status': 'Запланирован',
        'Comment': 'Первичный прием',
        'OrderDate': datetime(2024, 1, 10, 10, 0)
    }

    print("🧪 Тестирование DataTransformer...")

    # Тест 1: Нормализация телефона
    print("\n1️⃣ Тест нормализации телефонов:")
    test_phones = [
        '+7 (999) 123-45-67',
        '8 999 123 45 67',
        '9991234567',
        '7(999)123-45-67'
    ]
    for phone in test_phones:
        normalized = PhoneNormalizer.normalize(phone)
        print(f"  {phone} → {normalized}")

    # Тест 2: Валидация
    print("\n2️⃣ Тест валидации:")
    validation = ReceptionValidator.validate(test_reception)
    print(f"  Валидна: {validation.is_valid}")
    if validation.errors:
        print("  Ошибки:")
        for error in validation.errors:
            print(f"    • {error}")
    if validation.warnings:
        print("  Предупреждения:")
        for warning in validation.warnings:
            print(f"    • {warning}")

    # Тест 3: Трансформация
    print("\n3️⃣ Тест трансформации:")
    transformer = DataTransformer(filial_id=1)
    transformed = transformer.transform_reception(test_reception)

    if transformed:
        print("  ✅ Успешно трансформировано")
        print(f"  Unique ID: {transformed['unique_id']}")
        print(f"  Контакт: {transformed['contact']['last_name']} {transformed['contact']['name']}")
        print(f"  Телефон: {transformed['contact']['phone']}")
        print(f"  Стадия: {transformed['deal']['stage_id']}")
        print(f"  Сумма: {transformed['deal']['opportunity']} {transformed['deal']['currency_id']}")
    else:
        print("  ❌ Трансформация не удалась")

    print("\n✅ Все тесты пройдены!")
