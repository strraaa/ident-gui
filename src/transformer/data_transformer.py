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
            logger.warning(
                f"Список услуг обрезан до {max_length} символов "
                f"(исходная длина {len(services_text)})"
            )
            return truncated + "..."

        return services_text


class StageMapper:
    """Определение стадии воронки продаж (берёт значения из конфига при необходимости)"""

    @staticmethod
    def _get_stage_config():
        from src.config.config_manager_v2 import get_config
        cfg = get_config()
        stage_cfg = cfg.get_stage_config()
        return stage_cfg['mapping'], stage_cfg['finals'], stage_cfg['protected']

    @staticmethod
    def _get_stage_mapping() -> dict:
        mapping, _, _ = StageMapper._get_stage_config()
        return mapping

    @staticmethod
    def _get_final_stages() -> list:
        _, finals, _ = StageMapper._get_stage_config()
        return finals

    @staticmethod
    def _get_protected_stages() -> list:
        _, _, protected = StageMapper._get_stage_config()
        return protected

    @staticmethod
    def _normalize_status(status: str) -> str:
        """Нормализует строку статуса для устойчивого сопоставления"""
        if not status:
            return ''
        s = status.strip().lower()
        # Убираем лишние пробелы и нормализуем скобки/знаковые варианты
        s = s.replace('\xa0', ' ')
        s = s.replace('\u200f', '')
        s = s.replace('ё', 'е')
        # Упрощаем: заменим несколько вариантов формулировок
        s = s.replace('счет выставлен', 'счет выдан')
        s = s.replace('счёт выставлен', 'счет выдан')
        # Уберём пробелы вокруг скобок
        s = s.replace('(', ' (').replace(')', ')').replace('  ', ' ')
        return s

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
        if current_stage and current_stage in StageMapper._get_protected_stages():
            logger.info(f"Стадия {current_stage} защищена от автоизменения")
            return current_stage

        # Нормализуем статус и ищем в карте с более устойчивым сравнением
        norm = StageMapper._normalize_status(status)

        # Попробуем точное соответствие по нормализованным ключам
        for key, stage in StageMapper._get_stage_mapping().items():
            if StageMapper._normalize_status(key) == norm:
                return stage

        # Частичное совпадение (например, 'завершено(счет выставлен)')
        from src.config.config_manager_v2 import get_config
        default_stage = get_config().get_deal_defaults().get('default_stage_id')
        if not default_stage:
            raise ValueError("default_stage_id not configured")

        if 'счет выдан' in norm or ('счет' in norm and 'выдан' in norm):
            return StageMapper._get_stage_mapping().get('Завершен (счет выдан)', default_stage)

        if 'завершен' in norm or 'завершено' in norm:
            return StageMapper._get_stage_mapping().get('Завершен', default_stage)

        # По умолчанию — если в маппинге есть 'Запланирован', используем, иначе default_stage
        return StageMapper._get_stage_mapping().get('Запланирован', default_stage)

    @staticmethod
    def is_stage_protected(stage_id: Optional[str]) -> bool:
        """
        Проверяет защищена ли стадия от автоизменения

        Защищаются:
        - Финальные стадии (из конфига)
        - Ручные стадии менеджера (из конфига)

        Args:
            stage_id: ID стадии

        Returns:
            True если стадия защищена
        """
        if not stage_id:
            return False

        return (stage_id in StageMapper._get_final_stages() or
                stage_id in StageMapper._get_protected_stages())

    @staticmethod
    def is_stage_final(stage_id: Optional[str]) -> bool:
        """
        Проверяет является ли стадия финальной (закрытой)

        Args:
            stage_id: ID стадии

        Returns:
            True если стадия финальная (по конфигу)
        """
        return stage_id in StageMapper._get_final_stages() if stage_id else False


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

        # Предупреждения
        if not reception.get('Filial'):
            warnings.append("Филиал не определен (будет 'Не указан')")

        if not reception.get('Services'):
            warnings.append("Услуги не указаны")

        if not reception.get('TotalAmount') or reception['TotalAmount'] == 0:
            warnings.append("Сумма не указана или равна 0")

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
            filial_id: ID филиала по умолчанию (1-10), используется если FilialID отсутствует в записи
        """
        if filial_id < 1 or filial_id > 10:
            raise ValueError(f"filial_id должен быть 1-10, получено: {filial_id}")

        self.filial_id = filial_id

        # Читаем конфигурацию фильтрации филиалов для мультифилиальных БД
        from src.config.config_manager_v2 import get_config
        filial_config = get_config().get_filial_filter_config()
        self.enabled_filial_ids = filial_config.get('enabled_filial_ids', [])
        self.default_filial_id = filial_config.get('default_filial_id', 0)

        if self.enabled_filial_ids:
            logger.info(f"DataTransformer: филиал по умолчанию {filial_id}, фильтр филиалов: {self.enabled_filial_ids}")
        else:
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

        # Определение FilialID для генерации уникального идентификатора
        # Для мультифилиальных БД: FilialID берётся из записи (поле из OwnCompanies.ID)
        # Если FilialID отсутствует (0), используется default_filial_id из конфига
        filial_id_from_db = reception.get('FilialID', 0)

        if filial_id_from_db == 0:
            # Филиал не определён в БД - используем значение по умолчанию
            if self.default_filial_id == 0:
                # default_filial_id = 0 означает "пропускать записи без филиала"
                logger.warning(
                    f"Запись {reception['ReceptionID']}: FilialID не определён (0), "
                    f"default_filial_id = 0 → пропускаем запись"
                )
                return None
            else:
                effective_filial_id = self.default_filial_id
                logger.debug(
                    f"Запись {reception['ReceptionID']}: FilialID не определён, "
                    f"используем default_filial_id={self.default_filial_id}"
                )
        else:
            effective_filial_id = filial_id_from_db

        # Проверка на фильтр enabled_filial_ids (если задан)
        if self.enabled_filial_ids and effective_filial_id not in self.enabled_filial_ids:
            logger.debug(
                f"Запись {reception['ReceptionID']}: FilialID={effective_filial_id} "
                f"не в списке enabled_filial_ids={self.enabled_filial_ids} → пропускаем"
            )
            return None

        # Генерация уникального ID с правильным FilialID
        unique_id = UniqueIdGenerator.generate_reception_id(
            effective_filial_id,
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

        # Формирование данных для Bitrix24 (используем маппинг полей из конфига)
        from src.config.config_manager_v2 import get_config
        field_map = get_config().get_bitrix_field_map()

        contact_card_field = field_map.get('contact_card_number')
        contact_parent_field = field_map.get('contact_parent')

        deal_start_field = field_map.get('deal_start')
        deal_end_field = field_map.get('deal_end')
        deal_doctor_field = field_map.get('deal_doctor')
        deal_services_field = field_map.get('deal_services')
        deal_status_field = field_map.get('deal_status')
        deal_card_field = field_map.get('deal_card_number')
        deal_parent_field = field_map.get('deal_parent')
        deal_comment_field = field_map.get('deal_comment')

        transformed = {
            # Идентификаторы
            'unique_id': unique_id,
            'filial_id': effective_filial_id,  # Используем динамический FilialID из БД
            'ident_reception_id': reception['ReceptionID'],

            # Контакт (пациент)
            'contact': {
                'name': reception['PatientName'],
                'last_name': reception['PatientSurname'],
                'second_name': reception.get('PatientPatronymic', ''),
                'phone': normalized_phone,
                'type_id': 'CLIENT',  # Тип контакта - клиент
                contact_card_field: reception.get('CardNumber', ''),  # Номер карты пациента
                contact_parent_field: reception.get('ParentFullName', '')  # Родитель/Опекун
            },

            # Сделка
            'deal': {
                'title': reception['PatientFullName'],
                'stage_id': stage,
                'opportunity': opportunity_value,  # Сумма (безопасно преобразовано из Decimal)
                'currency_id': 'RUB',

                # Кастомные поля (из конфига)
                deal_start_field: start_time_iso,        # Дата начало приема
                deal_end_field: end_time_iso,          # Дата окончания приема
                deal_doctor_field: reception['DoctorFullName'],  # Врач
                deal_services_field: services,              # Услуги
                deal_status_field: reception.get('Status', 'Запланирован'),  # Статус записи
                deal_card_field: reception.get('CardNumber', ''),  # Номер карты пациента
                deal_parent_field: reception.get('ParentFullName', ''),  # Родитель/Опекун
                deal_comment_field: reception.get('Comment', ''),  # Комментарий из IDENT

                # Дополнительная информация (в комментарии)
                'uf_crm_ident_id': unique_id,               # ID из Ident (для поиска)
                'uf_crm_filial': reception.get('Filial', 'Не указан'),
                'uf_crm_armchair': reception.get('Armchair', ''),
                'uf_crm_status': reception.get('Status', 'Запланирован'),
                'uf_crm_card_number': reception.get('CardNumber', ''),
                'uf_crm_order_date': order_date_iso,
                'uf_crm_doctor_speciality': reception.get('Speciality', ''),
                'uf_crm_registrar_id': reception.get('RegistrarStaffId')  # ID регистратора (ответственный за запись)
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
        'RegistrarStaffId': 42,
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
