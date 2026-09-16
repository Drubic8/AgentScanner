"""Explicit offline UI/release smoke check with synthetic documentation addresses."""
import json
import traceback
import os
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from .preferences import defaults
from .settings_dialog import SettingsDialog
from miner_scanner.profiles import ProfileRegistry


def run_smoke(app, window_type, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    window = None
    try:
        # Qt's offscreen Windows plugin does not enumerate installed fonts.
        font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"):
            QFontDatabase.addApplicationFont(str(font_directory / name))
        # Loading the catalog also checks PyInstaller resource collection.
        registry = ProfileRegistry()
        settings = defaults()
        settings.update(theme="light", export_dir=str(output), export_csv_dir=str(output))
        window = window_type(settings=settings, ranges=[{"name": "Демонстрационная сеть", "ranges": ["192.0.2.1-24"]}])
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        window.grab().save(str(output / "empty-light.png"))
        rows = []
        for i, (model, status, rate, firmware) in enumerate([
            ("Antminer S21", "Running", "200.00 TH/s", "Stock"),
            ("Whatsminer M60", "Running", "170.00 TH/s", "Stock"),
            ("Antminer S19", "Sleep", "0.00 TH/s", "VNish"),
            ("Avalon 1346", "Error", "—", "Stock"),
            ("Antminer S21", "WaitWork", "—", "PitBit"),
            ("Whatsminer M60", "Unknown", "—", "Stock"),
        ], 1):
            rows.append(dict(IP=f"192.0.2.{i}", Model=model, Make="Demo", Algo="SHA256",
                Status=status, Real=rate, Avg=rate, RawHash=float(rate.split()[0]) if rate != "—" else None,
                SortIP=3221225984+i, Temp="65 / 68 °C", Fan="4200 / 4300", Uptime="2d 4h 10m",
                Error="Пример ошибки" if status == "Error" else "", ErrorDetails="Демонстрационные данные",
                Pool="stratum+tcp://example.invalid:3333", Worker=f"demo.worker-{i:02}",
                Firmware=firmware, FirmwareVersion="demo", DeviceId=f"demo-{i}", ProfileId="demo",
                Capabilities={}, Stale=status == "Unknown"))
        window.on_result(rows)
        window.stats_timer.stop()
        window.update_stats()
        window.status_bar.setText("Демонстрационные данные · подключение к ASIC не выполнялось")
        app.processEvents()
        window.grab().save(str(output / "dashboard-light.png"))
        window.dark_mode = True
        window.apply_theme()
        app.processEvents()
        window.grab().save(str(output / "dashboard-dark.png"))
        dialog = SettingsDialog(settings, window)
        dialog.show()
        for index, name in enumerate(("general", "scanner", "columns", "pdf", "excel")):
            dialog.navigation.setCurrentRow(index)
            app.processEvents()
            dialog.grab().save(str(output / f"settings-{name}.png"))
        dialog.close()
        window.search_input.setText("192.0.2.2")
        window.btn_select_all.click()
        selected = [window.table.item(i, 0).text() for i in range(window.table.rowCount())
                    if window.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        assert selected == ["192.0.2.2"], selected
        window.search_input.clear()
        # Exercise actual GUI export handlers with modal dialogs intercepted.
        errors = []
        with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "critical", side_effect=lambda *args: errors.append(str(args))):
            for extension in ("csv", "xlsx"):
                destination = output / f"smoke-report.{extension}"
                with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), extension)):
                    window.export_csv()
                assert destination.is_file(), errors
            window.last_scan_name = "UI_smoke"
            window.export_pdf_pro()
        assert not errors, errors
        assert list(output.glob("UI_smoke_*.pdf")), "PDF export missing"
        result = {"ok": True, "version": app.applicationVersion(), "synthetic_rows": len(rows),
                  "profiles": len(registry.profiles), "exports": ["csv", "xlsx", "pdf"], "network_used": False}
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0
    except Exception:
        (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        if window is not None:
            window.close()
