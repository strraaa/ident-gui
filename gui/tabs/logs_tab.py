"""
Вкладка «Логи»: хвост журнала службы с фильтрацией.

Файл читается с конца — журналы ротируются по размеру, но за сутки активной
работы всё равно вырастают до десятков мегабайт, и грузить их целиком незачем.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout
)

from gui.services.config_service import ConfigService
from gui.tabs.base import BaseTab

# Сколько байт с конца файла читать
TAIL_BYTES = 512 * 1024

ERROR_MARKERS = ('ERROR', 'CRITICAL', 'Traceback', 'ОШИБКА')


class LogsTab(BaseTab):
    """Просмотр журналов службы"""

    title = 'Логи'

    def __init__(self, config: ConfigService, parent=None):
        super().__init__(config, parent)
        self._current_file: Optional[Path] = None
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._auto_refresh)

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Панель управления
        controls = QHBoxLayout()

        self.cmb_file = QComboBox()
        self.cmb_file.setMinimumWidth(260)
        self.cmb_file.currentIndexChanged.connect(self._on_file_changed)

        self.spin_lines = QSpinBox()
        self.spin_lines.setRange(50, 5000)
        self.spin_lines.setSingleStep(50)
        self.spin_lines.setValue(300)
        self.spin_lines.valueChanged.connect(self.refresh)

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText('Фильтр по тексту строки')
        self.txt_filter.textChanged.connect(self.refresh)

        self.chk_errors = QCheckBox('Только ошибки')
        self.chk_errors.stateChanged.connect(self.refresh)

        self.chk_auto = QCheckBox('Автообновление')
        self.chk_auto.stateChanged.connect(self._on_auto_toggled)

        controls.addWidget(QLabel('Файл:'))
        controls.addWidget(self.cmb_file)
        controls.addWidget(QLabel('Строк:'))
        controls.addWidget(self.spin_lines)
        controls.addWidget(self.txt_filter, stretch=1)
        controls.addWidget(self.chk_errors)
        controls.addWidget(self.chk_auto)
        layout.addLayout(controls)

        # Текст журнала
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)

        font = QFont('Consolas' if sys.platform == 'win32' else 'Monospace')
        font.setStyleHint(QFont.TypeWriter)
        font.setPointSize(9)
        self.view.setFont(font)

        layout.addWidget(self.view, stretch=1)

        # Нижняя панель
        bottom = QHBoxLayout()

        self.lbl_info = QLabel('—')
        btn_refresh = QPushButton('Обновить')
        btn_refresh.clicked.connect(self.refresh)

        btn_open_dir = QPushButton('Открыть папку логов')
        btn_open_dir.clicked.connect(self._open_log_dir)

        bottom.addWidget(self.lbl_info, stretch=1)
        bottom.addWidget(btn_refresh)
        bottom.addWidget(btn_open_dir)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------

    def on_activated(self):
        self._reload_file_list()
        self.refresh()

    def _reload_file_list(self):
        """Обновляет список файлов журнала, сохраняя текущий выбор"""
        log_dir = self.config.log_dir_path()
        previous = self.cmb_file.currentData()

        files = []
        if log_dir.exists():
            files = sorted(
                (p for p in log_dir.glob('*.log*') if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

        self.cmb_file.blockSignals(True)
        self.cmb_file.clear()

        for path in files:
            size_mb = path.stat().st_size / (1024 * 1024)
            self.cmb_file.addItem(f'{path.name}  ({size_mb:.1f} МБ)', str(path))

        if previous:
            index = self.cmb_file.findData(previous)
            if index >= 0:
                self.cmb_file.setCurrentIndex(index)

        self.cmb_file.blockSignals(False)

        if not files:
            self.view.setPlainText(f'В каталоге {log_dir} нет файлов журнала.')
            self.lbl_info.setText('')

    def _on_file_changed(self):
        self.refresh()

    def _on_auto_toggled(self):
        if self.chk_auto.isChecked():
            self._timer.start()
        else:
            self._timer.stop()

    def _auto_refresh(self):
        if self.isVisible():
            self.refresh()

    def refresh(self):
        """Перечитывает хвост выбранного файла"""
        data = self.cmb_file.currentData()
        if not data:
            return

        path = Path(data)
        if not path.exists():
            self.view.setPlainText(f'Файл не найден: {path}')
            return

        lines = self._read_tail(path, self.spin_lines.value())
        filtered = self._filter_lines(lines)

        # Сохраняем позицию прокрутки, если оператор читает середину журнала
        scrollbar = self.view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4

        self.view.setPlainText('\n'.join(filtered))

        if at_bottom:
            self.view.moveCursor(QTextCursor.End)

        self.lbl_info.setText(
            f'{path.name}: показано строк {len(filtered)} из {len(lines)} прочитанных'
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _read_tail(path: Path, max_lines: int) -> List[str]:
        """Читает хвост файла, не загружая его целиком"""
        try:
            size = path.stat().st_size
            with open(path, 'rb') as f:
                if size > TAIL_BYTES:
                    f.seek(size - TAIL_BYTES)
                    f.readline()  # отбрасываем возможную обрезанную строку
                chunk = f.read()

            text = chunk.decode('utf-8', errors='replace')
            lines = text.splitlines()
            return lines[-max_lines:]
        except OSError as e:
            return [f'Не удалось прочитать файл: {e}']

    def _filter_lines(self, lines: List[str]) -> List[str]:
        needle = self.txt_filter.text().strip().lower()
        only_errors = self.chk_errors.isChecked()

        result = []
        for line in lines:
            if only_errors and not any(marker in line for marker in ERROR_MARKERS):
                continue
            if needle and needle not in line.lower():
                continue
            result.append(line)

        return result

    def _open_log_dir(self):
        log_dir = self.config.log_dir_path()

        if not log_dir.exists():
            QMessageBox.information(self, 'Логи', f'Каталог не найден:\n{log_dir}')
            return

        try:
            if sys.platform == 'win32':
                os.startfile(str(log_dir))  # noqa: S606 — штатный способ открыть проводник
            elif sys.platform == 'darwin':
                subprocess.run(['open', str(log_dir)], check=False)
            else:
                subprocess.run(['xdg-open', str(log_dir)], check=False)
        except Exception as e:
            QMessageBox.warning(self, 'Логи', f'Не удалось открыть каталог:\n{e}')
