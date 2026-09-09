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

from .paths import TASK_NAME, TASK_PATH

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
    available: bool = False           # доступно ли управление (Windows + задача найдена)
    exists: bool = False
    state: str = 'Unknown'
    last_run_time: str = ''
    last_task_result: Optional[int] = None
    next_run_time: str = ''
    error: str = ''
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

        script = (
            f"$ErrorActionPreference='Stop';"
            f"$t = Get-ScheduledTask -TaskName '{self.task_name}' -TaskPath '{self.task_path}';"
            f"$i = Get-ScheduledTaskInfo -TaskName '{self.task_name}' -TaskPath '{self.task_path}';"
            f"$p = @(Get-Process -Name 'ident_sync' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id);"
            f"[pscustomobject]@{{"
            f"State=$t.State.ToString();"
            f"LastRunTime=$i.LastRunTime.ToString('dd.MM.yyyy HH:mm:ss');"
            f"LastTaskResult=$i.LastTaskResult;"
            f"NextRunTime=$i.NextRunTime.ToString('dd.MM.yyyy HH:mm:ss');"
            f"Processes=$p"
            f"}} | ConvertTo-Json -Compress"
        )

        code, out, err = self._run_powershell(script)

        if code != 0:
            return TaskStatus(
                available=True,
                exists=False,
                error=self._clean_error(err) or 'Задача не найдена в планировщике'
            )

        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return TaskStatus(available=True, exists=False, error='Не удалось разобрать ответ планировщика')

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
        )

    def start(self) -> tuple[bool, str]:
        """Запускает задачу. Возвращает (успех, сообщение)."""
        if not self.supported:
            return False, 'Доступно только под Windows'

        code, _, err = self._run_powershell(
            f"Start-ScheduledTask -TaskName '{self.task_name}' -TaskPath '{self.task_path}'"
        )

        if code == 0:
            return True, 'Задача запущена'
        return False, self._clean_error(err) or 'Не удалось запустить задачу'

    def stop(self) -> tuple[bool, str]:
        """Останавливает задачу и снимает оставшиеся процессы службы"""
        if not self.supported:
            return False, 'Доступно только под Windows'

        script = (
            f"Stop-ScheduledTask -TaskName '{self.task_name}' -TaskPath '{self.task_path}' "
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

    @staticmethod
    def _clean_error(err: str) -> str:
        """Первая содержательная строка из вывода PowerShell"""
        for line in (err or '').splitlines():
            line = line.strip()
            if line:
                return line[:300]
        return ''
