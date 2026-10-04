"""Qt background workers; update verification and installation live outside the UI."""
from threading import Event
from PyQt6.QtCore import QThread, pyqtSignal

from .update_package import (UpdateCancelled, discover_release, download_package,
                             validate_manifest, version_tuple)
from .update_installer import prepare_update


class UpdateCheckWorker(QThread):
    result = pyqtSignal(object, str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            self.result.emit(discover_release(self.url), "")
        except Exception as exc:
            self.result.emit(None, str(exc))


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, release, cache, target, parent=None):
        super().__init__(parent)
        self.release, self.cache, self.target = dict(release), cache, target
        self.cancel_event = Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        candidate = None
        try:
            candidate = download_package(self.release, self.cache, cancelled=self.cancel_event,
                                         progress=self.progress.emit)
            if self.cancel_event.is_set():
                raise UpdateCancelled()
            plan = prepare_update(candidate, self.target, self.release["version"], self.release["sha256"])
            if self.cancel_event.is_set():
                raise UpdateCancelled()
            self.ready.emit(plan)
        except UpdateCancelled:
            self.clean_staging(candidate)
            self.cancelled.emit()
        except Exception as exc:
            self.clean_staging(candidate)
            self.failed.emit(str(exc))

    @staticmethod
    def clean_staging(candidate):
        if candidate is None:
            return
        for name in ("package.exe", "updater.exe", "plan.json"):
            try:
                (candidate.parent / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            candidate.parent.rmdir()
        except OSError:
            pass
