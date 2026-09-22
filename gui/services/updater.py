"""
Проверка, загрузка и установка обновлений GUI.

Источник обновлений — релизы репозитория на GitHub: тот же `release.yml`,
что уже собирает `ident_settings_vX.Y.Z_win64.zip`, публикует его на вкладке
Releases, откуда апдейтер и берёт файл. Отдельного сервера для этого не
заводится.

Модуль ничего не знает про Qt — как и `config_service.py`, `task_service.py`:
шаги вызываются из фонового потока GUI (`gui.services.workers.Worker`),
а не отсюда. Каждый шаг — отдельная функция, а не один метод «обновиться»,
чтобы интерфейс мог показать прогресс между ними и остановиться на любом,
если оператор передумал.

Порядок использования:
    info = check()                      # None, если обновлений нет
    if info:
        archive = download(info, tmp_dir, on_progress=...)
        verify(archive, info)           # поднимает UpdateError, если хэш не совпал
        result = install(archive)       # переименование + откат при сбое
        if result.restart_required:
            restart()

Установка не трогает `config.ini`, `queue.json` и остальные файлы службы —
апдейтер заменяет только папку самого GUI (`app_dir()`), которая, по
конвенции `install_task_onedir.ps1`, отделена от рабочей папки службы.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import requests

from gui import __version__

from .logging_setup import logger
from .paths import app_dir

log = logger('обновление')

#: репозиторий, из релизов которого берутся обновления
REPO = 'strraaa/ident-gui'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'

#: так называется файл в архиве релиза (см. release.yml)
ASSET_SUFFIX = '_win64.zip'

#: имя exe внутри архива и в установленной папке
EXE_NAME = 'ident_settings.exe'

#: файл с контрольными суммами, если release.yml его публикует (см. план CI ниже)
SUMS_ASSET_NAME = 'SHA256SUMS.txt'

REQUEST_TIMEOUT_S = 15
DOWNLOAD_CHUNK = 256 * 1024
SELFTEST_TIMEOUT_S = 60


class UpdateError(Exception):
    """
    Ошибка на любом из шагов обновления.

    Текст пригоден для показа оператору как есть — формулировать заново
    в интерфейсе не нужно, как и с `ConfigService.load_error`.
    """


@dataclass
class UpdateInfo:
    """Сведения о доступном релизе"""
    version: str                  # без ведущей 'v': '0.0.3'
    notes: str                    # текст релиза с GitHub — заметки об изменениях
    download_url: str
    asset_name: str
    checksum: Optional[str]       # sha256 в нижнем регистре, если релиз его публикует


@dataclass
class InstallResult:
    installed: bool
    restart_required: bool
    message: str
    deferred: bool = False


# ----------------------------------------------------------------------
# Версии
# ----------------------------------------------------------------------

def parse_version(text: str) -> tuple:
    """
    'v0.0.3' -> (0, 0, 3). Нечисловой хвост компонента ('-rc1', '+build5')
    отбрасывается: сравнивать нужно только числа, а не то, в каком формате
    кто-то подписал тег.
    """
    text = text.strip()
    if text[:1] in ('v', 'V'):
        text = text[1:]

    parts = []
    for chunk in text.split('.'):
        digits = ''
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)

    while len(parts) < 3:
        parts.append(0)

    return tuple(parts[:3])


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


# ----------------------------------------------------------------------
# Проверка релиза
# ----------------------------------------------------------------------

def check(timeout: int = REQUEST_TIMEOUT_S) -> Optional[UpdateInfo]:
    """
    Спрашивает GitHub о последнем релизе.

    Возвращает None, если релиз не новее текущей версии — это нормальный
    исход проверки, а не ошибка. Сетевые сбои и неожиданный ответ сервера
    поднимаются как UpdateError с текстом, который можно показать оператору
    без изменений.
    """
    try:
        response = requests.get(
            API_LATEST, timeout=timeout,
            headers={'Accept': 'application/vnd.github+json'}
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        raise UpdateError(f'Не удалось проверить обновления: {e}') from e
    except ValueError as e:
        raise UpdateError('Сервер обновлений вернул неожиданный ответ') from e

    tag = str(payload.get('tag_name') or '')
    if not tag:
        raise UpdateError('В ответе GitHub нет версии релиза')

    if not is_newer(tag):
        log.info('Обновлений нет: текущая версия %s, на сервере %s', __version__, tag)
        return None

    assets = payload.get('assets') or []
    asset = _pick_asset(assets)
    if not asset:
        raise UpdateError(f'В релизе {tag} нет файла *{ASSET_SUFFIX}')

    info = UpdateInfo(
        version=tag[1:] if tag[:1] in ('v', 'V') else tag,
        notes=str(payload.get('body') or '').strip(),
        download_url=asset['browser_download_url'],
        asset_name=asset['name'],
        checksum=_find_checksum(assets, asset['name'], timeout),
    )
    log.info('Найдено обновление %s (%s)', info.version, info.asset_name)
    return info


def _pick_asset(assets: List[dict]) -> Optional[dict]:
    for asset in assets:
        if str(asset.get('name') or '').endswith(ASSET_SUFFIX):
            return asset
    return None


def _find_checksum(assets: List[dict], asset_name: str, timeout: int) -> Optional[str]:
    """
    Хэш архива из SHA256SUMS.txt, если релиз его публикует.

    Отсутствие файла не мешает обновлению — иначе релизы, собранные до
    появления этого файла в CI, нельзя было бы поставить вовсе. verify()
    просто пропустит сверку и предупредит об этом в журнале.
    """
    sums_asset = next((a for a in assets if a.get('name') == SUMS_ASSET_NAME), None)
    if not sums_asset:
        return None

    try:
        response = requests.get(sums_asset['browser_download_url'], timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        log.warning('%s есть в релизе, но не загрузился — сверка хэша будет пропущена', SUMS_ASSET_NAME)
        return None

    for line in response.text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip('*') == asset_name:
            return parts[0].lower()

    return None


# ----------------------------------------------------------------------
# Загрузка и проверка целостности
# ----------------------------------------------------------------------

def download(info: UpdateInfo, dest_dir: Path,
             on_progress: Optional[Callable[[int, int], None]] = None,
             timeout: int = REQUEST_TIMEOUT_S) -> Path:
    """
    Качает архив во временную папку (никогда не поверх текущей установки).

    on_progress(получено_байт, всего_байт) вызывается по мере закачки;
    `всего` может быть 0, если сервер не прислал Content-Length — интерфейс
    в этом случае показывает индикатор без процентов, а не пытается делить
    на ноль.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / info.asset_name

    try:
        with requests.get(info.download_url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get('Content-Length') or 0)
            done = 0

            with open(target, 'wb') as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
    except requests.RequestException as e:
        target.unlink(missing_ok=True)
        raise UpdateError(f'Не удалось скачать обновление: {e}') from e

    log.info('Обновление скачано: %s (%s байт)', target, target.stat().st_size)
    return target


