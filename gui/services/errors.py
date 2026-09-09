"""
Перехват необработанных ошибок.

Без перехвата ошибка в обработчике сигнала выглядит для оператора так:
кнопка нажата, ничего не произошло, объяснений нет. PySide6 такую ошибку
не считает фатальной — приложение продолжает работать, а трассировка уходит
в stderr, которого у сборки с `--noconsole` нет.

Поэтому здесь: записать в журнал полностью, показать человеку коротко и
сказать, где смотреть подробности.

Диалог показывается не больше трёх раз за запуск. Если ошибка приходит из
таймера обновления состояния, она повторяется каждые десять секунд, и окно
с сообщением превращается в ловушку, из которой не выбраться.

Там, где окно закрыть некому (самопроверка сборки на сборочной машине),
диалоги выключаются: модальное окно останавливает выполнение навсегда,
и прогон висит до общего таймаута, ничего не сообщая. Тогда трассировка
идёт в stderr, а число ошибок можно спросить через `count()`.
"""

import sys
import threading
import traceback

from .logging_setup import log_path, logger

#: сколько раз за запуск показывать окно с ошибкой
MAX_DIALOGS = 3

_shown = 0
_count = 0
_dialogs = True


def install(dialogs: bool = True) -> None:
    """
    Ставит перехватчики для главного потока и для фоновых.

    `dialogs=False` — режим без человека за экраном: ошибка пишется
    в журнал и в stderr, но окном никого не останавливает.
    """
    global _dialogs
    _dialogs = dialogs

    sys.excepthook = handle_exception
    threading.excepthook = _handle_thread_exception


def count() -> int:
    """Сколько необработанных ошибок перехвачено с начала запуска"""
    return _count


def handle_exception(exc_type, exc, tb) -> None:
    """Ошибка в главном потоке: в журнал и в окно"""
    global _count

    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return

    _count += 1
    logger('ошибки').critical('Необработанная ошибка', exc_info=(exc_type, exc, tb))

    if not _dialogs:
        traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
        return

    _show_dialog(exc_type, exc)


def _handle_thread_exception(args) -> None:
    """
    Ошибка в фоновом потоке: только в журнал.

    Показывать окно отсюда нельзя — обращаться к интерфейсу из чужого потока
    Qt не разрешает, и попытка кончится падением уже настоящим.
    """
    global _count

    if issubclass(args.exc_type, SystemExit):
        return

    _count += 1

    logger('ошибки').critical(
        'Необработанная ошибка в фоновом потоке %s',
        getattr(args.thread, 'name', '—'),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )


def _show_dialog(exc_type, exc) -> None:
    global _shown

    _shown += 1
    if _shown > MAX_DIALOGS:
        return

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:
            return

        where = f'\n\nПодробности записаны в журнал:\n{log_path()}' if log_path() else ''
        tail = (
            '\n\nЭто сообщение больше не повторится — остальные ошибки '
            'будут только в журнале.' if _shown == MAX_DIALOGS else ''
        )

        QMessageBox.critical(
            None,
            'Непредвиденная ошибка',
            f'{exc_type.__name__}: {exc}'
            f'{where}{tail}'
        )
    except Exception:
        # Показать не вышло — в журнале запись уже есть, этого достаточно
        pass
