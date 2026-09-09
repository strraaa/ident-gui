# -*- mode: python ; coding: utf-8 -*-
"""
Описание сборки приложения настроек.

Раньше сборка задавалась ключами в build_gui_exe.ps1, и вычистить лишнее
можно было только через --exclude-module. Этого мало: PyInstaller кладёт
рядом с приложением библиотеки Qt целыми каталогами, и модульные исключения
на них не действуют. Здесь список файлов фильтруется после разбора.

Что выбрасывается и почему — в DROP_PATTERNS ниже. Приложению нужны ровно
три модуля Qt (QtCore, QtGui, QtWidgets), всё остальное в дистрибутиве
оказывается попутно.

Проверка результата — `ident_settings.exe --selftest`: собранное приложение
строит окно и выходит с кодом 0. Если чистка зашла слишком далеко и Qt не
находит плагин платформы или стиля, это видно на сборочной машине.
"""

import re
import sys
from pathlib import Path

# Сборка идёт из-под CI, где вывод уходит в конвейер: Python берёт кодировку
# системы (на раннере GitHub — cp1252), и печать строки с кириллицей роняет
# сборку через UnicodeEncodeError. Переводим потоки спеки на UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError, ValueError):
        pass

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))

from gui import __version__  # noqa: E402

ICON = ROOT / 'gui' / 'assets' / 'app.ico'

# ----------------------------------------------------------------------
# Модули, которые в приложение не входят
# ----------------------------------------------------------------------

EXCLUDED_MODULES = [
    # Дополнения Qt: приложение — обычные виджеты, ничего из этого не импортирует
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender',
    'PySide6.QtBluetooth', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
    'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets', 'PySide6.QtNfc', 'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtPositioning', 'PySide6.QtQml', 'PySide6.QtQuick',
    'PySide6.QtQuick3D', 'PySide6.QtQuickControls2', 'PySide6.QtQuickWidgets',
    'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtSensors',
    'PySide6.QtSerialBus', 'PySide6.QtSerialPort', 'PySide6.QtSpatialAudio',
    'PySide6.QtSql', 'PySide6.QtStateMachine', 'PySide6.QtSvgWidgets',
    'PySide6.QtTest', 'PySide6.QtTextToSpeech', 'PySide6.QtUiTools',
    'PySide6.QtWebChannel', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets', 'PySide6.QtWebSockets', 'PySide6.QtXml',

    # Есть в окружении сборки (ставится requirements.lock службы),
    # но приложению не нужно
    'flask', 'jinja2', 'werkzeug', 'click', 'itsdangerous',
    'pytest', 'sqlitedict', 'schedule', 'pythonjsonlogger',
    'PyInstaller',

    # Стандартная библиотека, которую тянет за собой всё подряд
    'tkinter', 'test', 'unittest', 'pydoc_data', 'lib2to3',
]

# ----------------------------------------------------------------------
# Файлы, которые PyInstaller кладёт сам, а приложению они не нужны
# ----------------------------------------------------------------------
#
# Каждая строка — регулярное выражение по пути внутри дистрибутива,
# сравнение без учёта регистра.