def verify(archive: Path, info: UpdateInfo) -> None:
    """
    Сверяет SHA-256 архива с тем, что опубликован в релизе.

    Если релиз хэш не публикует, проверка молча пропускается (см.
    `_find_checksum`) — это решение уже принято в check(), здесь только
    предупреждение в журнал, чтобы это было видно при разборе жалобы.
    """
    if not info.checksum:
        raise UpdateError(
            f'Релиз {info.version} не публикует {SUMS_ASSET_NAME}; '
            'установка без проверки целостности запрещена.'
        )

    digest = hashlib.sha256()
    with open(archive, 'rb') as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b''):
            digest.update(chunk)
    actual = digest.hexdigest()

    if actual != info.checksum:
        archive.unlink(missing_ok=True)
        raise UpdateError(
            'Скачанный файл повреждён или подменён: контрольная сумма не совпадает.\n'
            f'Ожидалось {info.checksum}, получено {actual}.'
        )

    log.info('Контрольная сумма архива подтверждена')


# ----------------------------------------------------------------------
# Установка
# ----------------------------------------------------------------------

def install(archive: Path, install_dir: Optional[Path] = None,
            run_selftest: Optional[Callable[[Path], None]] = None) -> InstallResult:
    """
    Разворачивает архив поверх текущей установки с откатом при неудаче.

    Порядок:
    1. распаковать во временную папку — не на место установки;
    2. убедиться, что в архиве вообще есть exe — иначе откатывать нечего,
       и лучше сказать об этом сразу, чем после переименования папок;
    3. переименовать текущую установку в `<папка>.bak` (не удалить);
    4. перенести новую версию на освободившееся место;
    5. прогнать на ней самопроверку (`run_selftest`, по умолчанию —
       `ident_settings.exe --selftest`, тот же флаг, что использует CI
       после сборки).

    Любая ошибка на шагах 4–5 возвращает `.bak` на место и поднимает
    UpdateError — работающее приложение при любом исходе остаётся на диске
    в рабочем состоянии, не наполовину замененным.
    """
    run_selftest = run_selftest or _run_selftest
    install_dir = Path(install_dir) if install_dir else app_dir()

    if install_dir == app_dir() and not getattr(sys, 'frozen', False):
        # Защита от случайного вызова при разработке: app_dir() без сборки
        # указывает на корень репозитория, и переименовать его недопустимо
        raise UpdateError('Установка обновлений недоступна при запуске из исходников')

    with tempfile.TemporaryDirectory(prefix='ident_settings_update_') as tmp:
        extracted = _extract(archive, Path(tmp))

        exe = extracted / EXE_NAME
        if not exe.exists():
            raise UpdateError(f'В скачанном архиве нет {EXE_NAME} — архив собран неверно')

        # На Windows работающий exe удерживает каталог установки. Нельзя
        # переименовать его из самого GUI: WinError 32 возникает до rollback.
        if (
            sys.platform == 'win32'
            and getattr(sys, 'frozen', False)
            and install_dir.resolve() == app_dir().resolve()
        ):
            return _schedule_deferred_install(
                extracted,
                install_dir,
                os.getpid(),
            )

        backup_dir = install_dir.with_name(install_dir.name + '.bak')
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

        try:
            install_dir.rename(backup_dir)
        except OSError as e:
            raise UpdateError(
                f'Не удалось переименовать текущую установку: {e}. '
                'Приложение открыто другим процессом или не хватает прав администратора.'
            ) from e

        try:
            shutil.move(str(extracted), str(install_dir))
            run_selftest(install_dir / EXE_NAME)
        except Exception as e:
            log.error('Установка обновления не удалась, откат прежней версии: %s', e)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            backup_dir.rename(install_dir)
            raise UpdateError(
                f'Обновление не установилось, восстановлена прежняя версия. {e}'
            ) from e

        shutil.rmtree(backup_dir, ignore_errors=True)

    log.info('Обновление установлено в %s', install_dir)
    return InstallResult(
        installed=True, restart_required=True,
        message='Обновление установлено. Перезапустите приложение, чтобы применить его.'
    )


