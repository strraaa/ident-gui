"""
Работа с очередью повторных попыток (queue.json) из GUI.

Очередь читается и правится напрямую, а не через PersistentQueue: конструктор
PersistentQueue переводит все элементы PROCESSING в PENDING, считая их следами
падения процесса. Если служба в этот момент работает, такой сброс привёл бы
к повторной обработке записи, которую служба отправляет прямо сейчас.

Блокировка используется та же, что и у службы (файл <queue>.lock), поэтому
одновременная запись двух процессов невозможна.

ВАЖНО: служба держит очередь в памяти и при любом своём изменении перезаписывает
файл целиком. Правки из GUI, сделанные при работающей службе, могут быть затёрты
её следующей записью. Для массовых операций службу лучше остановить.
"""

import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

STATUS_PENDING = 'pending'
STATUS_PROCESSING = 'processing'
STATUS_FAILED = 'failed'
STATUS_COMPLETED = 'completed'

STATUS_TITLES = {
    STATUS_PENDING: 'Ожидает',
    STATUS_PROCESSING: 'В обработке',
    STATUS_FAILED: 'Ошибка',
    STATUS_COMPLETED: 'Отправлено',
}


@dataclass
class QueueRow:
    """Строка очереди в терминах интерфейса"""
    unique_id: str
    status: str
    retry_count: int
    created_at: str
    updated_at: str
    next_retry_at: Optional[str]
    last_error: Optional[str]
    patient: str
    raw: Dict[str, Any]

    @property
    def status_title(self) -> str:
        return STATUS_TITLES.get(self.status, self.status)

    def is_exhausted(self, max_retry_attempts: int) -> bool:
        """Исчерпаны ли попытки — такой элемент служба больше не возьмёт"""
        return self.retry_count >= max_retry_attempts


class QueueLockError(RuntimeError):
    """Не удалось получить блокировку файла очереди"""


