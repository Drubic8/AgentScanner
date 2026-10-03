"""Per-user preferences. Legacy files next to the application remain readable."""
import json
import os
from pathlib import Path

COLUMNS = {
    "IP": "IP", "Model": "Model", "Algo": "Algo", "Status": "State",
    "Error": "Errors", "Uptime": "Uptime", "Real HR": "Real Hash",
    "Avg HR": "Avg Hash", "Temp": "Temp", "Fan": "Fans",
    "Pool": "Pool", "Worker": "Worker", "LED": "LED",
}
BASE_COLUMNS = list(COLUMNS)
COLUMNS.update(Firmware="Firmware", FirmwareVersion="Version", Modes="Modes")

# Logical column IDs never change: existing exports and saved preferences use them.
TABLE_PRESETS = {
    "overview": ("Обзор", BASE_COLUMNS),
    "compact": ("Компактный", ["IP", "LED", "Model", "Status", "Error", "Real HR", "Temp"]),
    "diagnostics": ("Диагностика", ["IP", "LED", "Model", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Firmware", "FirmwareVersion"]),
    "pools": ("Пулы и воркеры", ["IP", "Model", "Status", "Real HR", "Pool", "Worker"]),
    "management": ("Управление", ["IP", "LED", "Model", "Status", "Error", "Real HR", "Firmware", "Modes"]),
}


def data_directory():
    override = os.environ.get("ASIC_MONITOR_DATA_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "ASICMonitor"


def defaults():
    return dict(scan_bitmain=True, scan_whatsminer=True, scan_elphapex=True,
                scan_other=True, timeout=2, workers=64, command_workers=32, theme="system", density="comfortable",
                check_updates=False, summary_expanded=False, columns_version=2, journal_language="ru", table_preset="overview",
                column_order=["IP", "LED"] + [c for c in COLUMNS if c not in ("IP", "LED")],
                column_widths={}, export_dir="", export_csv_dir="", copy_pdf=False,
                copy_csv=False, pdf_sort="IP", csv_sort="IP", ui_cols=list(BASE_COLUMNS),
                pdf_cols=list(BASE_COLUMNS), csv_cols=list(BASE_COLUMNS))


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
    for key, lo, hi in (("timeout", 1, 10), ("workers", 1, 128), ("command_workers", 1, 32)):
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
    # Introduce the new indicator once; later explicit hiding remains respected.
    if settings.get("columns_version") != 2 and "LED" not in result["ui_cols"]:
        result["ui_cols"].append("LED")
    for key in ("pdf_sort", "csv_sort"):
        if result[key] not in COLUMNS:
            result[key] = "IP"
    if result["theme"] not in ("system", "light", "dark"):
        result["theme"] = "system"
    if result["density"] not in ("comfortable", "compact"):
        result["density"] = "comfortable"
    if result["journal_language"] not in ("ru", "en"):
        result["journal_language"] = "ru"
    if result["table_preset"] not in (*TABLE_PRESETS, "custom"):
        result["table_preset"] = "custom"
    order = settings.get("column_order")
    if isinstance(order, list):
        result["column_order"] = ["IP"]
        result["column_order"].extend(c for c in order if isinstance(c, str) and c in COLUMNS and c not in result["column_order"])
        result["column_order"].extend(c for c in COLUMNS if c not in result["column_order"])
    widths = settings.get("column_widths")
    if isinstance(widths, dict):
        for density in ("compact", "comfortable"):
            values = widths.get(density)
            if isinstance(values, dict):
                result["column_widths"][density] = {code: width for code, width in values.items()
                    if code in COLUMNS and isinstance(width, int) and not isinstance(width, bool) and 36 <= width <= 1200}
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
