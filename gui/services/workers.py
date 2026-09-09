"""
Фоновое выполнение длительных операций.

Обращения к порталу и к SQL Server занимают секунды и не должны подвешивать
интерфейс, поэтому выполняются в отдельном потоке.
"""

import atexit
import time
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

#: общий срок ожидания фоновых потоков при выходе из процесса, мс
#:
#: Считается на все потоки сразу, а не на каждый: обращение к планировщику
#: ограничено своим таймаутом, и складывать эти таймауты на выходе незачем.
SHUTDOWN_TIMEOUT_MS = 35_000

#: все запущенные потоки, ещё не сообщившие о завершении
#:
#: Ссылка держится до конца работы процесса: без неё объект будет собран
#: сборщиком мусора, а деструктор работающего QThread вызывает abort().
_live = []


def _wait_live() -> None:
    """
    Дожидается фоновых потоков перед выходом из процесса.

    Разрушение работающего QThread завершает процесс аварийно. Внешне это
    выглядит обманчиво: тесты отчитываются об успехе, а код возврата
    приходит ненулевым, и прогон CI падает без единого сообщения.
    """
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_MS / 1000

    for worker in list(_live):
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            break

        worker.wait(left_ms)


atexit.register(_wait_live)


class Worker(QThread):
    """Выполняет функцию в фоне и отдаёт результат сигналом"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)
            return

        self.succeeded.emit(result)


class WorkerRunner:
    """
    Держит ссылки на активные потоки.

    Без этого QThread может быть собран сборщиком мусора до завершения работы,
    что роняет приложение.
    """

    def __init__(self):
        self._workers = []

    def run(self, fn: Callable[..., Any], on_success: Callable[[Any], None],
            on_error: Callable[[str], None], *args, **kwargs) -> Worker:
        worker = Worker(fn, *args, **kwargs)
        self._workers.append(worker)
        _live.append(worker)

        def cleanup():
            if worker in self._workers:
                self._workers.remove(worker)
            if worker in _live:
                _live.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_error)
        worker.finished.connect(cleanup)

        # Удаление объекта потока поручается Qt: он сделает это в цикле
        # событий после того, как поток действительно завершился. Сборщик
        # мусора Python такой гарантии не даёт, а разрушение работающего
        # QThread завершает процесс аварийно.
        worker.finished.connect(worker.deleteLater)

        worker.start()

        return worker

    def wait_all(self, timeout_ms: int = 3000):
        """
        Дожидается завершения потоков — вызывается при закрытии приложения.

        Не дождавшиеся откладываются в сторону, а не бросаются: у обращения
        к SQL Server таймаут может быть и десять минут, столько держать
        закрывающееся окно нельзя, но и разрушать работающий поток тоже.
        Их дождётся `_wait_live` при выходе из процесса.
        """
        for worker in list(self._workers):
            if not worker.isRunning():
                continue

            worker.wait(timeout_ms)

            if worker.isRunning() and worker in self._workers:
                self._workers.remove(worker)
