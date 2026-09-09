"""
Перехват необработанных ошибок.

Главное здесь — режим без диалогов. Модальное окно на сборочной машине
останавливает самопроверку навсегда: закрыть его некому, а прогон висит
до общего таймаута, ничего не сообщая о причине.
"""

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.services import errors


class ErrorHandlerTests(unittest.TestCase):
    """Счётчик ошибок и выключатель диалогов"""

    def setUp(self):
        self._excepthook = sys.excepthook
        self._thread_excepthook = threading.excepthook

        errors._count = 0
        errors._shown = 0
        errors._dialogs = True

    def tearDown(self):
        sys.excepthook = self._excepthook
        threading.excepthook = self._thread_excepthook

        errors._count = 0
        errors._shown = 0
        errors._dialogs = True

    @staticmethod
    def _failure():
        try:
            raise RuntimeError('поломка')
        except RuntimeError:
            return sys.exc_info()

    def test_dialog_is_skipped_when_disabled(self):
        """Самопроверка: ошибка в журнал и в stderr, окно не показывается"""
        errors.install(dialogs=False)

        with mock.patch.object(errors, '_show_dialog') as dialog:
            errors.handle_exception(*self._failure())

        dialog.assert_not_called()
        self.assertEqual(errors.count(), 1)

    def test_dialog_is_shown_by_default(self):
        errors.install()

        with mock.patch.object(errors, '_show_dialog') as dialog:
            errors.handle_exception(*self._failure())

        dialog.assert_called_once()
        self.assertEqual(errors.count(), 1)

    def test_interrupt_is_not_an_application_error(self):
        """Ctrl+C — не поломка сборки, и в счётчик он попадать не должен"""
        errors.install(dialogs=False)

        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            info = sys.exc_info()

        with mock.patch.object(sys, '__excepthook__'):
            errors.handle_exception(*info)

        self.assertEqual(errors.count(), 0)

    def test_background_error_is_counted(self):
        """Ошибку фонового потока самопроверка тоже обязана заметить"""
        errors.install(dialogs=False)

        exc_type, exc, tb = self._failure()
        args = mock.Mock(
            exc_type=exc_type, exc_value=exc, exc_traceback=tb,
            thread=mock.Mock(name='фон')
        )

        errors._handle_thread_exception(args)

        self.assertEqual(errors.count(), 1)


if __name__ == '__main__':
    unittest.main()
