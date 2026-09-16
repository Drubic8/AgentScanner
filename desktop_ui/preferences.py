"""Per-user preferences. Legacy files next to the application remain readable."""
import json
import os
from pathlib import Path

COLUMNS = {
    "IP": "IP-адрес", "Model": "Модель", "Algo": "Алгоритм", "Status": "Состояние",
    "Error": "Ошибки", "Uptime": "Время работы", "Real HR": "Хешрейт",
    "Avg HR": "Средний хешрейт", "Temp": "Температура", "Fan": "Вентиляторы",
    "Pool": "Пул", "Worker": "Воркер",
}


def data_directory():
    override = os.environ.get("ASIC_MONITOR_DATA_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "ASICMonitor"


def defaults():
    return dict(scan_bitmain=True, scan_whatsminer=True, scan_elphapex=True,
                scan_other=True, timeout=2, workers=64, theme="system", density="comfortable",
                check_updates=False, export_dir="", export_csv_dir="", copy_pdf=False,
                copy_csv=False, pdf_sort="IP", csv_sort="IP", ui_cols=list(COLUMNS),
                pdf_cols=list(COLUMNS), csv_cols=list(COLUMNS))


def normalize(settings):
    result = defaults()
    if not isinstance(settings, dict):
        return result
    for key, default in result.items():
        value = settings.get(key, default)
        if isinstance(default, bool):
            result[key] = value if isinstance(value, bool) else default
        elif isinstance(default, str):
            result[key] = value if isinstance(value, str) else default
    for key, lo, hi in (("timeout", 1, 10), ("workers", 1, 128)):
        try:
            result[key] = max(lo, min(hi, int(settings.get(key, result[key]))))
        except (TypeError, ValueError, OverflowError):
            pass
    for key in ("ui_cols", "pdf_cols", "csv_cols"):
        value = settings.get(key)
        if isinstance(value, list):
            result[key] = [c for c in COLUMNS if c in value] or list(COLUMNS)
    if "IP" not in result["ui_cols"]:
        result["ui_cols"].insert(0, "IP")
    for key in ("pdf_sort", "csv_sort"):
        if result[key] not in COLUMNS:
            result[key] = "IP"
    if result["theme"] not in ("system", "light", "dark"):
        result["theme"] = "system"
    if result["density"] not in ("comfortable", "compact"):
        result["density"] = "comfortable"
    return result


def load_json(path, legacy_path=None, default=None):
    path = Path(path)
    source = path if path.exists() else Path(legacy_path) if legacy_path else path
    try:
        return json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