def _schedule_deferred_install(
    extracted: Path,
    install_dir: Path,
    process_id: int,
) -> InstallResult:
    """Передает замену внешнему процессу после закрытия текущего GUI."""
    staging = Path(tempfile.mkdtemp(prefix='ident_settings_staged_'))
    staged_app = staging / install_dir.name
    shutil.copytree(extracted, staged_app)

    script = staging / 'apply_update.ps1'
    script.write_text(
        _deferred_script(),
        encoding='utf-8',
    )

    command = [
        'powershell',
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        str(script),
        '-ProcessId',
        str(process_id),
        '-InstallDir',
        str(install_dir),
        '-StagedDir',
        str(staged_app),
        '-BackupDir',
        str(install_dir.with_name(install_dir.name + '.bak')),
        '-ExeName',
        EXE_NAME,
        '-CleanupDir',
        str(staging),
    ]
    try:
        subprocess.Popen(
            command,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            | getattr(subprocess, 'DETACHED_PROCESS', 0),
            close_fds=True,
        )
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f'Не удалось запустить помощник обновления: {e}') from e

    log.info('Отложенная установка передана helper-процессу, PID=%s', process_id)
    return InstallResult(
        installed=True,
        restart_required=True,
        deferred=True,
        message='Обновление подготовлено. Приложение будет закрыто и перезапущено автоматически.',
    )


