"""
Точка входа приложения настроек.

Запуск:
    python gui_main.py [--workdir "C:\\Program Files\\IdentBitrix24"] [--theme dark]

Порядок в `main` важен и не случаен:

1. журнал заводится первым — иначе о падении на старте узнать неоткуда,
   а модули службы при импорте заведут свой файл в папке службы;
2. перехват ошибок ставится до создания окна;
3. блокировка второго экземпляра проверяется до чтения конфигурации:
   две копии, правящие один config.ini, затрут правки друг друга.
"""

import argparse
import faulthandler
import sys
import tempfile
from pathlib import Path

# Импорты модулей проекта работают и при запуске из исходников, и из собранного exe
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.services import logging_setup  # noqa: E402
from gui.services.paths import Workspace, detect_workdir, resource_path  # noqa: E402

APP_NAME = 'Настройки Ident → Битрикс24'

#: имя файла блокировки — по нему второй экземпляр узнаёт о первом
LOCK_NAME = 'ident-settings.lock'

#: через сколько считать блокировку брошенной, мс
STALE_LOCK_MS = 30_000

#: сколько ждать самопроверку, с — дольше окно не строится ни на одной машине
SELFTEST_TIMEOUT_S = 120


def parse_args(argv):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        '--workdir',
        help='Папка установки службы (где лежат config.ini, queue.json и logs)'
    )
    parser.add_argument(
        '--theme',
        choices=('system', 'light', 'dark'),
        help='Оформление: system (как в системе), light или dark. '
             'Без ключа берётся то, что выбрано в прошлый раз.'
    )
    parser.add_argument(
        '--selftest',
        action='store_true',
        help='Собрать окно и сразу выйти. Код возврата 0 означает, '
             'что сборка работоспособна: библиотеки Qt и плагины на месте.'
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    log_file = logging_setup.configure()
    log = logging_setup.logger()
    log.info('Запуск. %s', logging_setup.describe_environment())
    if log_file:
        log.info('Журнал приложения: %s', log_file)

    # Импорт Qt и модулей службы — только после настройки журнала
    from PySide6.QtCore import QLibraryInfo, QLockFile, QTranslator
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from gui.services import errors
    from gui.services.app_settings import AppSettings
    from gui.theme import apply_theme

    # В самопроверке окна показывать некому: модальное сообщение об ошибке
    # остановит сборочную машину до общего таймаута прогона
    errors.install(dialogs=not args.selftest)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName('IDENT Integration')
    app.setWindowIcon(QIcon(str(resource_path('gui', 'assets', 'app.ico'))))
    _install_translation(app, QTranslator, QLibraryInfo, log)

    if args.selftest:
        return _selftest(app, apply_theme, log)

    lock = QLockFile(str(Path(tempfile.gettempdir()) / LOCK_NAME))
    lock.setStaleLockTime(STALE_LOCK_MS)

    if not lock.tryLock(0):
        log.warning('Второй экземпляр приложения — запуск отменён')
        QMessageBox.information(
            None, APP_NAME,
            'Приложение настроек уже запущено.\n\n'
            'Две копии, открывшие один и тот же config.ini, затрут правки '
            'друг друга, поэтому вторая не открывается. Найдите первую '
            'в панели задач.'
        )
        return 0

    settings = AppSettings()

    # Ключ командной строки перекрывает запомненный выбор и запоминается сам
    if args.theme:
        settings.set_theme(args.theme)
    apply_theme(app, settings.theme())

    # Папка службы: ключ командной строки, затем прошлый выбор, затем поиск.
    # Запомненную папку могли переименовать или удалить — тогда ищем заново.
    remembered = settings.workdir()
    if remembered and not Path(remembered).exists():
        log.info('Запомненная папка службы недоступна: %s', remembered)
        remembered = None

    workdir = detect_workdir(args.workdir or remembered)
    log.info('Папка службы: %s', workdir)

    from gui.main_window import MainWindow

    window = MainWindow(Workspace(workdir), settings)
    window.show()

    code = app.exec()
    log.info('Завершение работы, код %s', code)

    lock.unlock()
    return code


def _install_translation(app, QTranslator, QLibraryInfo, log) -> None:
    """
    Русский язык стандартных диалогов Qt.

    Свои надписи в приложении русские, а кнопки QMessageBox и QFileDialog
    берутся из Qt и без перевода остаются английскими: «Save», «Cancel»,
    «Yes», «No» в окне с русским текстом.

    Переводчик привязан к приложению, иначе сборщик мусора уберёт его сразу
    после выхода из функции, и перевод отвалится.
    """
    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)

    if translator.load('qtbase_ru', path):
        app.installTranslator(translator)
    else:
        log.warning('Перевод Qt не найден в %s — диалоги будут английскими', path)


def _selftest(app, apply_theme, log) -> int:
    """
    Проверка работоспособности сборки.

    Собирает окно на пустой папке и закрывает его. Смысл — в упаковке: после
    вычищения лишних библиотек Qt приложение может перестать находить плагин
    платформы или стиля, и узнать об этом лучше на сборочной машине, чем
    у заказчика. Запускается с `QT_QPA_PLATFORM=offscreen`.

    Проверка идёт под сторожевым таймером: всё, что ждёт ответа (окно
    с вопросом, обращение к планировщику, сеть), на сборочной машине ждало
    бы вечно. По истечении срока процесс печатает стек всех потоков —
    по нему видно, где именно встали — и завершается с ненулевым кодом.
    """
    from PySide6.QtCore import QSettings

    from gui.main_window import MainWindow
    from gui.services import errors
    from gui.services.app_settings import AppSettings

    log.info('Самопроверка сборки')
    faulthandler.dump_traceback_later(SELFTEST_TIMEOUT_S, exit=True)

    with tempfile.TemporaryDirectory() as tmp:
        apply_theme(app, 'light')

        settings = AppSettings(QSettings(str(Path(tmp) / 'selftest.ini'), QSettings.IniFormat))
        window = MainWindow(Workspace(Path(tmp)), settings)
        window.show()
        app.processEvents()

        pages = len(window.pages)
        window.close()

    faulthandler.cancel_dump_traceback_later()

    # Перехваченная ошибка не роняет приложение — окно строится дальше, и без
    # этой проверки самопроверка отчиталась бы об успехе на сломанной сборке
    failures = errors.count()
    if failures:
        log.error('Самопроверка не пройдена, ошибок: %s', failures)
        print(f'Самопроверка не пройдена: ошибок {failures}')
        return 1

    log.info('Самопроверка пройдена, страниц собрано: %s', pages)
    print(f'Самопроверка пройдена: страниц собрано {pages}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