DROP_PATTERNS = [
    # QML и Qt Quick: интерфейс собран на виджетах, декларативного слоя в нём нет
    r'[/\\]qml[/\\]',
    r'Qt6Qml.*\.dll$',
    r'Qt6Quick.*\.dll$',
    r'Qt6LabsSettings.*\.dll$',
    r'qmltooling[/\\]',

    # Инструменты разработчика, попадающие из каталога PySide6
    r'(qmlls|qmlformat|qmlimportscanner|qmllint|qmltyperegistrar|qmlscene)\.exe$',
    r'(assistant|designer|linguist|lrelease|lupdate|qtdiag|qtpaths|pyside6-\w+)\.exe$',
    r'Qt6Designer.*\.dll$',
    r'Qt6Help\.dll$',

    # Программная реализация OpenGL (19,7 МБ) и ANGLE: нужны Qt Quick
    # и виджетам с OpenGL-холстом, которых здесь нет
    r'opengl32sw\.dll$',
    r'd3dcompiler_\d+\.dll$',
    r'libEGL\.dll$',
    r'libGLESv2\.dll$',

    # Ресурсы движка веб-страниц: сам движок исключён, данные остаются
    r'resources[/\\]icudtl\.dat$',
    r'resources[/\\]qtwebengine.*',

    # Драйверы баз данных Qt: к SQL Server приложение ходит через pyodbc
    r'plugins[/\\]sqldrivers[/\\]',

    # Экранная клавиатура и способы ввода — на сервере не используются
    r'plugins[/\\]platforminputcontexts[/\\]',
    r'plugins[/\\]virtualkeyboard[/\\]',

    # Мультимедиа и 3D, если всё же просочились
    r'Qt6(Multimedia|Spatial|3D|Charts|DataVisualization|Pdf|WebEngine|Bluetooth|Nfc)\w*\.dll$',
]

# Переводы Qt: оставляем только русский. Приложение показывает стандартные
# диалоги (QMessageBox, QFileDialog), и без qtbase_ru кнопки в них английские.
KEEP_TRANSLATIONS = re.compile(r'qtbase_ru\.qm$', re.IGNORECASE)
DROP_TRANSLATIONS = re.compile(r'translations[/\\].*\.qm$', re.IGNORECASE)

_dropped = []


def keep(entry) -> bool:
    """Оставить ли файл в дистрибутиве"""
    name = entry[0]

    if DROP_TRANSLATIONS.search(name) and not KEEP_TRANSLATIONS.search(name):
        _dropped.append(name)
        return False

    for pattern in DROP_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            _dropped.append(name)
            return False

    return True


# ----------------------------------------------------------------------
# Сведения о файле для проводника Windows
# ----------------------------------------------------------------------


def write_version_resource() -> Path:
    """
    Готовит ресурс версии.

    Без него в свойствах файла пусто: ни названия, ни версии, ни
    правообладателя. Кроме приличий это влияет и на репутацию у антивирусов —
    безымянный exe подозрительнее подписанного и описанного.
    """
    numbers = tuple(int(part) for part in __version__.split('.')[:3]) + (0,)

    target = ROOT / 'build' / 'version_info.txt'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('041904b0', [
        StringStruct('CompanyName', 'GitPro'),
        StringStruct('FileDescription', 'Настройки интеграции Ident → Битрикс24'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', 'ident_settings'),
        StringStruct('OriginalFilename', 'ident_settings.exe'),
        StringStruct('ProductName', 'Настройки Ident → Битрикс24'),
        StringStruct('ProductVersion', '{__version__}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
""", encoding='utf-8')

    return target


VERSION_RESOURCE = write_version_resource()

# ----------------------------------------------------------------------

a = Analysis(
    ['gui_main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Иконка окна и образец конфигурации: по нему приложение создаёт
        # config.ini, если в папке службы его ещё нет
        (str(ICON), 'gui/assets'),
        (str(ROOT / 'config.example.ini'), '.'),
    ],
    hiddenimports=['requests'],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=0,
)

a.binaries = [entry for entry in a.binaries if keep(entry)]
a.datas = [entry for entry in a.datas if keep(entry)]

print(f'[spec] исключено файлов: {len(_dropped)}')
for name in sorted(_dropped)[:40]:
    print(f'[spec]   {name}')
if len(_dropped) > 40:
    print(f'[spec]   … и ещё {len(_dropped) - 40}')

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ident_settings',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX уменьшает файл, но резко повышает ложные
                        # срабатывания антивирусов — цена не стоит выигрыша
    console=False,
    icon=str(ICON),
    version=str(VERSION_RESOURCE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='ident_settings',
)
