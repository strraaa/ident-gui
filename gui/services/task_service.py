"""
Управление задачей планировщика Windows «IdentBitrix24Integration».

Служба синхронизации запускается как задача планировщика от имени SYSTEM
(см. install_task_onedir.ps1). После правки config.ini её нужно перезапустить:
конфигурация читается один раз при старте процесса.
"""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from .paths import TASK_NAME, TASK_PATH, default_install_dir

# Не показывать окно консоли при вызове PowerShell из GUI
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

# PowerShell 5.1 пишет вывод в кодировке консоли (обычно cp866), а Python
# по умолчанию читает его в кодировке системы (cp1251) — русские сообщения
# планировщика превращались в мусор. Договариваемся об UTF-8 с обеих сторон.
_FORCE_UTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"

STATE_TITLES = {
    'Ready': 'Готова к запуску',
    'Running': 'Работает',
    'Disabled': 'Отключена',
    'Queued': 'В очереди',
    'Unknown': 'Состояние неизвестно',
}

# Коды результата последнего запуска, которые не являются ошибкой
_OK_RESULTS = {0, 267009, 267011}  # успех, задача выполняется, задача ещё не запускалась


@dataclass
class TaskStatus:
    """Состояние задачи планировщика"""
    available: bool = False           # доступен ли провайдер планировщика
    exists: bool = False
    state: str = 'Unknown'
    last_run_time: str = ''
    last_task_result: Optional[int] = None
    next_run_time: str = ''
    error: str = ''
    error_kind: str = ''
    actual_task_path: str = ''
    processes: List[int] = field(default_factory=list)

    @property
    def state_title(self) -> str:
        return STATE_TITLES.get(self.state, self.state)

    @property
    def is_running(self) -> bool:
        return self.state == 'Running' or bool(self.processes)

    @property
    def last_result_is_error(self) -> bool:
        if self.last_task_result is None:
            return False
        return self.last_task_result not in _OK_RESULTS