class QueueService:
    """Чтение и правка queue.json"""

    def __init__(self, queue_path: Path):
        self.queue_path = Path(queue_path)

    @property
    def lock_path(self) -> Path:
        return Path(str(self.queue_path) + '.lock')

    def exists(self) -> bool:
        return self.queue_path.exists()

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    def read_rows(self) -> List[QueueRow]:
        """Читает очередь. Пустой список — если файла нет или он повреждён."""
        data = self._read_raw()
        rows = []

        for item in data.get('items', []):
            if not isinstance(item, dict) or 'unique_id' not in item:
                continue

            rows.append(QueueRow(
                unique_id=str(item.get('unique_id', '')),
                status=str(item.get('status', '')),
                retry_count=int(item.get('retry_count') or 0),
                created_at=str(item.get('created_at', '')),
                updated_at=str(item.get('updated_at', '')),
                next_retry_at=item.get('next_retry_at'),
                last_error=item.get('last_error'),
                patient=self._extract_patient(item),
                raw=item
            ))

        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    def statistics(self, rows: Optional[List[QueueRow]] = None,
                   max_retry_attempts: int = 3) -> Dict[str, int]:
        """Сводка по очереди в тех же терминах, что и у службы"""
        rows = self.read_rows() if rows is None else rows

        stats = {
            'total': len(rows),
            'pending': 0,
            'processing': 0,
            'failed': 0,
            'completed': 0,
            'exhausted': 0,
        }

        for row in rows:
            if row.status in stats:
                stats[row.status] += 1
            if row.status == STATUS_FAILED and row.is_exhausted(max_retry_attempts):
                stats['exhausted'] += 1

        return stats

    # ------------------------------------------------------------------
    # Изменение
    # ------------------------------------------------------------------

    def reset_items(self, unique_ids: Iterable[str]) -> int:
        """
        Возвращает элементы в очередь на немедленную обработку:
        статус PENDING, счётчик попыток обнулён.
        """
        target = set(unique_ids)
        if not target:
            return 0

        now = datetime.now().isoformat()

        def mutate(item: Dict[str, Any]) -> bool:
            if item.get('unique_id') not in target:
                return False
            item['status'] = STATUS_PENDING
            item['retry_count'] = 0
            item['last_error'] = None
            item['next_retry_at'] = now
            item['updated_at'] = now
            return True

        return self._mutate_items(mutate)

    def reset_all_unsent(self) -> int:
        """
        Сбрасывает всё, что ещё не доехало до Битрикса: и обычные ошибки,
        и элементы с исчерпанными попытками. Служба заберёт их в ближайшем цикле.
        """
        now = datetime.now().isoformat()

        def mutate(item: Dict[str, Any]) -> bool:
            if item.get('status') == STATUS_COMPLETED:
                return False
            item['status'] = STATUS_PENDING
            item['retry_count'] = 0
            item['last_error'] = None
            item['next_retry_at'] = now
            item['updated_at'] = now
            return True

        return self._mutate_items(mutate)

    def clear_completed(self) -> int:
        """Удаляет успешно отправленные элементы"""
        return self._filter_items(lambda item: item.get('status') != STATUS_COMPLETED)

    def remove_items(self, unique_ids: Iterable[str]) -> int:
        """Удаляет элементы из очереди безвозвратно"""
        target = set(unique_ids)
        if not target:
            return 0
        return self._filter_items(lambda item: item.get('unique_id') not in target)

    # ------------------------------------------------------------------
    # Экспорт
    # ------------------------------------------------------------------

    def export_csv(self, path: Path, rows: List[QueueRow]) -> int:
        """Выгружает очередь в CSV (разделитель ';' — для Excel с русской локалью)"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow([
                'IDENT ID', 'Пациент', 'Статус', 'Попыток',
                'Создан', 'Обновлён', 'Следующая попытка', 'Последняя ошибка'
            ])
            for row in rows:
                writer.writerow([
                    row.unique_id,
                    row.patient,
                    row.status_title,
                    row.retry_count,
                    self._format_dt(row.created_at),
                    self._format_dt(row.updated_at),
                    self._format_dt(row.next_retry_at or ''),
                    (row.last_error or '').replace('\n', ' ')[:500],
                ])

        return len(rows)

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------

    def _read_raw(self) -> Dict[str, Any]:
        if not self.queue_path.exists():
            return {'items': []}

        try:
            with open(self.queue_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {'items': []}
            return data
        except (json.JSONDecodeError, OSError):
            return {'items': []}

    def _mutate_items(self, mutate) -> int:
        """Применяет изменение к элементам под блокировкой. Возвращает число изменённых."""
        if not self._acquire_lock():
            raise QueueLockError(
                'Файл очереди занят службой синхронизации. Повторите попытку через несколько секунд.'
            )

        try:
            data = self._read_raw()
            items = data.get('items', [])

            changed = 0
            for item in items:
                if isinstance(item, dict) and mutate(item):
                    changed += 1

            if changed:
                data['items'] = items
                self._write_raw(data)

            return changed
        finally:
            self._release_lock()

    def _filter_items(self, keep) -> int:
        """Оставляет только подходящие элементы. Возвращает число удалённых."""
        if not self._acquire_lock():
            raise QueueLockError(
                'Файл очереди занят службой синхронизации. Повторите попытку через несколько секунд.'
            )

        try:
            data = self._read_raw()
            items = data.get('items', [])

            kept = [item for item in items if isinstance(item, dict) and keep(item)]
            removed = len(items) - len(kept)

            if removed:
                data['items'] = kept
                self._write_raw(data)

            return removed
        finally:
            self._release_lock()

    def _write_raw(self, data: Dict[str, Any]):
        """Атомарная запись — тем же способом, что и в службе"""
        data['saved_at'] = datetime.now().isoformat()
        data['total_items'] = len(data.get('items', []))

        temp_path = self.queue_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_path.replace(self.queue_path)

    def _acquire_lock(self, timeout_seconds: int = 10, stale_seconds: int = 120) -> bool:
        start = time.time()

        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(f"{os.getpid()}|{int(time.time())}")
                return True
            except FileExistsError:
                try:
                    if time.time() - self.lock_path.stat().st_mtime > stale_seconds:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass

                if time.time() - start > timeout_seconds:
                    return False
                time.sleep(0.1)
            except OSError:
                return False

    def _release_lock(self):
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _extract_patient(item: Dict[str, Any]) -> str:
        """Достаёт ФИО пациента из сохранённых данных записи"""
        data = item.get('data')
        if not isinstance(data, dict):
            return ''

        contact = data.get('contact')
        if not isinstance(contact, dict):
            return ''

        parts = [
            str(contact.get('last_name') or '').strip(),
            str(contact.get('name') or '').strip(),
            str(contact.get('second_name') or '').strip(),
        ]
        return ' '.join(part for part in parts if part)

    @staticmethod
    def _format_dt(value: str) -> str:
        """ISO-время → привычный вид для таблицы и выгрузки"""
        if not value:
            return ''
        try:
            return datetime.fromisoformat(value).strftime('%d.%m.%Y %H:%M:%S')
        except (TypeError, ValueError):
            return str(value)
