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
        ('Notifications', 'smtp_password')
    ]

    # Обязательные поля для проверки
    REQUIRED_FIELDS = [
        ('Database', 'server', 'Адрес сервера БД'),
        ('Database', 'database', 'Имя базы данных'),
        ('Database', 'username', 'Имя пользователя БД'),
        ('Database', 'password', 'Пароль БД'),
        ('Bitrix24', 'webhook_url', 'URL webhook Битрикс24'),
        ('Bitrix24', 'token', 'Токен webhook'),
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

        except ValueError as e:
            errors.append(f"Ошибка типа данных в конфигурации: {e}")

        # 3. Валидация URL webhook
        if self.config.has_option('Bitrix24', 'webhook_url'):
            webhook_url = self.config.get('Bitrix24', 'webhook_url')
            if not webhook_url.startswith(('http://', 'https://')):
                errors.append(f"Некорректный webhook_url: должен начинаться с http:// или https://")

        # 4. Проверка DPAPI
        if not DPAPI_AVAILABLE:
            errors.append(
                "Модуль win32crypt не установлен - шифрование недоступно!\n"
                "Установите: pip install pywin32"
            )

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
        assigned_by_id = self.config.get('Bitrix24', 'default_assigned_by_id', fallback='').strip()

        return {
            'webhook_url': self.config.get('Bitrix24', 'webhook_url'),
            'token': self._get_decrypted('Bitrix24', 'token'),  # Расшифровиваем
            'request_timeout': self.config.getint('Bitrix24', 'request_timeout', fallback=30),
            'max_retries': self.config.getint('Bitrix24', 'max_retries', fallback=3),
            'default_assigned_by_id': int(assigned_by_id) if assigned_by_id else None,
        }

    def get_bitrix_field_map(self) -> Dict[str, str]:
        """Возвращает маппинг логических имён полей → фактические UF_CRM имена"""
        # Безопасные значения по-умолчанию (совпадают с текущим кодом)
        return {
            'ident_field': self.config.get('Bitrix24', 'ident_field', fallback='UF_CRM_1769072841035'),
            'contact_card_number': self.config.get('Bitrix24', 'contact_card_number', fallback='UF_CRM_1769083788971'),
            'contact_parent': self.config.get('Bitrix24', 'contact_parent', fallback='UF_CRM_1769087537061'),
            'deal_start': self.config.get('Bitrix24', 'deal_start_field', fallback='UF_CRM_1769008900'),
            'deal_end': self.config.get('Bitrix24', 'deal_end_field', fallback='UF_CRM_1769008947'),
            'deal_doctor': self.config.get('Bitrix24', 'deal_doctor_field', fallback='UF_CRM_1769008996'),
            'deal_services': self.config.get('Bitrix24', 'deal_services_field', fallback='UF_CRM_1769009098'),
            'deal_status': self.config.get('Bitrix24', 'deal_status_field', fallback='UF_CRM_1769009157'),
            'deal_card_number': self.config.get('Bitrix24', 'deal_card_number_field', fallback='UF_CRM_1769083581481'),
            'deal_parent': self.config.get('Bitrix24', 'deal_parent_field', fallback='UF_CRM_1769087458477'),
            'deal_comment': self.config.get('Bitrix24', 'deal_comment_field', fallback='UF_CRM_1769494714842'),
            'treatment_plan': self.config.get('Bitrix24', 'treatment_plan_field', fallback='UF_CRM_1769167266723'),
            'treatment_plan_hash': self.config.get('Bitrix24', 'treatment_plan_hash_field', fallback='UF_CRM_1769167398642'),
            'filial': self.config.get('Bitrix24', 'filial_field', fallback='UF_CRM_FILIAL'),
            'armchair': self.config.get('Bitrix24', 'armchair_field', fallback='UF_CRM_ARMCHAIR'),
            'status_field': self.config.get('Bitrix24', 'status_field', fallback='UF_CRM_STATUS'),
        }

    def get_stage_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию стадий: маппинг, финальные и защищённые"""
        # Считываем JSON-подобные строки из конфига или используем простые CSV значения
        def _get_map(section, option, fallback):
            s = self.config.get(section, option, fallback=fallback)
            # Формат: Статус1:WON,Статус2:NEW
            items = [i.strip() for i in s.split(',') if i.strip()]
            res = {}
            for it in items:
                if ':' in it:
                    k, v = it.split(':', 1)
                    res[k.strip()] = v.strip()
            return res

        mapping = _get_map('Bitrix24', 'stage_mapping', "Запланирован:NEW,Пациент пришел:NEW,В процессе:UC_NO40X0,Завершен:UC_NO40X0,Завершен (счет выдан):WON,Отменен:LOSE")

        finals = [s.strip() for s in self.config.get('Bitrix24', 'final_stages', fallback='WON,LOSE').split(',') if s.strip()]
        protected = [s.strip() for s in self.config.get('Bitrix24', 'protected_stages', fallback='PREPAYMENT_INVOICE,FINAL_INVOICE,EXECUTING,APOLOGY').split(',') if s.strip()]

        return {
            'mapping': mapping,
            'finals': finals,
            'protected': protected,
        }

    def get_sync_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию синхронизации"""
        return {
            'filial_id': self.config.getint('Sync', 'filial_id'),
            'interval_minutes': self.config.getint('Sync', 'interval_minutes', fallback=2),
            'batch_size': self.config.getint('Sync', 'batch_size', fallback=50),
            'initial_days': self.config.getint('Sync', 'initial_days', fallback=7),
            'enable_update_existing': self.config.getboolean('Sync', 'enable_update_existing', fallback=True),
        }

    def get_logging_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию логирования"""
        return {
            'level': self.config.get('Logging', 'level', fallback='INFO'),
            'log_dir': self.config.get('Logging', 'log_dir', fallback='logs'),
            'rotation_days': self.config.getint('Logging', 'rotation_days', fallback=30),
            'mask_personal_data': self.config.getboolean('Logging', 'mask_personal_data', fallback=True),
        }

    def get_queue_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию очереди"""
        return {
            'enabled': self.config.getboolean('Queue', 'enabled', fallback=True),
            'max_size': self.config.getint('Queue', 'max_size', fallback=1000),
            'persistence_file': self.config.get('Queue', 'persistence_file', fallback='queue.json'),
            'retry_interval_minutes': self.config.getint('Queue', 'retry_interval_minutes', fallback=5),
            'max_retry_attempts': self.config.getint('Queue', 'max_retry_attempts', fallback=3),
        }

    def get_monitoring_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию мониторинга"""
        return {
            'enable_web_interface': self.config.getboolean('Monitoring', 'enable_web_interface', fallback=True),
            'web_port': self.config.getint('Monitoring', 'web_port', fallback=8080),
            'enable_metrics': self.config.getboolean('Monitoring', 'enable_metrics', fallback=True),
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
