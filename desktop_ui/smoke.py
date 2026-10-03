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
from .range_dialog import IPRangeDialog
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
        window = window_type(settings=settings, ranges=[
            {"type": "folder", "name": "Площадка 1", "children": [
                {"name": "Контейнер А", "ranges": ["192.0.2.1-24"]},
                {"type": "folder", "name": "Ночной сон", "children": [
                    {"name": "Ряд 2", "ranges": ["198.51.100.0/30"], "enabled": False}]}]}])
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        window.grab().save(str(output / "empty-light.png"))
        # Synthetic editor screenshots exercise validation without saving local settings.
        ranges_dialog = IPRangeDialog("Контейнер А", ["192.0.2.1-24", "192.0.2.10", "198.51.100.0/30"], window)
        ranges_dialog.show()
        app.processEvents()
        assert ranges_dialog.update_preview()
        ranges_dialog.grab().save(str(output / "networks-editor-light.png"))
        ranges_dialog.close()
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
                Capabilities={}, Stale=status == "Unknown",
                IdentifyEnabled=True if i in (1, 3) else False if i in (2, 4) else None,
                LED="Включена" if i in (1, 3) else "Выключена" if i in (2, 4) else "Неизвестно"))
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
        window.summary_panel.toggle.click()
        app.processEvents()
        window.grab().save(str(output / "dashboard-expanded-totals.png"))
        window.summary_panel.toggle.click()
        # New journal is tested entirely with synthetic outcomes, including uncertain writes.
        for i in range(24):
            outcome = ("succeeded", "unconfirmed", "failed", "unsupported")[i % 4]
            message = {"succeeded": "Ожидаемое состояние подтверждено чтением API",
                       "unconfirmed": "Запись принята; подтвердить состояние пока не удалось",
                       "failed": "Устройство отказало в выполнении команды",
                       "unsupported": "Текущий интерфейс не поддерживает эту команду"}[outcome]
            window.add_log(f"192.0.2.{i + 1}: [{outcome}] {message}", action="sleep" if i % 2 else "led_on")
        journal = window.log_dialog
        journal.show()
        journal.flush()
        journal.table.selectRow(22)
        app.processEvents()
        journal.grab().save(str(output / "journal-dark.png"))
        journal.language_box.setCurrentIndex(journal.language_box.findData("en"))
        assert window.btn_scan.text() == "Start scan"
        assert len(window.scan_data) == 6
        assert window.table.rowCount() == 6
        assert not window.windowIcon().isNull()
        window.grab().save(str(output / "dashboard-en.png"))
        english_settings = SettingsDialog(window.app_settings, window)
        assert english_settings.navigation.item(0).text() == "General"
        english_settings.show()
        app.processEvents()
        english_settings.grab().save(str(output / "settings-en.png"))
        english_settings.close()
        journal.outcome.setCurrentIndex(journal.outcome.findData("unconfirmed"))
        assert journal.proxy.rowCount() == 6
        app.processEvents()
        journal.grab().save(str(output / "journal-filtered-en.png"))
        journal.hide()
        window.table_preset.setCurrentIndex(window.table_preset.findData("management"))
        app.processEvents()
        window.grab().save(str(output / "dashboard-management.png"))
        window.table_preset.setCurrentIndex(window.table_preset.findData("compact"))
        app.processEvents()
        window.grab().save(str(output / "dashboard-compact.png"))
        window.table_preset.setCurrentIndex(window.table_preset.findData("overview"))
        ranges_dialog.show()
        app.processEvents()
        ranges_dialog.grab().save(str(output / "networks-editor-dark.png"))
        ranges_dialog.te_ranges.setPlainText("192.0.2.1\n192.0.2.8-2")
        assert not ranges_dialog.update_preview()
        app.processEvents()
        ranges_dialog.grab().save(str(output / "networks-editor-error.png"))
        ranges_dialog.close()
        dialog = SettingsDialog(settings, window, configure_access=window.configure_device_access)
        dialog.show()
        for index, name in enumerate(("general", "scanner", "columns", "pdf", "excel", "access")):
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
        # A command preflight/readback must replace the selected row, including
        # its identity, rather than keep an old ID or append another device.
        refreshed = dict(rows[1], DeviceId='demo-refreshed', Status='Sleep', Real='0.00 TH/s')
        counts_before = window.table.rowCount(), len(window.scan_data)
        window.on_result([refreshed])
        assert (window.table.rowCount(), len(window.scan_data)) == counts_before
        item = next(window.table.item(i, 0) for i in range(window.table.rowCount())
                    if window.table.item(i, 0).text() == '192.0.2.2')
        assert item.checkState() == Qt.CheckState.Checked
        assert item.data(Qt.ItemDataRole.UserRole + 1)['DeviceId'] == 'demo-refreshed'
        window.on_result([rows[1]])
        # Exercise actual GUI export handlers with modal dialogs intercepted.
        errors = []
        with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "critical", side_effect=lambda *args: errors.append(str(args))):
            window.take_screenshot()
            screenshot = app.clipboard().pixmap()
            assert not screenshot.isNull(), "Clipboard screenshot missing"
            assert screenshot.deviceIndependentSize().toSize() == window.content_panel.size()
            assert screenshot.save(str(output / "clipboard-dashboard.png"))
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
                  "profiles": len(registry.profiles), "exports": ["clipboard_png", "csv", "xlsx", "pdf"], "network_used": False}
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0
    except Exception:
        (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        if window is not None:
            window.close()