class TaskService:
    """Статус, запуск и остановка задачи синхронизации"""

    def __init__(self, task_name: str = TASK_NAME, task_path: str = TASK_PATH):
        self.task_name = task_name
        self.task_path = task_path

    @property
    def supported(self) -> bool:
        """Управление задачей возможно только под Windows"""
        return sys.platform == 'win32'

    def status(self) -> TaskStatus:
        """Собирает состояние задачи и список процессов службы"""
        if not self.supported:
            return TaskStatus(
                available=False,
                error='Управление задачей доступно только под Windows'
            )

        script = self._status_script()

        code, out, err = self._run_powershell(script)

        if code != 0:
            return self._status_from_failure(err)

        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return TaskStatus(
                available=True,
                exists=False,
                error_kind='provider_unavailable',
                error='Планировщик вернул некорректный ответ'
            )

        if data.get('ErrorKind'):
            error_kind = str(data['ErrorKind'])
            messages = {
                'not_found': 'Задача не зарегистрирована',
                'wrong_path': 'Задача зарегистрирована в другом пути',
            }
            return TaskStatus(
                available=error_kind != 'provider_unavailable',
                exists=False,
                error_kind=error_kind,
                error=messages.get(error_kind, str(data.get('Error') or 'Ошибка планировщика')),
                actual_task_path=str(data.get('ActualTaskPath') or ''),
            )

        processes = data.get('Processes')
        if processes is None:
            processes = []
        elif isinstance(processes, int):
            processes = [processes]

        return TaskStatus(
            available=True,
            exists=True,
            state=str(data.get('State') or 'Unknown'),
            last_run_time=str(data.get('LastRunTime') or ''),
            last_task_result=data.get('LastTaskResult'),
            next_run_time=str(data.get('NextRunTime') or ''),
            processes=[int(p) for p in processes],
            actual_task_path=str(data.get('TaskPath') or self.task_path),
        )

    def start(self) -> tuple[bool, str]:
        """Запускает задачу. Возвращает (успех, сообщение)."""
        if not self.supported:
            return False, 'Доступно только под Windows'

        code, _, err = self._run_powershell(
            f"Start-ScheduledTask -TaskName {self._ps_quote(self.task_name)} "
            f"-TaskPath {self._ps_quote(self.task_path)}"
        )

        if code == 0:
            return True, 'Задача запущена'
        return False, self._clean_error(err) or 'Не удалось запустить задачу'

    def stop(self) -> tuple[bool, str]:
        """Останавливает задачу и снимает оставшиеся процессы службы"""
        if not self.supported:
            return False, 'Доступно только под Windows'

        script = (
            f"Stop-ScheduledTask -TaskName {self._ps_quote(self.task_name)} "
            f"-TaskPath {self._ps_quote(self.task_path)} "
            f"-ErrorAction SilentlyContinue;"
            f"Start-Sleep -Seconds 2;"
            f"Get-Process -Name 'ident_sync' -ErrorAction SilentlyContinue | Stop-Process -Force"
        )

        code, _, err = self._run_powershell(script)

        if code == 0:
            return True, 'Задача остановлена'
        return False, self._clean_error(err) or 'Не удалось остановить задачу'

    def restart(self) -> tuple[bool, str]:
        """Перезапуск — нужен после изменения конфигурации"""
        ok, message = self.stop()
        if not ok:
            return False, f'Остановка не удалась: {message}'

        ok, message = self.start()
        if not ok:
            return False, f'Запуск не удался: {message}'

        return True, 'Служба перезапущена'

    def install(self) -> tuple[bool, str]:
        """Регистрирует задачу из штатной папки установки, не затрагивая данные."""
        if not self.supported:
            return False, 'Доступно только под Windows'
        if not self.is_admin():
            return False, 'Для восстановления задачи нужны права администратора'

        install_dir = default_install_dir()
        exe_path = install_dir / 'ident_sync.exe'
        config_path = install_dir / 'config.ini'
        if not exe_path.is_file():
            return False, f'Исполняемый файл службы не найден: {exe_path}'
        if not config_path.is_file():
            return False, f'Конфигурация службы не найдена: {config_path}'

        script = (
            "$ErrorActionPreference='Stop';"
            f"$task = Get-ScheduledTask -TaskName {self._ps_quote(self.task_name)} "
            f"-TaskPath {self._ps_quote(self.task_path)} -ErrorAction SilentlyContinue;"
            "if ($task) { Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue; "
            "Unregister-ScheduledTask -InputObject $task -Confirm:$false };"
            f"$action=New-ScheduledTaskAction -Execute {self._ps_quote(str(exe_path))} "
            f"-WorkingDirectory {self._ps_quote(str(install_dir))};"
            "$trigger=New-ScheduledTaskTrigger -AtStartup;"
            "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable "
            "-MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 0);"
            "$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' "
            "-LogonType ServiceAccount -RunLevel Highest;"
            f"Register-ScheduledTask -TaskName {self._ps_quote(self.task_name)} "
            f"-TaskPath {self._ps_quote(self.task_path)} -Action $action -Trigger $trigger "
            "$Settings $settings -Principal $principal "
            "-Description 'IDENT to Bitrix24 integration service' | Out-Null"
        )
        code, _, err = self._run_powershell(script)
        if code:
            return False, self._clean_error(err) or 'Не удалось зарегистрировать задачу'
        return True, f'Задача зарегистрирована: {self.task_path}{self.task_name}'

    def is_admin(self) -> bool:
        """Запущено ли GUI с правами администратора (без них управление задачей недоступно)"""
        if not self.supported:
            return False

        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # ------------------------------------------------------------------

    @staticmethod
    def _run_powershell(script: str, timeout: int = 30) -> tuple[int, str, str]:
        try:
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                 _FORCE_UTF8 + script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                creationflags=_CREATE_NO_WINDOW
            )
            return result.returncode, (result.stdout or '').strip(), (result.stderr or '').strip()
        except subprocess.TimeoutExpired:
            return 1, '', 'Превышено время ожидания ответа планировщика'
        except FileNotFoundError:
            return 1, '', 'PowerShell не найден'
        except Exception as e:
            return 1, '', str(e)

    def _status_script(self) -> str:
        name = self._ps_quote(self.task_name)
        path = self._ps_quote(self.task_path)
        return (
            "$ErrorActionPreference='Stop';"
            f"$all=@(Get-ScheduledTask -ErrorAction Stop);"
            f"$t=$all | Where-Object {{ $_.TaskName -eq {name} }} | Select-Object -First 1;"
            f"if (-not $t) {{ [pscustomobject]@{{ErrorKind='not_found';"
            f"Error='Задача не зарегистрирована';}} | ConvertTo-Json -Compress; exit 0 }};"
            f"if ($t.TaskPath -ne {path}) {{ [pscustomobject]@{{ErrorKind='wrong_path';"
            f"Error=('Задача найдена в пути ' + $t.TaskPath);ActualTaskPath=$t.TaskPath;}} "
            f"| ConvertTo-Json -Compress; exit 0 }};"
            f"$i=Get-ScheduledTaskInfo -TaskName {name} -TaskPath {path} -ErrorAction Stop;"
            "$p=@(Get-Process -Name 'ident_sync' -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty Id);"
            "[pscustomobject]@{TaskPath=$t.TaskPath;State=$t.State.ToString();"
            "LastRunTime=$(if($i.LastRunTime){$i.LastRunTime.ToString('dd.MM.yyyy HH:mm:ss')}else{''});"
            "LastTaskResult=$i.LastTaskResult;"
            "NextRunTime=$(if($i.NextRunTime){$i.NextRunTime.ToString('dd.MM.yyyy HH:mm:ss')}else{''});"
            "Processes=$p} | ConvertTo-Json -Compress"
        )

    def _status_from_failure(self, error: str) -> TaskStatus:
        clean = self._clean_error(error)
        lowered = clean.lower()
        if any(token in lowered for token in ('access is denied', 'отказано в доступе', 'доступ запрещен')):
            kind = 'access_denied'
            message = 'Нет доступа к планировщику. Запустите приложение от имени администратора.'
        elif any(token in lowered for token in ('cim', 'msft_scheduledtask', 'rpc', 'служб')):
            kind = 'provider_unavailable'
            message = 'Провайдер планировщика недоступен. Проверьте службу Task Scheduler.'
        else:
            kind = 'command_failed'
            message = clean or 'Не удалось получить состояние планировщика'
        return TaskStatus(available=False, exists=False, error_kind=kind, error=message)

    @staticmethod
    def _ps_quote(value: str) -> str:
        """PowerShell single-quoted literal; user-controlled values cannot escape it."""
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _clean_error(err: str) -> str:
        """Первая содержательная строка из вывода PowerShell"""
        for line in (err or '').splitlines():
            line = line.strip()
            if line:
                return line[:300]
        return ''
