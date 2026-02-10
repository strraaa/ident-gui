"""
Модуль управления конфигурацией (Версия 2.0 - Оптимизированная)

ИСПРАВЛЕНИЯ:
- ✅ Использование Windows DPAPI для шифрования (ключ не хранится в файле)
- ✅ Блокирующая валидация конфигурации при загрузке
- ✅ Проверка прав доступа к файлу конфигурации
- ✅ Валидация обязательных полей перед запуском
- ✅ Secure defaults для всех параметров
"""

import configparser
import os
import sys
import stat
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

# Windows DPAPI для безопасного шифрования
try:
    import win32crypt
    DPAPI_AVAILABLE = True
except ImportError:
    DPAPI_AVAILABLE = False
    print(
        "⚠️  ВНИМАНИЕ: Модуль win32crypt не установлен!\n"
        "Шифрование паролей будет недоступно.\n"
        "Установите: pip install pywin32"
    )

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Ошибка валидации конфигурации"""
    pass


class ConfigManager:
    """
    Менеджер конфигурации с безопасным шифрованием через Windows DPAPI

    Ключевые улучшения:
    - Шифрование через OS (DPAPI) - ключ НЕ хранится в файле
    - Блокирующая валидация при запуске
    - Проверка прав доступа к config.ini
    - Secure defaults
    """

    # Поля, которые должны быть зашифрованы
    ENCRYPTED_FIELDS = [
        ('Database', 'password'),
        ('Bitrix24', 'token'),
        ('bitrix', 'token'),
        ('Notifications', 'smtp_password')
    ]

    # Обязательные поля для проверки (Bitrix секция определяется динамически)
    REQUIRED_FIELDS = [
        ('Database', 'server', 'Адрес сервера БД'),
        ('Database', 'database', 'Имя базы данных'),
        ('Database', 'username', 'Имя пользователя БД'),
        ('Database', 'password', 'Пароль БД'),
        ('Sync', 'filial_id', 'ID филиала')
    ]

    def __init__(self, config_path: str = "config.ini", require_config: bool = False):
        """
        Инициализация менеджера конфигурации

        Args:
            config_path: Путь к файлу конфигурации
            require_config: Если True — требуем наличия `config.ini` в указанном пути,
                            даже если есть `config.example.ini` (useful for production)

        Raises:
            FileNotFoundError: Если файл конфигурации не найден
            ConfigValidationError: Если конфигурация невалидна
        """
        self.config_path = Path(config_path)

        # Allow environment to enforce requiring config.ini in production
        env_require = os.environ.get('IDENT_REQUIRE_CONFIG', '').lower() in ('1', 'true', 'yes') or os.environ.get('IDENT_ENV', '').lower() == 'production'
        self.require_config = require_config or env_require

        self.config = configparser.ConfigParser(interpolation=None)

        # Ищем пример конфигурации (config.example.ini) рядом с config_path или в CWD
        example_path = self.config_path.with_name('config.example.ini')
        cwd_example = Path.cwd() / 'config.example.ini'

        files_to_read = []

        # Если example рядом с config_path существует - используем его как источник defaults
        if example_path.exists():
            files_to_read.append(str(example_path))
        elif cwd_example.exists():
            files_to_read.append(str(cwd_example))

        # Если пользовательский config.ini существует - он должен переопределять example
        if self.config_path.exists():
            files_to_read.append(str(self.config_path))
        else:
            if self.require_config:
                # В режиме production требуем наличия config.ini — это критическая ошибка
                raise FileNotFoundError(
                    f"Требуется файл конфигурации: {self.config_path} (IDENT_REQUIRE_CONFIG=1 или IDENT_ENV=production)")

            if not files_to_read:
                # Ни config.ini ни config.example.ini не найдены - критическая ошибка
                raise FileNotFoundError(
                    f"Файл конфигурации не найден: {self.config_path}\n"
                    f"Создайте {self.config_path.name} на основе config.example.ini"
                )
            # config.ini отсутствует, но есть example — используем example и логируем предупреждение
            logger.warning(f"Файл {self.config_path} не найден — использую {files_to_read[0]} как дефолтную конфигурацию")

        # Проверяем права доступа (только на Unix-like системах) если найден config.ini
        if sys.platform != 'win32' and self.config_path.exists():
            self._check_file_permissions()

        # Загружаем конфигурацию (сначала example как defaults, затем config.ini для переопределения)
        try:
            read_files = self.config.read(files_to_read, encoding='utf-8')
            logger.info(f"Загружены конфигурационные файлы: {read_files}")
        except Exception as e:
            raise ConfigValidationError(f"Ошибка чтения файла конфигурации: {e}") from e

        # ✅ КРИТИЧНО: Блокирующая валидация ПЕРЕД запуском
        validation_errors = self.validate()
        if validation_errors:
            error_msg = "❌ КОНФИГУРАЦИЯ НЕВАЛИДНА!\n\n" + "\n".join(
                f"  • {error}" for error in validation_errors
            )
            raise ConfigValidationError(error_msg)

        logger.info(f"Конфигурация загружена из {self.config_path}")

    def _get_bitrix_section(self) -> Optional[str]:
        """Определяет секцию конфигурации Bitrix24."""
        if self.config.has_section('bitrix'):
            return 'bitrix'
        if self.config.has_section('Bitrix24'):
            return 'Bitrix24'
        return None

    def _check_file_permissions(self):
        """
        Проверяет права доступа к файлу конфигурации

        Файл не должен быть доступен другим пользователям (chmod 600)
        """
        try:
            file_stat = self.config_path.stat()
            file_mode = stat.S_IMODE(file_stat.st_mode)

            # Проверяем что файл не доступен группе и остальным (маска 077)
            if file_mode & 0o077:
                logger.warning(
                    f"⚠️  НЕБЕЗОПАСНЫЕ ПРАВА ДОСТУПА к {self.config_path}!\n"
                    f"Текущие права: {oct(file_mode)}\n"
                    f"Рекомендуется: chmod 600 {self.config_path}\n"
                    f"Файл содержит чувствительные данные и не должен быть доступен другим пользователям!"
                )
        except Exception as e:
            logger.warning(f"Не удалось проверить права доступа к файлу: {e}")

    def validate(self) -> List[str]:
        """
        Валидирует конфигурацию

        Returns:
            Список ошибок валидации (пустой список = валидна)
        """
        errors = []

        # 1. Проверка обязательных полей
        for section, option, description in self.REQUIRED_FIELDS:
            if not self.config.has_section(section):
                errors.append(f"Отсутствует секция [{section}]")
                continue

            if not self.config.has_option(section, option):
                errors.append(f"Отсутствует параметр [{section}].{option} ({description})")
                continue

            value = self.config.get(section, option, fallback='').strip()
            if not value:
                errors.append(f"Пустое значение [{section}].{option} ({description})")

        # 1.1 Проверка обязательных полей Bitrix (секция может быть [bitrix] или [Bitrix24])
        bitrix_section = self._get_bitrix_section()
        if not bitrix_section:
            errors.append("Отсутствует секция [bitrix] или [Bitrix24]")
        else:
            for option, description in [
                ('webhook_url', 'URL webhook Битрикс24'),
                ('token', 'Токен webhook')
            ]:
                if not self.config.has_option(bitrix_section, option):
                    errors.append(f"Отсутствует параметр [{bitrix_section}].{option} ({description})")
                else:
                    value = self.config.get(bitrix_section, option, fallback='').strip()
                    if not value:
                        errors.append(f"Пустое значение [{bitrix_section}].{option} ({description})")

        # 2. Валидация типов и диапазонов
        try:
            # Database port
            if self.config.has_option('Database', 'port'):
                port = self.config.getint('Database', 'port')
                if port < 1 or port > 65535:
                    errors.append(f"Некорректный порт БД: {port} (должен быть 1-65535)")

            # Sync interval
            if self.config.has_option('Sync', 'interval_minutes'):
                interval = self.config.getint('Sync', 'interval_minutes')
                if interval < 1 or interval > 1440:
                    errors.append(f"Некорректный интервал синхронизации: {interval} мин (должен быть 1-1440)")

            # Batch size
            if self.config.has_option('Sync', 'batch_size'):
                batch_size = self.config.getint('Sync', 'batch_size')
                if batch_size < 1 or batch_size > 1000:
                    errors.append(f"Некорректный batch_size: {batch_size} (должен быть 1-1000)")

            # Filial ID
            if self.config.has_option('Sync', 'filial_id'):
                filial_id = self.config.getint('Sync', 'filial_id')
                if filial_id < 1 or filial_id > 10:
                    errors.append(f"Некорректный filial_id: {filial_id} (должен быть 1-10)")

            # FilialFilter validation (опциональная секция для мультифилиальных БД)
            if self.config.has_section('FilialFilter'):
                # enabled_filial_ids
                if self.config.has_option('FilialFilter', 'enabled_filial_ids'):
                    enabled = self.config.get('FilialFilter', 'enabled_filial_ids', fallback='').strip()
                    if enabled:  # Если не пустое
                        try:
                            ids = [int(x.strip()) for x in enabled.split(',') if x.strip()]
                            for fid in ids:
                                if fid < 1 or fid > 100:
                                    errors.append(f"Некорректный ID филиала в enabled_filial_ids: {fid} (должен быть 1-100)")
                        except ValueError:
                            errors.append(f"enabled_filial_ids должен содержать числа через запятую: '{enabled}'")

                # default_filial_id
                if self.config.has_option('FilialFilter', 'default_filial_id'):
                    default_fid = self.config.getint('FilialFilter', 'default_filial_id')
                    if default_fid < 0 or default_fid > 100:
                        errors.append(f"Некорректный default_filial_id: {default_fid} (должен быть 0-100)")

        except ValueError as e:
            errors.append(f"Ошибка типа данных в конфигурации: {e}")

        # 3. Валидация URL webhook
        if bitrix_section and self.config.has_option(bitrix_section, 'webhook_url'):
            webhook_url = self.config.get(bitrix_section, 'webhook_url')
            if not webhook_url.startswith(('http://', 'https://')):
                errors.append(f"Некорректный webhook_url: должен начинаться с http:// или https://")

        # 3.1 Валидация Bitrix24-констант (стадии/поля)
        required_sections = {
            'contact_fields': ['card_number', 'parent_name'],
            'deal_fields': [
                'ident_id', 'start_time', 'end_time', 'doctor_name', 'services',
                'status', 'card_number', 'parent_name', 'comment', 'filial',
                'armchair', 'status_text', 'treatment_plan', 'treatment_plan_hash'
            ],
            'deal_defaults': ['default_stage_id'],
            'deal_stages': ['mapping', 'final', 'protected'],
            'pipelines': ['deal_category_id'],
            'lead_statuses': ['closed'],
        }
        for section, keys in required_sections.items():
            if not self.config.has_section(section):
                errors.append(f"Отсутствует секция [{section}] для Bitrix24")
                continue
            for key in keys:
                if not self.config.has_option(section, key):
                    errors.append(f"Отсутствует параметр [{section}].{key}")
                else:
                    value = self.config.get(section, key, fallback='').strip()
                    if not value:
                        errors.append(f"Пустое значение [{section}].{key}")

        # 4. Проверка DPAPI (только если зашифрованные значения реально используются)
        if not DPAPI_AVAILABLE:
            encrypted_in_use = False
            for section, option in self.ENCRYPTED_FIELDS:
                if self.config.has_option(section, option):
                    value = self.config.get(section, option, fallback='')
                    if value and value.startswith('DPAPI:'):
                        encrypted_in_use = True
                        break
            if encrypted_in_use:
                errors.append(
                    "Модуль win32crypt не установлен, но в конфигурации есть DPAPI-зашифрованные значения.\n"
                    "Установите: pip install pywin32"
                )
            else:
                logger.warning("DPAPI недоступен: шифрование будет отключено, но конфигурация не использует DPAPI.")

        return errors

    def _encrypt_value(self, plaintext: str) -> str:
        """
        Шифрует значение через Windows DPAPI

        Args:
            plaintext: Исходное значение

        Returns:
            Зашифрованное значение (hex-строка)

        Raises:
            RuntimeError: Если DPAPI недоступен
        """
        if not DPAPI_AVAILABLE:
            raise RuntimeError(
                "Windows DPAPI недоступен! Установите: pip install pywin32"
            )

        try:
            # Шифруем через DPAPI (ключ привязан к текущему пользователю Windows)
            encrypted_bytes = win32crypt.CryptProtectData(
                plaintext.encode('utf-8'),
                None,  # Description
                None,  # Optional entropy
                None,  # Reserved
                None,  # Prompt struct
                0      # Flags
            )

            # Возвращаем как hex-строку с префиксом
            return 'DPAPI:' + encrypted_bytes.hex()

        except Exception as e:
            logger.error(f"Ошибка шифрования через DPAPI: {e}", exc_info=True)
            raise RuntimeError(f"Не удалось зашифровать значение: {e}") from e

    def _decrypt_value(self, encrypted_hex: str) -> str:
        """
        Дешифрует значение через Windows DPAPI

        Args:
            encrypted_hex: Зашифрованное значение (hex-строка с префиксом DPAPI:)

        Returns:
            Расшифрованное значение

        Raises:
            RuntimeError: Если DPAPI недоступен или расшифровка не удалась
        """
        if not DPAPI_AVAILABLE:
            raise RuntimeError(
                "Windows DPAPI недоступен! Установите: pip install pywin32"
            )

        # Проверяем префикс
        if not encrypted_hex.startswith('DPAPI:'):
            # Если нет префикса - это незашифрованное значение (для обратной совместимости)
            logger.warning("Обнаружено незашифрованное значение! Рекомендуется перезашифровать.")
            return encrypted_hex

        try:
            # Убираем префикс и конвертируем из hex
            encrypted_bytes = bytes.fromhex(encrypted_hex[6:])

            # Дешифруем через DPAPI
            decrypted_bytes = win32crypt.CryptUnprotectData(
                encrypted_bytes,
                None,  # Optional entropy
                None,  # Reserved
                None,  # Prompt struct
                0      # Flags
            )[1]  # Возвращает (description, data)

            return decrypted_bytes.decode('utf-8')

        except Exception as e:
            logger.error(f"Ошибка расшифровки через DPAPI: {e}", exc_info=True)
            raise RuntimeError(
                f"Не удалось расшифровать значение!\n"
                f"Возможно, файл был зашифрован другим пользователем Windows.\n"
                f"Ошибка: {e}"
            ) from e

    def encrypt_sensitive_fields(self) -> int:
        """
        Шифрует все чувствительные поля в конфигурации

        Returns:
            Количество зашифрованных полей
        """
        if not DPAPI_AVAILABLE:
            logger.error("DPAPI недоступен - шифрование невозможно")
            return 0

        encrypted_count = 0

        for section, option in self.ENCRYPTED_FIELDS:
            if not self.config.has_section(section):
                continue

            if not self.config.has_option(section, option):
                continue

            current_value = self.config.get(section, option, fallback='')

            # Пропускаем пустые и уже зашифрованные
            if not current_value or current_value.startswith('DPAPI:'):
                continue

            # Шифруем
            try:
                encrypted_value = self._encrypt_value(current_value)
                self.config.set(section, option, encrypted_value)
                encrypted_count += 1
                logger.info(f"Зашифровано поле [{section}].{option}")
            except Exception as e:
                logger.error(f"Не удалось зашифровать [{section}].{option}: {e}")

        # Сохраняем изменения
        if encrypted_count > 0:
            self._save_config()
            logger.info(f"Конфигурация сохранена. Зашифровано полей: {encrypted_count}")

        return encrypted_count

    def _save_config(self):
        """Сохраняет конфигурацию в файл"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                self.config.write(f)
            # Best-effort ограничение прав доступа
            try:
                if sys.platform == 'win32':
                    import subprocess
                    user = os.environ.get('USERNAME') or os.getlogin()
                    subprocess.run(
                        ['icacls', str(self.config_path), '/inheritance:r',
                         '/grant', f'{user}:(R,W)', 'SYSTEM:(F)', 'Administrators:(F)'],
                        check=False,
                        capture_output=True,
                        text=True
                    )
                else:
                    os.chmod(self.config_path, 0o600)
            except Exception as e:
                logger.warning(f"Не удалось ограничить права доступа к {self.config_path}: {e}")
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}", exc_info=True)
            raise

    def _get_decrypted(self, section: str, option: str, fallback: Any = None) -> Any:
        """Получает значение с автоматической дешифровкой"""
        value = self.config.get(section, option, fallback=fallback)

        # Проверяем нужно ли дешифровать
        if (section, option) in self.ENCRYPTED_FIELDS and value and value.startswith('DPAPI:'):
            try:
                return self._decrypt_value(value)
            except Exception as e:
                logger.error(f"Ошибка расшифровки [{section}].{option}: {e}")
                raise

        return value

    # ========== ПУБЛИЧНЫЕ МЕТОДЫ ДЛЯ ДОСТУПА К КОНФИГУРАЦИИ ==========

    def get_database_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию БД с расшифрованным паролем"""
        return {
            'server': self.config.get('Database', 'server'),
            'port': self.config.getint('Database', 'port', fallback=1433),
            'database': self.config.get('Database', 'database'),
            'username': self.config.get('Database', 'username'),
            'password': self._get_decrypted('Database', 'password'),  # Расшифровываем
            'connection_timeout': self.config.getint('Database', 'connection_timeout', fallback=10),
            'query_timeout': self.config.getint('Database', 'query_timeout', fallback=30),
        }

    def get_bitrix24_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию Битрикс24 с расшифрованным токеном"""
        # Читаем ID ответственного (может быть пустым)
        section = self._get_bitrix_section()
        if not section:
            raise ConfigValidationError("?????? Bitrix24 ?? ??????? (????????? [bitrix] ??? [Bitrix24])")

        assigned_by_id = self.config.get(section, 'default_assigned_by_id', fallback='').strip()

        return {
            'webhook_url': self.config.get(section, 'webhook_url'),
            'token': self._get_decrypted(section, 'token'),  # Расшифровиваем
            'request_timeout': self.config.getint(section, 'request_timeout', fallback=30),
            'max_retries': self.config.getint(section, 'max_retries', fallback=3),
            'default_assigned_by_id': int(assigned_by_id) if assigned_by_id else None,
        }

    def get_bitrix_field_map(self) -> Dict[str, str]:
        """Возвращает маппинг логических имён полей → фактические UF_CRM имена"""
        return {
            'ident_field': self.config.get('deal_fields', 'ident_id'),
            'contact_card_number': self.config.get('contact_fields', 'card_number'),
            'contact_parent': self.config.get('contact_fields', 'parent_name'),
            'deal_start': self.config.get('deal_fields', 'start_time'),
            'deal_end': self.config.get('deal_fields', 'end_time'),
            'deal_doctor': self.config.get('deal_fields', 'doctor_name'),
            'deal_services': self.config.get('deal_fields', 'services'),
            'deal_status': self.config.get('deal_fields', 'status'),
            'deal_card_number': self.config.get('deal_fields', 'card_number'),
            'deal_parent': self.config.get('deal_fields', 'parent_name'),
            'deal_comment': self.config.get('deal_fields', 'comment'),
            'treatment_plan': self.config.get('deal_fields', 'treatment_plan'),
            'treatment_plan_hash': self.config.get('deal_fields', 'treatment_plan_hash'),
            'filial': self.config.get('deal_fields', 'filial'),
            'armchair': self.config.get('deal_fields', 'armchair'),
            'status_field': self.config.get('deal_fields', 'status_text'),
            'legacy_card_number': self.config.get('deal_fields', 'legacy_card_number', fallback=''),
            'order_date': self.config.get('deal_fields', 'order_date', fallback=''),
            'doctor_speciality': self.config.get('deal_fields', 'doctor_speciality', fallback=''),
        }

    def get_stage_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию стадий: маппинг, финальные и защищённые"""
        # Считываем CSV строки из конфига
        def _get_map(section, option):
            s = self.config.get(section, option)
            # Формат: Статус1:STAGE_ID,Статус2:STAGE_ID
            items = [i.strip() for i in s.split(',') if i.strip()]
            res = {}
            for it in items:
                if ':' in it:
                    k, v = it.split(':', 1)
                    res[k.strip()] = v.strip()
            return res

        mapping = _get_map('deal_stages', 'mapping')
        finals = [s.strip() for s in self.config.get('deal_stages', 'final').split(',') if s.strip()]
        protected = [s.strip() for s in self.config.get('deal_stages', 'protected').split(',') if s.strip()]

        return {
            'mapping': mapping,
            'finals': finals,
            'protected': protected,
        }

    def get_lead_status_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию статусов лидов"""
        closed = [s.strip() for s in self.config.get('lead_statuses', 'closed').split(',') if s.strip()]
        return {'closed': closed}

    def get_deal_defaults(self) -> Dict[str, Any]:
        """Возвращает значения по умолчанию для сделок"""
        return {
            'default_stage_id': self.config.get('deal_defaults', 'default_stage_id'),
        }

    def get_pipeline_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию воронок"""
        return {
            'deal_category_id': self.config.getint('pipelines', 'deal_category_id'),
        }

    def get_sync_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию синхронизации"""
        # Поддержка старого имени initial_sync_days для обратной совместимости
        if self.config.has_option('Sync', 'initial_days'):
            initial_days = self.config.getint('Sync', 'initial_days', fallback=7)
        else:
            initial_days = self.config.getint('Sync', 'initial_sync_days', fallback=7)
        return {
            'filial_id': self.config.getint('Sync', 'filial_id'),
            'interval_minutes': self.config.getint('Sync', 'interval_minutes', fallback=2),
            'batch_size': self.config.getint('Sync', 'batch_size', fallback=50),
            'initial_days': initial_days,
            'enable_update_existing': self.config.getboolean('Sync', 'enable_update_existing', fallback=True),
        }

    def get_filial_filter_config(self) -> Dict[str, Any]:
        """
        Возвращает конфигурацию фильтрации филиалов для мультифилиальных БД.

        Returns:
            Dict с ключами:
            - enabled_filial_ids: List[int] - список ID филиалов для синхронизации (пустой = все)
            - default_filial_id: int - ID по умолчанию для записей без филиала (0 = пропускать)
        """
        if not self.config.has_section('FilialFilter'):
            return {
                'enabled_filial_ids': [],
                'default_filial_id': 0,
            }

        # enabled_filial_ids
        enabled_str = self.config.get('FilialFilter', 'enabled_filial_ids', fallback='').strip()
        if enabled_str:
            try:
                enabled_ids = [int(x.strip()) for x in enabled_str.split(',') if x.strip()]
            except ValueError:
                # Если парсинг не удался - используем пустой список (синхронизировать все)
                enabled_ids = []
        else:
            enabled_ids = []

        # default_filial_id
        default_fid = self.config.getint('FilialFilter', 'default_filial_id', fallback=0)

        return {
            'enabled_filial_ids': enabled_ids,
            'default_filial_id': default_fid,
        }

    def get_logging_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию логирования"""
        # Поддержка старого ключа max_file_size_mb
        if self.config.has_option('Logging', 'max_log_size_mb'):
            max_log_size_mb = self.config.getint('Logging', 'max_log_size_mb', fallback=20)
        else:
            max_log_size_mb = self.config.getint('Logging', 'max_file_size_mb', fallback=20)
        return {
            'level': self.config.get('Logging', 'level', fallback='INFO'),
            'log_dir': self.config.get('Logging', 'log_dir', fallback='logs'),
            'rotation_days': self.config.getint('Logging', 'rotation_days', fallback=30),
            'mask_personal_data': self.config.getboolean('Logging', 'mask_personal_data', fallback=True),
            'max_log_size_mb': max_log_size_mb,
            'max_backup_files': self.config.getint('Logging', 'max_backup_files', fallback=5),
        }

    def get_queue_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию очереди"""
        # Поддержка старых ключей
        enabled = self.config.getboolean('Queue', 'enabled', fallback=True)
        if self.config.has_option('Queue', 'max_size'):
            max_size = self.config.getint('Queue', 'max_size', fallback=1000)
        else:
            max_size = self.config.getint('Queue', 'max_queue_size', fallback=1000)
        if self.config.has_option('Queue', 'persistence_file'):
            persistence_file = self.config.get('Queue', 'persistence_file', fallback='queue.json')
        else:
            persistence_file = self.config.get('Queue', 'queue_db_path', fallback='queue.json')
        return {
            'enabled': enabled,
            'max_size': max_size,
            'persistence_file': persistence_file,
            'retry_interval_minutes': self.config.getint('Queue', 'retry_interval_minutes', fallback=5),
            'max_retry_attempts': self.config.getint('Queue', 'max_retry_attempts', fallback=3),
        }

    def get_monitoring_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию мониторинга"""
        # Поддержка старых ключей enabled/host/port/debug
        enable_web = self.config.getboolean('Monitoring', 'enable_web_interface', fallback=None) if self.config.has_option('Monitoring', 'enable_web_interface') else None
        if enable_web is None:
            enable_web = self.config.getboolean('Monitoring', 'enabled', fallback=False)
        web_port = self.config.getint('Monitoring', 'web_port', fallback=None) if self.config.has_option('Monitoring', 'web_port') else None
        if web_port is None:
            web_port = self.config.getint('Monitoring', 'port', fallback=8080)
        enable_metrics = self.config.getboolean('Monitoring', 'enable_metrics', fallback=None) if self.config.has_option('Monitoring', 'enable_metrics') else None
        if enable_metrics is None:
            enable_metrics = self.config.getboolean('Monitoring', 'debug', fallback=False)

        return {
            'enable_web_interface': enable_web,
            'web_host': self.config.get('Monitoring', 'web_host', fallback=self.config.get('Monitoring', 'host', fallback='localhost')),
            'web_port': web_port,
            'enable_metrics': enable_metrics,
        }

    def get_performance_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию производительности"""
        return {
            'max_processing_time': self.config.getint('Performance', 'max_processing_time', fallback=2),
            'batch_pause': self.config.getint('Performance', 'batch_pause', fallback=1),
            'db_pool_size': self.config.getint('Performance', 'db_pool_size', fallback=2),
        }

    def get_all_config(self) -> Dict[str, Dict[str, Any]]:
        """Возвращает всю конфигурацию"""
        return {
            'database': self.get_database_config(),
            'bitrix24': self.get_bitrix24_config(),
            'sync': self.get_sync_config(),
            'logging': self.get_logging_config(),
            'queue': self.get_queue_config(),
            'monitoring': self.get_monitoring_config(),
        }