def _deferred_script() -> str:
    """PowerShell helper: не загружает exe из каталога до его замены."""
    return r'''param(
    [int]$ProcessId,
    [string]$InstallDir,
    [string]$StagedDir,
    [string]$BackupDir,
    [string]$ExeName,
    [string]$CleanupDir
)
$ErrorActionPreference = "Stop"
$handoff = $false
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $quote = {
            param($value)
            '"' + ($value -replace '"', '\"') + '"'
        }
        $arguments = @(
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", (& $quote $PSCommandPath),
            "-ProcessId", $ProcessId.ToString(),
            "-InstallDir", (& $quote $InstallDir),
            "-StagedDir", (& $quote $StagedDir),
            "-BackupDir", (& $quote $BackupDir),
            "-ExeName", (& $quote $ExeName),
            "-CleanupDir", (& $quote $CleanupDir)
        )
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arguments
        $handoff = $true
        exit 0
    }

    while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 250
    }

    if (Test-Path $BackupDir) {
        Remove-Item -LiteralPath $BackupDir -Recurse -Force
    }
    Move-Item -LiteralPath $InstallDir -Destination $BackupDir
    Move-Item -LiteralPath $StagedDir -Destination $InstallDir

    $newExe = Join-Path $InstallDir $ExeName
    & $newExe --selftest
    if ($LASTEXITCODE -ne 0) {
        throw "Самопроверка новой версии завершилась с кодом $LASTEXITCODE"
    }

    Remove-Item -LiteralPath $BackupDir -Recurse -Force
    Start-Process -FilePath $newExe
}
catch {
    if (Test-Path $InstallDir) {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
    }
    if (Test-Path $BackupDir) {
        Move-Item -LiteralPath $BackupDir -Destination $InstallDir
    }
}
finally {
    if (-not $handoff -and (Test-Path $CleanupDir)) {
        Remove-Item -LiteralPath $CleanupDir -Recurse -Force
    }
}
'''


def _extract(archive: Path, dest: Path) -> Path:
    """
    Распаковывает архив и возвращает папку с самим приложением.

    `build_gui_exe.ps1`/`release.yml` кладут в zip одну папку верхнего
    уровня (`dist\\ident_settings`) — её и переносим на место установки,
    а не содержимое zip как есть, иначе внутри install_dir появится лишний
    уровень вложенности.
    """
    dest = Path(dest).resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if target != dest and dest not in target.parents:
                raise UpdateError('Архив обновления содержит небезопасный путь')
        zf.extractall(dest)

    entries = [p for p in dest.iterdir() if p.is_dir()]
    return entries[0] if len(entries) == 1 else dest


def _run_selftest(exe: Path) -> None:
    """
    Тот же `--selftest`, что `ident_settings.spec` прогоняет на сборочной
    машине: собирает окно на пустой папке и выходит. Здесь смысл тот же —
    поймать нерабочую сборку до того, как оператор понадеется на неё,
    а не после.
    """
    if not exe.exists():
        raise UpdateError(f'В новой версии нет {exe.name}')

    try:
        result = subprocess.run(
            [str(exe), '--selftest'],
            timeout=SELFTEST_TIMEOUT_S,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as e:
        raise UpdateError('Новая версия не прошла самопроверку (истекло время ожидания)') from e
    except OSError as e:
        raise UpdateError(f'Не удалось запустить новую версию: {e}') from e

    if result.returncode != 0:
        raise UpdateError('Новая версия не прошла самопроверку сборки (--selftest вернул ошибку)')


def restart(install_dir: Optional[Path] = None) -> None:
    """
    Запускает обновлённый exe и завершает текущий процесс.

    Вызывается только из собранного приложения после успешной install()
    — при запуске из исходников перезапускать нечего, интерфейс должен
    просто предложить закрыть и открыть заново вручную.
    """
    install_dir = Path(install_dir) if install_dir else app_dir()
    exe = install_dir / EXE_NAME

    if not exe.exists():
        raise UpdateError(f'Не найден {exe} — перезапустите приложение вручную')

    log.info('Перезапуск после обновления: %s', exe)
    subprocess.Popen([str(exe)], close_fds=True)
    sys.exit(0)
