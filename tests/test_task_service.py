import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.services.task_service import TaskService


def test_status_reports_missing_task_without_treating_it_as_provider_failure(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(type(service), 'supported', property(lambda self: True))
    monkeypatch.setattr(
        service,
        '_run_powershell',
        lambda script: (
            0,
            json.dumps({'ErrorKind': 'not_found', 'Error': 'Задача не зарегистрирована'}),
            '',
        ),
    )

    status = service.status()

    assert status.available is True
    assert status.exists is False
    assert status.error_kind == 'not_found'


def test_status_distinguishes_task_registered_in_wrong_path(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(type(service), 'supported', property(lambda self: True))
    monkeypatch.setattr(
        service,
        '_run_powershell',
        lambda script: (
            0,
            json.dumps({
                'ErrorKind': 'wrong_path',
                'Error': 'Задача найдена',
                'ActualTaskPath': '\\Other\\',
            }),
            '',
        ),
    )

    status = service.status()

    assert status.error_kind == 'wrong_path'
    assert status.actual_task_path == '\\Other\\'


def test_status_classifies_cim_failure():
    status = TaskService()._status_from_failure(
        'Get-ScheduledTask : Запрос CIM экземпляров класса '
        'Root/Microsoft/Windows/TaskScheduler/MSFT_ScheduledTask'
    )

    assert status.available is False
    assert status.error_kind == 'provider_unavailable'


def test_power_shell_literals_are_escaped():
    quoted = TaskService._ps_quote("name'; Write-Host hacked")

    assert quoted == "'name''; Write-Host hacked'"


def test_status_accepts_single_process_and_optional_times(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(type(service), 'supported', property(lambda self: True))
    monkeypatch.setattr(
        service,
        '_run_powershell',
        lambda script: (
            0,
            json.dumps({
                'TaskPath': '\\IDENT\\',
                'State': 'Running',
                'LastTaskResult': 0,
                'Processes': 1234,
            }),
            '',
        ),
    )

    status = service.status()

    assert status.exists is True
    assert status.is_running is True
    assert status.processes == [1234]
    assert status.last_run_time == ''
