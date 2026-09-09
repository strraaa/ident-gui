"""
Учёт фоновых потоков.

Работающий QThread, доживший до выхода из процесса, завершает его аварийно:
прогон отчитывается об успехе, а код возврата приходит ненулевым. Поэтому
запущенный поток обязан числиться в реестре, пока не сообщит о завершении.

Пропускается, если PySide6 не установлен.
"""

import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, 'PySide6 не установлен')
class WorkerRegistryTests(unittest.TestCase):
    """Реестр потоков, который дожидается их при выходе"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.services import workers

        self.workers = workers
        self.runner = workers.WorkerRunner()

    def _drain(self, worker, timeout_s: float = 5.0):
        """Дожидается потока и разбирает накопившиеся события"""
        worker.wait(int(timeout_s * 1000))

        deadline = time.monotonic() + timeout_s
        while worker in self.workers._live and time.monotonic() < deadline:
            self.app.processEvents()

    def test_running_worker_is_registered(self):
        worker = self.runner.run(lambda: time.sleep(0.2), lambda _: None, lambda _: None)

        self.assertIn(worker, self.workers._live)

        self._drain(worker)
        self.assertNotIn(worker, self.workers._live)

    def test_failed_worker_leaves_the_registry(self):
        """Упавшая задача тоже обязана убрать за собой"""
        def boom():
            raise RuntimeError('поломка')

        worker = self.runner.run(boom, lambda _: None, lambda _: None)

        self._drain(worker)
        self.assertNotIn(worker, self.workers._live)

    def test_shutdown_waits_for_unfinished_work(self):
        """Выход из процесса не бросает поток, который ещё работает"""
        worker = self.runner.run(lambda: time.sleep(0.5), lambda _: None, lambda _: None)

        self.assertTrue(worker.isRunning())
        self.workers._wait_live()

        self.assertFalse(worker.isRunning())

        self._drain(worker)


if __name__ == '__main__':
    unittest.main()
