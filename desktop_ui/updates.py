"""Background check of the existing GitHub version manifest."""
import json
import re
import time
from urllib.parse import urlsplit
import requests
from PyQt6.QtCore import QThread, pyqtSignal


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Неверный формат версии")
    return tuple(int(part) for part in value.split("."))


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("Неверный формат манифеста")
    version_tuple(data.get("version"))
    url = urlsplit(data.get("url", ""))
    if url.scheme != "https" or url.netloc != "github.com" or not url.path.startswith("/Drubic8/AgentScanner/releases/"):
        raise ValueError("Неизвестный источник обновления")
    return data


class UpdateCheckWorker(QThread):
    result = pyqtSignal(object, str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            with requests.get(self.url, timeout=(3, 5), stream=True) as response:
                response.raise_for_status()
                content = bytearray()
                started = time.monotonic()
                while True:
                    chunk = response.raw.read1(4096, decode_content=True)
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > 65536 or time.monotonic() - started > 10:
                        raise ValueError("Ответ сервера обновлений слишком большой или медленный")
                data = validate_manifest(json.loads(content))
            self.result.emit(data, "")
        except Exception as exc:
            self.result.emit(None, str(exc))
