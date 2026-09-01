"""
Запуск приложения настроек интеграции Ident → Битрикс24.

Отдельная точка входа нужна PyInstaller: служба синхронизации собирается из
main.py, а настройки — из этого файла (см. build_gui_exe.ps1).
"""

import sys

from gui.app import main

if __name__ == '__main__':
    sys.exit(main())
