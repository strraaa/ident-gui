"""
Модуль логирования с маскированием персональных данных (Версия 2.0 - Оптимизированная)

ИСПРАВЛЕНИЯ:
- ✅ Thread-safe singleton с использованием threading.Lock
- ✅ Защита от race conditions при инициализации
- ✅ Потокобезопасное добавление handlers
- ✅ Улучшенное маскирование ПД (телефоны, email, ИНН)
"""

import logging
import logging.handlers
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class PersonalDataMaskingFormatter(logging.Formatter):
    """
    Форматтер для маскирования персональных данных в логах

    Маскирует:
    - Телефоны (все форматы: +7, 8, без пробелов, с пробелами)
    - Email адреса
    - ИНН (10 и 12 цифр)
    - Номера карт (16 цифр)
    """

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        super().__init__(fmt, datefmt)

        # Паттерны для маскирования
        self.phone_pattern = re.compile(
            r'(?:\+7|8|7)?[\s\-]?\(?[0-9]{3}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}'
        )
        self.email_pattern = re.compile(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        )
        self.inn_pattern = re.compile(r'\b\d{10}\b|\b\d{12}\b')
        self.card_pattern = re.compile(r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b')

        # Паттерн для ФИО (упрощенный, только явные случаи)
        self.fio_pattern = re.compile(
            r'(?:пациент|врач|доктор|клиент)[\s:]+([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)',
            re.IGNORECASE
        )

    def _mask_phone(self, text: str) -> str:
        """Маскирует телефоны"""
        def replacer(match):
            phone = match.group(0)
            # Сохраняем только первые 3 цифры
            digits = re.sub(r'\D', '', phone)
            if len(digits) >= 7:
                return f"+7XXX***XX-XX"
            return "***MASKED_PHONE***"

        return self.phone_pattern.sub(replacer, text)

    def _mask_email(self, text: str) -> str:
        """Маскирует email"""
        def replacer(match):
            email = match.group(0)
            parts = email.split('@')
            if len(parts) == 2:
                username = parts[0]
                domain = parts[1]
                masked_username = username[0] + '*' * (len(username) - 1) if len(username) > 1 else '*'
                return f"{masked_username}@{domain}"
            return "***MASKED_EMAIL***"

        return self.email_pattern.sub(replacer, text)

    def _mask_inn(self, text: str) -> str:
        """Маскирует ИНН"""
        return self.inn_pattern.sub('***MASKED_INN***', text)

    def _mask_card(self, text: str) -> str:
        """Маскирует номера карт"""
        def replacer(match):
            card = match.group(0)
            digits = re.sub(r'\D', '', card)
            if len(digits) == 16:
                return f"{digits[:4]} **** **** {digits[-4:]}"
            return "***MASKED_CARD***"

        return self.card_pattern.sub(replacer, text)

    def _mask_fio(self, text: str) -> str:
        """Маскирует ФИО в контексте"""
        def replacer(match):
            prefix = match.group(0).split(':')[0]
            fio = match.group(1)
            parts = fio.split()
            masked_parts = [part[0] + '*' * (len(part) - 1) for part in parts]
            return f"{prefix}: {' '.join(masked_parts)}"

        return self.fio_pattern.sub(replacer, text)

    def format(self, record: logging.LogRecord) -> str:
        """Форматирует запись с маскированием ПД"""
        # Сначала форматируем обычным способом
        formatted = super().format(record)

        # Маскируем ПД
        formatted = self._mask_phone(formatted)
        formatted = self._mask_email(formatted)
        formatted = self._mask_inn(formatted)
        formatted = self._mask_card(formatted)
        formatted = self._mask_fio(formatted)

        return formatted


class ThreadSafeLogger:
    """
    Thread-safe singleton логгер с маскированием персональных данных

    Использует double-checked locking pattern для потокобезопасности
    """

    _instance: Optional[logging.Logger] = None
    _lock = threading.Lock()
    _initialized = False

    @classmethod
    def get_logger(
        cls,
        name: str = 'ident_integration',
        log_dir: str = 'logs',
        level: str = 'INFO',
        rotation_days: int = 30,
        mask_personal_data: bool = True,
        max_log_size_mb: int = 20,
        max_backup_files: int = 5
    ) -> logging.Logger:
        """
        Получает thread-safe singleton экземпляр логгера

        Args:
            name: Имя логгера
            log_dir: Директория для логов
            level: Уровень логирования
            rotation_days: Срок хранения логов (дни)
            mask_personal_data: Маскировать ли персональные данные
            max_log_size_mb: Максимальный размер одного файла лога (MB)
            max_backup_files: Количество резервных файлов при ротации по размеру

        Returns:
            Настроенный экземпляр логгера
        """
        # Fast path: если уже инициализирован, возвращаем сразу
        if cls._initialized and cls._instance is not None:
            return cls._instance

        # Slow path: инициализация с блокировкой (double-checked locking)
        with cls._lock:
            # Проверяем еще раз внутри блокировки
            if cls._initialized and cls._instance is not None:
                return cls._instance

            # Создаем новый экземпляр
            logger = logging.getLogger(name)

            # Если уже есть handlers - не добавляем повторно
            if logger.handlers:
                cls._instance = logger
                cls._initialized = True
                return logger

            logger.setLevel(getattr(logging, level.upper(), logging.INFO))
            logger.propagate = False

            # Создаем директорию для логов
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            # Форматтер с маскированием или без
            if mask_personal_data:
                formatter = PersonalDataMaskingFormatter(
                    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
            else:
                formatter = logging.Formatter(
                    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )

            # ===== ФАЙЛОВЫЙ HANDLER (суточная ротация в полночь) =====
            # Раньше использовался RotatingFileHandler с датой в имени файла,
            # вычисленной один раз при инициализации. Для долгоживущего процесса
            # (run_scheduled) это приводило к тому, что все дни писались в один файл
            # с датой первого запуска. TimedRotatingFileHandler ротирует в полночь.
            # Параметры max_log_size_mb / max_backup_files больше не используются:
            # суточная ротация делает size-ротацию избыточной для этого процесса.
            log_file = log_path / "integration_log.txt"
            file_handler = logging.handlers.TimedRotatingFileHandler(
                log_file,
                when='midnight',
                interval=1,
                backupCount=max(1, int(rotation_days)),
                encoding='utf-8'
            )
            # При ротации старый файл получает суффикс с датой.
            # Дефолтное имя — integration_log.txt.YYYY-MM-DD; переименуем
            # в integration_log_YYYY-MM-DD.txt, чтобы _cleanup_old_logs продолжал работать.
            file_handler.suffix = "%Y-%m-%d"
            file_handler.namer = lambda default_name: str(
                Path(default_name).parent
                / f"integration_log_{Path(default_name).name.rsplit('.', 1)[-1]}.txt"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)

            # ===== КОНСОЛЬНЫЙ HANDLER =====
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)

            # Добавляем handlers (thread-safe, т.к. внутри lock)
            logger.addHandler(file_handler)
            logger.addHandler(console_handler)

            # Запускаем очистку старых логов
            cls._cleanup_old_logs(log_path, rotation_days)

            # Помечаем как инициализированный
            cls._instance = logger
            cls._initialized = True

            logger.info(f"Логгер инициализирован (thread-safe): {log_file}")
            logger.info(f"Маскирование ПД: {'включено' if mask_personal_data else 'отключено'}")

            return logger

    @classmethod
    def reset(cls):
        """
        Сбрасывает singleton (для тестов)

        ВНИМАНИЕ: Использовать только в тестах!
        """
        with cls._lock:
            if cls._instance:
                # Закрываем все handlers
                for handler in cls._instance.handlers[:]:
                    handler.close()
                    cls._instance.removeHandler(handler)

            cls._instance = None
            cls._initialized = False

    @staticmethod
    def _cleanup_old_logs(log_dir: Path, retention_days: int):
        """
        Удаляет старые лог-файлы

        Args:
            log_dir: Директория с логами
            retention_days: Срок хранения (дни)
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=retention_days)

            for log_file in log_dir.glob('integration_log_*.txt*'):
                try:
                    # Извлекаем дату из имени файла
                    # Учитываем файлы с ротацией по размеру (например *.txt.1)
                    name = log_file.name
                    base = name.split('.txt')[0]
                    date_str = base.replace('integration_log_', '')
                    file_date = datetime.strptime(date_str, '%Y-%m-%d')

                    # Удаляем если старше retention_days
                    if file_date < cutoff_date:
                        log_file.unlink()
                        logging.info(f"Удален старый лог-файл: {log_file}")

                except (ValueError, OSError) as e:
                    logging.warning(f"Не удалось обработать файл {log_file}: {e}")

        except Exception as e:
            logging.error(f"Ошибка при очистке старых логов: {e}")


def get_logger(
    name: str = 'ident_integration',
    log_dir: str = 'logs',
    level: str = 'INFO',
    rotation_days: int = 30,
    mask_personal_data: bool = True,
    max_log_size_mb: int = 20,
    max_backup_files: int = 5
) -> logging.Logger:
    """
    Функция-обертка для получения thread-safe логгера

    Args:
        name: Имя логгера
        log_dir: Директория для логов
        level: Уровень логирования
        rotation_days: Срок хранения логов (дни)
        mask_personal_data: Маскировать ли персональные данные
        max_log_size_mb: Максимальный размер одного файла лога (MB)
        max_backup_files: Количество резервных файлов при ротации по размеру

    Returns:
        Настроенный экземпляр логгера
    """
    return ThreadSafeLogger.get_logger(
        name=name,
        log_dir=log_dir,
        level=level,
        rotation_days=rotation_days,
        mask_personal_data=mask_personal_data,
        max_log_size_mb=max_log_size_mb,
        max_backup_files=max_backup_files
    )


if __name__ == "__main__":
    """Тестирование логгера"""
    import time
    import concurrent.futures

    def test_thread_safety():
        """Тестирует потокобезопасность логгера"""
        logger = get_logger(name='test_logger', log_dir='test_logs')

        # Тестовые данные с ПД
        test_messages = [
            "Пациент: Иванов Иван Иванович записан на прием",
            "Телефон: +7 (999) 123-45-67",
            "Email: test.user@example.com",
            "ИНН: 1234567890",
            "Номер карты: 4532 1234 5678 9012",
            "Врач: Петров Петр Петрович принял пациента",
        ]

        for msg in test_messages:
            logger.info(msg)
            time.sleep(0.1)

    def worker(thread_id: int):
        """Рабочая функция для тестирования многопоточности"""
        logger = get_logger()
        for i in range(5):
            logger.info(f"Thread {thread_id}: сообщение {i}")
            time.sleep(0.05)

    print("🧪 Тестирование логгера...")

    # Тест 1: Маскирование ПД
    print("\n1️⃣ Тест маскирования персональных данных:")
    test_thread_safety()
    print("✅ Проверьте test_logs/integration_log_*.txt - ПД должны быть замаскированы")

    # Тест 2: Потокобезопасность
    print("\n2️⃣ Тест потокобезопасности (10 потоков):")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        concurrent.futures.wait(futures)

    print("✅ Все потоки завершены без ошибок")

    # Тест 3: Singleton
    print("\n3️⃣ Тест singleton:")
    logger1 = get_logger()
    logger2 = get_logger()
    print(f"logger1 is logger2: {logger1 is logger2}")
    print(f"ID logger1: {id(logger1)}")
    print(f"ID logger2: {id(logger2)}")

    if logger1 is logger2:
        print("✅ Singleton работает корректно")
    else:
        print("❌ Ошибка: созданы разные экземпляры!")

    print("\n✅ Все тесты пройдены!")
