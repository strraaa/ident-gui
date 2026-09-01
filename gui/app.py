"""
Точка входа приложения настроек.

Запуск:
    python gui_main.py [--workdir "C:\\Program Files\\IdentBitrix24"]
"""

import argparse
import sys
from pathlib import Path

# Импорты модулей проекта работают и при запуске из исходников, и из собранного exe
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from gui.services.paths import Workspace, detect_workdir  # noqa: E402
from gui.theme import apply_theme  # noqa: E402

APP_NAME = 'Настройки Ident → Битрикс24'


def parse_args(argv):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        '--workdir',
        help='Папка установки службы (где лежат config.ini, queue.json и logs)'
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName('IDENT Integration')
    apply_theme(app)

    workspace = Workspace(detect_workdir(args.workdir))

    window = MainWindow(workspace)
    window.show()

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
