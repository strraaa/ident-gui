"""
Фоновое выполнение длительных операций.

Обращения к порталу и к SQL Server занимают секунды и не должны подвешивать
интерфейс, поэтому выполняются в отдельном потоке.
"""

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


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

        def cleanup():
            if worker in self._workers:
                self._workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_error)
        worker.finished.connect(cleanup)
        worker.start()

        return worker

    def wait_all(self, timeout_ms: int = 3000):
        """Дожидается завершения потоков — вызывается при закрытии приложения"""
        for worker in list(self._workers):
            if worker.isRunning():
                worker.wait(timeout_ms)
