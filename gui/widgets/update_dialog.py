"""Диалог проверки и установки обновлений GUI."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressBar,
    QVBoxLayout,
)

from gui import __version__
from gui.services import updater
from gui.services.workers import WorkerRunner
from gui.theme import set_tone, tokens


class UpdateDialog(QDialog):
    """Показывает состояние обновления и не блокирует главное окно сетью."""

    download_progress = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Обновление приложения')
        self.setMinimumWidth(520)
        self._runner = WorkerRunner()
        self._info: Optional[updater.UpdateInfo] = None
        self._archive: Optional[Path] = None
        self._tmp_dir: Optional[Path] = None
        self.download_progress.connect(self._on_download_progress)

        layout = QVBoxLayout(self)
        layout.setSpacing(tokens.GAP_LARGE)

        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_status)

        self.lbl_notes = QLabel()
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.hide()
        layout.addWidget(self.lbl_notes)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Close, parent=self
        )
        self.btn_check = self.buttons.addButton(
            'Проверить снова', QDialogButtonBox.ActionRole
        )
        self.btn_install = self.buttons.addButton(
            'Скачать и установить', QDialogButtonBox.AcceptRole
        )
        self.btn_install.hide()
        self.buttons.rejected.connect(self.reject)
        self.btn_check.clicked.connect(self.check)
        self.btn_install.clicked.connect(self.download_and_install)
        layout.addWidget(self.buttons)

        self._set_status(f'Текущая версия: {__version__}. Проверяем обновления…')
        self.check()

    def _set_status(self, text: str, tone: Optional[str] = None) -> None:
        self.lbl_status.setText(text)
        set_tone(self.lbl_status, tone)

    def check(self) -> None:
        if self._busy():
            return
        self._set_busy(True)
        self._set_status(f'Текущая версия: {__version__}. Проверяем обновления…')
        self._runner.run(
            updater.check,
            self._check_succeeded,
            self._failed,
        )

    def _check_succeeded(self, info: Optional[updater.UpdateInfo]) -> None:
        self._set_busy(False)
        if info is None:
            self._set_status(
                f'Установлена последняя версия ({__version__}).', 'success'
            )
            return

        self._info = info
        self.lbl_notes.setText(
            f'Версия {info.version} уже доступна.\n\n'
            f'{info.notes or "Для этой версии нет описания изменений."}'
        )
        self.lbl_notes.show()
        self.btn_install.show()
        self._set_status(
            f'Доступно обновление: {__version__} → {info.version}.', 'accent'
        )

    def download_and_install(self) -> None:
        if self._info is None or self._busy():
            return

        self._set_busy(True)
        self.btn_install.hide()
        self._set_status(f'Скачиваем версию {self._info.version}…')
        self._runner.run(
            self._download,
            self._download_succeeded,
            self._failed,
        )

    def _download(self):
        import tempfile

        self._tmp_dir = Path(tempfile.mkdtemp(prefix='ident_settings_update_'))
        archive = updater.download(
            self._info,
            self._tmp_dir,
            on_progress=lambda done, total: self.download_progress.emit(done, total),
        )
        updater.verify(archive, self._info)
        return archive

    def _on_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 0)

    def _download_succeeded(self, archive: Path) -> None:
        self._archive = archive
        self._set_status('Проверка завершена. Устанавливаем обновление…')
        self._runner.run(
            updater.install,
            self._install_succeeded,
            self._failed,
            archive,
        )

    def _install_succeeded(self, result: updater.InstallResult) -> None:
        self._set_busy(False)
        self._set_status(result.message, 'success')
        self._cleanup_temp()
        if result.deferred:
            self.accept()
            # Helper-процесс уже ждет завершения GUI и запустит новую версию.
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
            return
        answer = QMessageBox.question(
            self,
            'Обновление готово',
            'Обновление установлено. Перезапустить приложение сейчас?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            updater.restart()
        self.accept()

    def _failed(self, message: str) -> None:
        self._set_busy(False)
        self._cleanup_temp()
        self._set_status(message, 'danger')
        self.btn_install.setVisible(self._info is not None)
        QMessageBox.warning(self, 'Обновление не выполнено', message)

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.btn_check.setDisabled(busy)
        self.buttons.button(QDialogButtonBox.Close).setDisabled(busy)

    def _busy(self) -> bool:
        return self.progress.isVisible()

    def _cleanup_temp(self) -> None:
        if self._tmp_dir is None:
            return
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)
        self._tmp_dir = None

    def closeEvent(self, event) -> None:
        if self._busy():
            event.ignore()
            return
        self._cleanup_temp()
        super().closeEvent(event)
