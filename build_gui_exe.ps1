# Сборка приложения настроек Ident -> Битрикс24
# Итог: dist\ident_settings\ident_settings.exe
#
# Состав сборки описан в ident_settings.spec — там же список того, что из
# дистрибутива вычищается. Здесь только установка зависимостей, запуск
# PyInstaller и проверка результата.
#
# -NoPause: не ждать Enter в конце (нужно для CI, раннер не интерактивный)

param(
    [switch]$NoPause,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

# Русский текст в выводе Python: без этого на англоязычной Windows и на
# раннере GitHub stdout получает кодировку системы (cp1252), и первая же
# кириллическая строка валит сборку с UnicodeEncodeError
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# GitHub Actions и прочие CI выставляют $env:CI — пауза там повесит job
if ($env:CI) { $NoPause = $true }

function Exit-Build {
    param([int]$Code = 0)
    if (-not $NoPause) { Read-Host "Press Enter to exit" }
    exit $Code
}

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host $Text -ForegroundColor Cyan
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Сборка приложения настроек" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$PythonCmd = "python"
try {
    $PythonVersion = & $PythonCmd --version 2>&1
    Write-Host "Python: $PythonVersion" -ForegroundColor Green
} catch {
    Write-Host "ОШИБКА: Python не найден" -ForegroundColor Red
    Exit-Build 1
}

foreach ($Required in @("gui_main.py", "ident_settings.spec", "gui\assets\app.ico")) {
    if (-not (Test-Path $Required)) {
        Write-Host "ОШИБКА: не найден $Required" -ForegroundColor Red
        Exit-Build 1
    }
}

if (-not $SkipInstall) {
    Write-Step "Установка зависимостей"

    $ReqLock = Join-Path $ScriptDir "requirements.lock"
    if (Test-Path $ReqLock) {
        & $PythonCmd -m pip install --require-hashes -r $ReqLock
    } else {
        Write-Host "ВНИМАНИЕ: requirements.lock не найден, ставлю requirements.txt" -ForegroundColor Yellow
        & $PythonCmd -m pip install -r requirements.txt
    }

    & $PythonCmd -m pip install -r requirements-gui.txt
    if ($LASTEXITCODE -ne 0) { Exit-Build 1 }
}

Write-Step "Очистка прошлой сборки"

# Трогаем только каталоги приложения настроек: сборка службы лежит рядом
foreach ($Path in @("dist\ident_settings", "build\ident_settings")) {
    if (Test-Path $Path) { Remove-Item -Path $Path -Recurse -Force }
}

Write-Step "Сборка (onedir, без консоли)"

# ВАЖНО: config.ini намеренно НЕ вшивается в сборку.
# Приложение читает конфигурацию из папки установки службы во время работы,
# а секреты в дистрибутиве не нужны. Вшивается только config.example.ini.
& pyinstaller --noconfirm --clean ident_settings.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "ОШИБКА: сборка не удалась" -ForegroundColor Red
    Exit-Build 1
}

Write-Step "Самопроверка собранного приложения"

# Приложение строит окно и выходит. Так на сборочной машине ловится
# перестаравшаяся чистка библиотек Qt: без плагина платформы или стиля
# окно не создастся, и код возврата будет не нулевым.
$Exe = "dist\ident_settings\ident_settings.exe"

# Две проверки подряд: offscreen ловит нехватку самих библиотек Qt,
# запуск с обычной платформой — нехватку плагина qwindows и стиля
foreach ($Platform in @("offscreen", "windows")) {
    if ($Platform -eq "windows") {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $Platform
    }

    & $Exe --selftest
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ОШИБКА: самопроверка не прошла ($Platform), код $LASTEXITCODE" -ForegroundColor Red
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        Exit-Build 1
    }
    Write-Host "  $Platform — в порядке" -ForegroundColor Green
}
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

$SizeMb = [math]::Round(
    ((Get-ChildItem "dist\ident_settings" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1
)

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Сборка готова" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Приложение: dist\ident_settings\ident_settings.exe" -ForegroundColor White
Write-Host "Размер:     $SizeMb МБ" -ForegroundColor White
Write-Host ""
Write-Host "Запуск от имени администратора нужен, чтобы перезапускать службу" -ForegroundColor Yellow
Write-Host "Своя папка: ident_settings.exe --workdir ""C:\Program Files\IdentBitrix24""" -ForegroundColor White
Write-Host ""
Exit-Build 0