# Singleton instance
_config_instance: Optional[ConfigManager] = None


def get_config(config_path: str = "config.ini", require_config: bool = False) -> ConfigManager:
    """
    Получает singleton экземпляр ConfigManager

    Args:
        config_path: Путь к файлу конфигурации
        require_config: Если True — требуем наличия `config.ini` (production mode)

    Returns:
        Экземпляр ConfigManager
    """
    global _config_instance

    if _config_instance is None:
        _config_instance = ConfigManager(config_path, require_config=require_config)

    return _config_instance


if __name__ == "__main__":
    """Утилита для шифрования конфигурации"""
    import argparse

    parser = argparse.ArgumentParser(description='Утилита управления конфигурацией')
    parser.add_argument('--config', default='config.ini', help='Путь к файлу конфигурации')
    parser.add_argument('--encrypt', action='store_true', help='Зашифровать чувствительные поля')
    parser.add_argument('--validate', action='store_true', help='Валидировать конфигурацию')

    args = parser.parse_args()

    try:
        config = ConfigManager(args.config)

        if args.encrypt:
            print("🔐 Шифрование чувствительных полей...")
            count = config.encrypt_sensitive_fields()
            print(f"✅ Зашифровано полей: {count}")

        if args.validate:
            print("✅ Конфигурация валидна!")

        if not args.encrypt and not args.validate:
            print("📋 Конфигурация:")
            print("\nБаза данных:")
            db_config = config.get_database_config()
            print(f"  Server: {db_config['server']}:{db_config['port']}")
            print(f"  Database: {db_config['database']}")
            print(f"  Username: {db_config['username']}")
            print(f"  Password: {'*' * 8}")

            print("\nБитрикс24:")
            b24_config = config.get_bitrix24_config()
            print(f"  Webhook URL: {b24_config['webhook_url'][:50]}...")
            print(f"  Token: {'*' * 8}")

            print("\nСинхронизация:")
            sync_config = config.get_sync_config()
            for key, value in sync_config.items():
                print(f"  {key}: {value}")

    except ConfigValidationError as e:
        print(f"\n{e}\n")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
