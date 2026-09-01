# Build IDENT -> Bitrix24 Settings GUI (One Directory Mode)
# Creates dist\ident_settings\ident_settings.exe
#
# -NoPause: не ждать Enter в конце (нужно для CI, раннер не интерактивный)

param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

# GitHub Actions и прочие CI выставляют $env:CI — пауза там повесит job
if ($env:CI) { $NoPause = $true }

function Exit-Build {
    param([int]$Code = 0)
    if (-not $NoPause) { Read-Host "Press Enter to exit" }
    exit $Code
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Building IDENT Settings GUI" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
$PythonCmd = "python"
try {
    $PythonVersion = & $PythonCmd --version 2>&1
    Write-Host "Python: $PythonVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Python not found" -ForegroundColor Red
    Write-Host ""
    Exit-Build 1
}

if (-not (Test-Path "gui_main.py")) {
    Write-Host "ERROR: gui_main.py not found" -ForegroundColor Red
    Write-Host ""
    Exit-Build 1
}

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Cyan
$ReqLock = Join-Path $ScriptDir "requirements.lock"
if (Test-Path $ReqLock) {
    & $PythonCmd -m pip install --require-hashes -r $ReqLock
} else {
    Write-Host "WARNING: requirements.lock not found, falling back to requirements.txt" -ForegroundColor Yellow
    & $PythonCmd -m pip install -r requirements.txt
}

# PySide6 lives in requirements-gui.txt: the sync service does not need it
& $PythonCmd -m pip install -r requirements-gui.txt
Write-Host ""

# Clean previous GUI build only (do not touch the service build)
if (Test-Path "dist\ident_settings") {
    Write-Host "Cleaning dist\ident_settings..." -ForegroundColor Yellow
    Remove-Item -Path "dist\ident_settings" -Recurse -Force
}
if (Test-Path "build\ident_settings") {
    Remove-Item -Path "build\ident_settings" -Recurse -Force
}
if (Test-Path "ident_settings.spec") {
    Remove-Item -Path "ident_settings.spec" -Force
}

Write-Host ""
Write-Host "Building EXE (onedir, no console)..." -ForegroundColor Cyan
Write-Host ""

# ВАЖНО: config.ini намеренно НЕ вшивается в сборку.
# Приложение читает конфигурацию из папки установки службы во время работы,
# а секреты в дистрибутиве не нужны.
& pyinstaller `
    --name="ident_settings" `
    --onedir `
    --noconsole `
    --hidden-import=requests `
    --hidden-import=configparser `
    --exclude-module=PySide6.QtWebEngineCore `
    --exclude-module=PySide6.QtWebEngineWidgets `
    --exclude-module=PySide6.QtQuick `
    --exclude-module=PySide6.Qt3DCore `
    --exclude-module=PySide6.QtCharts `
    --exclude-module=PySide6.QtMultimedia `
    --noconfirm `
    gui_main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Build failed" -ForegroundColor Red
    Write-Host ""
    Exit-Build 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Build Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "EXE location: dist\ident_settings\ident_settings.exe" -ForegroundColor White
Write-Host ""
Write-Host "Usage:" -ForegroundColor Cyan
Write-Host "  Run as Administrator - otherwise service restart is unavailable" -ForegroundColor Yellow
Write-Host "  Custom folder: ident_settings.exe --workdir ""C:\Program Files\IdentBitrix24""" -ForegroundColor White
Write-Host ""
Exit-Build 0
