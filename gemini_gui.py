import sys
import re
import os
import json
import time
import requests
import webbrowser
import pandas as pd
from datetime import datetime
from pathlib import Path
from desktop_ui.preferences import COLUMNS, data_directory, normalize, load_json, write_json
from desktop_ui.settings_dialog import SettingsDialog
from desktop_ui.theme import apply_theme as apply_desktop_theme
from desktop_ui.updates import UpdateCheckWorker, version_tuple
from desktop_ui.reports import export_frame, sorted_frame
from collections import Counter, defaultdict


import socket
import platform

os.environ["QT_LOGGING_RULES"] = "*.warning=false" # Отключаем спам-варнинги Qt

from threading import Event

# === ФУНКЦИЯ ОПРЕДЕЛЕНИЯ ТЕМЫ WINDOWS ===
def is_system_dark_mode():
    if platform.system() == "Windows":
        try:
            import winreg
            registry = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
            key = winreg.OpenKey(registry, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0  # 0 = Темная, 1 = Светлая
        except Exception:
            pass
    return True # По умолчанию темная

# Константы автообновления
CURRENT_VERSION = "2.0.1"
UPDATE_INFO_URL = "https://raw.githubusercontent.com/Drubic8/AgentScanner/main/version.json"

# --- ФИКС ПУТЕЙ ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Добавляем путь к папке miner_scanner
scanner_path = os.path.join(current_dir, 'miner_scanner')
if os.path.exists(scanner_path) and scanner_path not in sys.path:
    sys.path.append(scanner_path)

# --- QT IMPORTS ---
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPushButton, QLabel, QLineEdit, QFileDialog, 
                             QProgressBar, QMessageBox, QHeaderView, QCheckBox,
                             QListWidget, QAbstractItemView, QInputDialog, QFrame,
                             QScrollArea, QSizePolicy, QMenu, QDialog, QRadioButton, 
                             QButtonGroup, QTextEdit, QTabWidget, QListWidgetItem,
                             QComboBox, QStackedWidget)

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QUrl, QMimeData, QTimer, QItemSelectionModel
from PyQt6.QtGui import QFont, QColor, QIcon, QAction

# --- FPDF & PANDAS (Вместо ReportLab) ---
try:
    from fpdf import FPDF
    FPDF_AVAIL = True
except ImportError:
    FPDF_AVAIL = False

# --- ИМПОРТ СКАНЕРА ---
try:
    from miner_scanner.core import scan_network_range
    from miner_scanner.runtime import ScanOptions
    from miner_scanner.ranges import expand_ranges
    from miner_scanner.service import default_service
    from miner_scanner.commands import execute_command
    from miner_scanner.models import Credentials
    SCANNER_AVAIL = True
except ImportError:
    try:
        from core import scan_network_range
        SCANNER_AVAIL = True
    except ImportError:
        SCANNER_AVAIL = False
        print("Warning: Scanner core not found.")

# --- ИМПОРТ ACTIONS ---
try:
    from miner_scanner.handlers.miner_actions import send_command
    ACTIONS_AVAIL = True
except ImportError:
    try:
        from handlers.miner_actions import send_command
        ACTIONS_AVAIL = True
    except ImportError:
        ACTIONS_AVAIL = False

# Writable preferences are separate from PyInstaller's temporary resource directory.
APP_DATA_DIR = data_directory()
LEGACY_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(current_dir)
CONFIG_FILE = APP_DATA_DIR / "ip_ranges.json"
SETTINGS_FILE = APP_DATA_DIR / "app_settings.json"
APP_TITLE = "ASIC Monitor"


def load_app_settings():
    return normalize(load_json(SETTINGS_FILE, LEGACY_DIR / "app_settings.json", {}))


def save_app_settings(settings):
    write_json(SETTINGS_FILE, normalize(settings))


VER = f"{CURRENT_VERSION}"  # Теперь версия в заголовке окна будет браться автоматически из CURRENT_VERSION

# ==========================================
# ДИАЛОГ ЛОГОВ ПРОГРАММЫ
# ==========================================
class LogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Журнал событий (Logs)")
        self.resize(650, 400)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap) # Чтобы строчки не ломались
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        
        btn_save = QPushButton("💾 Сохранить в файл")
        btn_save.clicked.connect(self.save_log)
        
        btn_clear = QPushButton("🗑 Очистить лог")
        btn_clear.clicked.connect(self.text_edit.clear)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_clear)
        layout.addLayout(btn_layout)
        
    def append_log(self, text):
        self.text_edit.append(text)
        # Автоскролл в самый низ при добавлении
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить лог", "ASIC_Scanner_Log.txt", "Text Files (*.txt)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.text_edit.toPlainText())
                QMessageBox.information(self, "Успех", "Лог успешно сохранен!")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")

# ==========================================
# ДИАЛОГ КОМАНД (REMOTE CTRL)
# ==========================================
class CommandDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remote Control Panel")
        self.setFixedSize(350, 240)
        self.selected_action = None
        
        layout = QVBoxLayout(self)
        
        # Заголовок
        lbl_info = QLabel(f"Selected Devices: {count}")
        lbl_info.setStyleSheet("font-size: 16px; font-weight: bold; color: #0069D9; margin-bottom: 10px;")
        lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_info)
        
        # Группа кнопок
        self.grp = QButtonGroup(self)
        
        self.rb_led_blink = QRadioButton("💡 LED: Flash (Locate)")
        self.rb_led_auto = QRadioButton("🌑 LED: Normal (Auto)")
        self.rb_reboot = QRadioButton("⚡ Reboot Device")
        
        self.rb_led_blink.setChecked(True)
        
        layout.addWidget(self.rb_led_blink)
        layout.addWidget(self.rb_led_auto)
        layout.addWidget(self.rb_reboot)
        
        self.grp.addButton(self.rb_led_blink)
        self.grp.addButton(self.rb_led_auto)
        self.grp.addButton(self.rb_reboot)
        
        layout.addStretch()
        
        # Кнопки
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("background-color: #DDD; border: 1px solid #CCC; border-radius: 4px; padding: 6px;")
        btn_cancel.clicked.connect(self.reject)
        
        btn_apply = QPushButton("EXECUTE")
        btn_apply.setStyleSheet("background-color: #0069D9; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
        btn_apply.clicked.connect(self.on_apply)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    def on_apply(self):
        if self.rb_reboot.isChecked(): self.selected_action = "reboot"
        elif self.rb_led_blink.isChecked(): self.selected_action = "led_on"
        elif self.rb_led_auto.isChecked(): self.selected_action = "led_off"
        self.accept()

# ==========================================
# ДИАЛОГ РЕДАКТОРА ПОДСЕТЕЙ (IP RANGE EDITOR)
# ==========================================
class IPRangeDialog(QDialog):
    def __init__(self, name="", ranges=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Диапазон сети")
        self.resize(500, 410)
        self.setMinimumSize(420, 360)
        
        layout = QVBoxLayout(self)
        
        # Название сети
        lbl_name = QLabel("Название сети")
        lbl_name.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_name)
        
        self.le_name = QLineEdit(name)
        self.le_name.setPlaceholderText("Например: Клиент А")
        layout.addWidget(self.le_name)
        
        layout.addSpacing(10)
        
        # Диапазоны (многострочное поле)
        lbl_ranges = QLabel("IP-адреса и диапазоны — каждый с новой строки")
        lbl_ranges.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_ranges)
        
        self.te_ranges = QTextEdit()
        self.te_ranges.setPlaceholderText("192.168.1.1-254\n10.10.33.0/24\n192.168.2.10")
        
        if ranges:
            if isinstance(ranges, list):
                self.te_ranges.setPlainText("\n".join(ranges))
            else:
                self.te_ranges.setPlainText(ranges.replace(",", "\n"))
                
        layout.addWidget(self.te_ranges)
        
        layout.addSpacing(10)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        
        btn_save = QPushButton("Сохранить")
        btn_save.setStyleSheet("background-color: #0069D9; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.validate_and_accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)
        
    def validate_and_accept(self):
        name, ranges = self.get_data()
        if not name or not ranges:
            QMessageBox.warning(self, "Диапазон сети", "Укажите название и хотя бы один диапазон.")
            return
        try:
            expand_ranges(ranges)
        except ValueError as exc:
            QMessageBox.warning(self, "Диапазон сети", str(exc))
            return
        self.accept()

    def get_data(self):
        name = self.le_name.text().strip()
        # Разбиваем текст на строки и удаляем пустые
        raw_ranges = self.te_ranges.toPlainText().split('\n')
        ranges = [r.strip() for r in raw_ranges if r.strip()]
        return name, ranges

# ==========================================
# WORKER: СКАНЕР
# ==========================================
class ScanWorker(QThread):
    progress_signal = pyqtSignal(int, int)
    result_signal = pyqtSignal(list)
    log_signal = pyqtSignal(str)

    def __init__(self, ranges, filters, timeout=2, workers=64):
        super().__init__()
        self.ranges = ranges
        self.filters = filters
        self.cancel = Event()
        self.options = ScanOptions(read_timeout=float(timeout), workers=int(workers))
        self.failure = None

    def run(self):
        try:
            scan_network_range(
                self.ranges, target_makes=self.filters, cancel=self.cancel,
                options=self.options,
                on_result=lambda row: self.result_signal.emit([row]),
                on_progress=self.progress_signal.emit,
                on_error=lambda ip, code: self.log_signal.emit(f"{ip}: {code}"),
            )
        except Exception as exc:
            self.failure = type(exc).__name__
            self.log_signal.emit(f"Ошибка сканирования: {self.failure}")

    def stop(self):
        self.cancel.set()


class ActionWorker(QThread):
    log_signal = pyqtSignal(str)

    def __init__(self, targets, action_type, experimental_ids=()):
        super().__init__()
        self.targets = targets
        self.action = action_type
        self.experimental_ids = frozenset(experimental_ids)

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="asic-control") as executor:
            futures = {
                executor.submit(execute_command, default_service(), row["IP"], self.action,
                                device_id=row.get("DeviceId"), allow_unverified=row.get("DeviceId") in self.experimental_ids): row["IP"]
                for row in self.targets
            }
            for future in as_completed(futures):
                ip = futures[future]
                try:
                    result = future.result()
                    self.log_signal.emit(f"{ip}: [{result.status}] {result.message}")
                except Exception as exc:
                    self.log_signal.emit(f"{ip}: ошибка управления ({type(exc).__name__})")


class PDFReport(FPDF):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        self.report_font = "Helvetica"
        if (fonts / "segoeui.ttf").exists():
            self.add_font("Report", "", str(fonts / "segoeui.ttf"), uni=True)
            self.add_font("Report", "B", str(fonts / "segoeuib.ttf"), uni=True)
            self.add_font("Report", "I", str(fonts / "segoeuii.ttf"), uni=True)
            self.report_font = "Report"

    def header(self):
        # Главный заголовок
        self.set_font(self.report_font, 'B', 14)
        self.cell(0, 8, f'ASIC Monitor · {CURRENT_VERSION}', 0, 1, 'L')
        self.line(10, 18, 287, 18)
        self.ln(3)
        
        # Название скана
        self.set_font(self.report_font, 'B', 12)
        if hasattr(self, 'report_title'):
            self.cell(0, 6, self.report_title, 0, 1, 'L')
        
        # СВОДКА (Рисуется ТОЛЬКО на первой странице)
        if self.page_no() == 1 and hasattr(self, 'summary_text'):
            self.set_font(self.report_font, '', 9)
            self.multi_cell(0, 5, self.summary_text)
            self.ln(3)
        else:
            self.ln(3)

        # --- ШАПКА ТАБЛИЦЫ (На каждой странице) ---
        self.set_font(self.report_font, 'B', 7)
        self.set_fill_color(0, 51, 153)
        self.set_text_color(255, 255, 255)
        if hasattr(self, 'table_cols') and hasattr(self, 'table_widths'):
            for i, c in enumerate(self.table_cols):
                self.cell(self.table_widths[i], 8, self.fit_text(COLUMNS[c], self.table_widths[i]), 1, 0, 'C', fill=True)
        self.ln()
        
        # Возврат цвета для обычных строк
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.report_font, 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

    def fit_text(self, text, width):
        if self.report_font == "Helvetica":
            text = text.encode("latin-1", "replace").decode("latin-1")
        if self.get_string_width(text) <= width - 2:
            return text
        while text and self.get_string_width(text + "...") > width - 2:
            text = text[:-1]
        return text + "..."

# ==========================================
# ДИАЛОГ НАСТРОЕК ПРОГРАММЫ
# ==========================================
class GeminiApp(QMainWindow):
    def __init__(self, *, settings=None, ranges=None):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{VER}")
        self.resize(1440, 900)
        self.setMinimumSize(1060, 680)
        self.setWindowIcon(QIcon(str(Path(current_dir) / "app.ico")))
        
        self.scan_data = [] 
        self.ranges_config = self.load_config() if ranges is None else ranges
        self.app_settings = load_app_settings() if settings is None else normalize(settings)
        self.dark_mode = self.app_settings["theme"] == "dark" or (self.app_settings["theme"] == "system" and is_system_dark_mode())
        
        # --- ИНИЦИАЛИЗАЦИЯ ОКНА ЛОГОВ ---
        self.log_dialog = LogDialog(self) 
        
        self.stats_timer = QTimer(self)
        self.stats_timer.setSingleShot(True)
        self.stats_timer.setInterval(150)
        self.stats_timer.timeout.connect(self.update_stats)
        self.init_ui()
        self.apply_theme()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        self.create_menu_bar()

        # === SIDEBAR ===
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(260)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(15, 20, 15, 20)
        side_layout.setSpacing(10)

        lbl_logo = QLabel("ASIC Monitor")
        lbl_logo.setObjectName("Logo")
        lbl_logo.setAlignment(Qt.AlignmentFlag.AlignLeft)
        side_layout.addWidget(lbl_logo)
        subtitle = QLabel(f"Локальный мониторинг  /  {CURRENT_VERSION}")
        subtitle.setObjectName("Muted")
        side_layout.addWidget(subtitle)

        self.btn_theme = QPushButton("Сменить тему")
        self.btn_theme.setObjectName("BtnTheme")
        self.btn_theme.clicked.connect(self.toggle_theme)
        side_layout.addWidget(self.btn_theme)
        
        side_layout.addSpacing(10)

        settings_button = QPushButton("Настройки программы")
        settings_button.clicked.connect(self.open_settings_dialog)
        side_layout.addWidget(settings_button)
        access_button = QPushButton("Доступ к ASIC")
        access_button.clicked.connect(self.configure_device_access)
        side_layout.addWidget(access_button)
        side_layout.addSpacing(12)

        header_layout = QHBoxLayout()
        lbl_ranges = QLabel("ДИАПАЗОНЫ СЕТИ")
        lbl_ranges.setObjectName("SectionHeader")
        
        self.chk_all = QCheckBox("Все")
        self.chk_all.setObjectName("ChkAll")
        self.chk_all.stateChanged.connect(self.toggle_all_ranges)
        
        header_layout.addWidget(lbl_ranges)
        header_layout.addStretch()
        header_layout.addWidget(self.chk_all)
        side_layout.addLayout(header_layout)

        self.list_ranges = QListWidget()
        self.list_ranges.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.refresh_ranges_list()
        
        # === ДВОЙНОЙ КЛИК ДЛЯ РЕДАКТИРОВАНИЯ ===
        self.list_ranges.itemDoubleClicked.connect(self.edit_subnet)
        
        side_layout.addWidget(self.list_ranges, 1)
        range_hint = QLabel("Без выбора будут опрошены все диапазоны.")
        range_hint.setObjectName("Muted")
        range_hint.setWordWrap(True)
        side_layout.addWidget(range_hint)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("Добавить")
        btn_add.setObjectName("RangeAction")
        btn_add.clicked.connect(self.add_range_dialog)
        
        # === КНОПКА ИЗМЕНИТЬ ===
        self.btn_edit_subnet = QPushButton("Изменить")
        self.btn_edit_subnet.setObjectName("RangeAction")
        self.btn_edit_subnet.clicked.connect(lambda: self.edit_subnet())
        
        btn_del = QPushButton("Удалить")
        btn_del.setObjectName("RangeAction")
        btn_del.clicked.connect(self.delete_range)
        
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(self.btn_edit_subnet) # Кнопка посередине
        btn_layout.addWidget(btn_del)
        side_layout.addLayout(btn_layout)

        side_layout.addSpacing(15)

        self.btn_scan = QPushButton("Начать сканирование")
        self.btn_scan.setObjectName("BtnScan")
        self.btn_scan.clicked.connect(self.start_scan)
        side_layout.addWidget(self.btn_scan)

        self.btn_stop = QPushButton("Остановить")
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        side_layout.addWidget(self.btn_stop)

        side_layout.addSpacing(12)

        lbl_exp = QLabel("ЭКСПОРТ РЕЗУЛЬТАТОВ")
        lbl_exp.setObjectName("SectionHeader")
        side_layout.addWidget(lbl_exp)

        btn_csv = QPushButton("Excel / CSV")
        btn_csv.clicked.connect(self.export_csv)
        side_layout.addWidget(btn_csv)

        btn_pdf = QPushButton("Отчёт PDF")
        btn_pdf.clicked.connect(self.export_pdf_pro)
        side_layout.addWidget(btn_pdf)

        btn_screenshot = QPushButton("Снимок таблицы")
        btn_screenshot.clicked.connect(self.take_screenshot)
        side_layout.addWidget(btn_screenshot)

        main_layout.addWidget(sidebar)

        # === CONTENT AREA ===
        content = QWidget()
        self.content_panel = content
        content.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Обзор оборудования")
        title.setObjectName("PageTitle")
        caption = QLabel("Устройства, состояние и хешрейт вашей локальной сети")
        caption.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(caption)
        heading.addLayout(title_box)
        heading.addStretch()
        self.scan_state = QLabel("Готов к сканированию")
        self.scan_state.setObjectName("Muted")
        heading.addWidget(self.scan_state)
        content_layout.addLayout(heading)

        # Summary cards
        self.dash_layout = QHBoxLayout()
        self.dash_layout.setSpacing(15)

        # Блок 1: Статусы
        self.box_status = QFrame()
        self.box_status.setObjectName("DashBox")
        self.layout_status = QVBoxLayout(self.box_status)
        self.layout_status.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Блок 2: Производители
        self.box_models = QFrame()
        self.box_models.setObjectName("DashBox")
        self.layout_models = QVBoxLayout(self.box_models)
        self.layout_models.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Блок 3: Хешрейты
        self.box_hashrate = QFrame()
        self.box_hashrate.setObjectName("DashBox")
        self.layout_hashrate = QVBoxLayout(self.box_hashrate)
        self.layout_hashrate.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.dash_layout.addWidget(self.box_status)
        self.dash_layout.addWidget(self.box_models)
        self.dash_layout.addWidget(self.box_hashrate)
        
        # Обворачиваем dash_layout в виджет, а его в QScrollArea
        self.dash_widget = QWidget()
        self.dash_widget.setLayout(self.dash_layout)
        
        self.dash_scroll = QScrollArea()
        self.dash_scroll.setWidgetResizable(True)
        self.dash_scroll.setWidget(self.dash_widget)
        self.dash_scroll.setFixedHeight(156)
        self.dash_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.dash_scroll.setStyleSheet("QScrollArea { background-color: transparent; }")
        
        content_layout.addWidget(self.dash_scroll)

        # Инициализируем пустой дашборд
        self.refresh_dashboard({}, {}, {})

        # 2. CONTROL BAR (Новая панель управления)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 5, 0, 5)
        
        self.btn_select_all = QPushButton("☑ Выделить всё")
        self.btn_select_all.setCheckable(True)
        self.btn_select_all.clicked.connect(self.toggle_select_all)
        
        btn_remote = QPushButton("Управление")
        self.btn_remote = btn_remote
        btn_remote.setEnabled(False)
        btn_remote.setFixedWidth(160)

        
        # Вместо открытия CommandDialog, вешаем сразу выпадающее меню!
        ctrl_menu = QMenu(self)
        ctrl_menu.addAction("Включить подсветку", lambda: self.run_action("led_on"))
        ctrl_menu.addAction("Выключить подсветку", lambda: self.run_action("led_off"))
        ctrl_menu.addAction("Перезагрузить", lambda: self.run_action("reboot"))
        ctrl_menu.addAction("Остановить майнинг", lambda: self.run_action("sleep"))
        ctrl_menu.addAction("Возобновить майнинг", lambda: self.run_action("normal"))
        btn_remote.setMenu(ctrl_menu)
        
        # === НОВЫЙ СЧЕТЧИК ВЫДЕЛЕННЫХ УСТРОЙСТВ ===
        self.lbl_selected_count = QLabel("Выделено: 0")
        self.lbl_selected_count.setObjectName("SelectionCount")
        
        ctrl_layout.addWidget(self.btn_select_all)
        ctrl_layout.addWidget(btn_remote)
        ctrl_layout.addWidget(self.lbl_selected_count) # <--- Добавлен счетчик
        ctrl_layout.addStretch()
        
        content_layout.addLayout(ctrl_layout)

        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по IP, модели, прошивке или воркеру…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.apply_table_filter)
        self.status_filter = QComboBox()
        for text, code in (("Все состояния", ""), ("Работает", "Running"), ("Сон", "Sleep"),
                           ("Ожидание", "WaitWork"), ("Ошибка", "Error"), ("Неизвестно", "Unknown"),
                           ("Устаревшие данные", "stale")):
            self.status_filter.addItem(text, code)
        self.status_filter.currentIndexChanged.connect(self.apply_table_filter)
        filters.addWidget(self.search_input, 1)
        filters.addWidget(self.status_filter)
        content_layout.addLayout(filters)

        # Device table
        cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
        self.table = QTableWidget()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels([COLUMNS[c] for c in cols])
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self.table.itemDoubleClicked.connect(self.open_web_interface)
        
        # === СИНХРОНИЗАЦИЯ ГАЛОЧЕК И ВЫДЕЛЕНИЯ ===
        self.table.itemSelectionChanged.connect(self.sync_checkboxes_with_selection)
        self.table.itemChanged.connect(self.on_item_changed)
        
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(True)
        for index, width in enumerate((145, 180, 110, 120, 145, 130, 135, 155, 130, 135, 240, 170)):
            self.table.setColumnWidth(index, width)

        self.table_stack = QStackedWidget()
        self.table_stack.addWidget(self.table)
        empty = QFrame()
        empty.setObjectName("EmptyState")
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        self.empty_title = QLabel("Добавьте диапазон сети")
        self.empty_title.setObjectName("DialogTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description = QLabel("Укажите IP-адреса устройств и начните сканирование.\nРезультаты появятся здесь по мере обнаружения ASIC.")
        self.empty_description.setObjectName("Muted")
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description.setWordWrap(True)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_description)
        empty_layout.addStretch()
        self.table_stack.addWidget(empty)
        self.table_stack.setCurrentIndex(1)
        content_layout.addWidget(self.table_stack, 1)

        # 4. Footer
        footer = QHBoxLayout()
        self.status_bar = QLabel("Готов к работе")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        
        # Добавляем кнопку логов
        self.btn_logs = QPushButton("Журнал")
        self.btn_logs.setFixedWidth(80)
        self.btn_logs.clicked.connect(self.log_dialog.show)
        
        footer.addWidget(self.status_bar)
        footer.addWidget(self.progress)
        footer.addWidget(self.btn_logs)
        content_layout.addLayout(footer)

        main_layout.addWidget(content)

        # === ДОБАВЛЯЕМ АВТОЗАПУСК ПРОВЕРКИ ОБНОВЛЕНИЙ ===
        if self.app_settings.get("check_updates", False):
            QTimer.singleShot(0, lambda: self.check_for_updates(auto=True))
        self.apply_ui_settings()
        self.update_selected_count()
        if self.ranges_config:
            self.empty_title.setText("Всё готово к сканированию")

    def create_menu_bar(self):
        menubar = self.menuBar()

        # Меню Файл
        file_menu = menubar.addMenu("Файл")

        access_act = QAction("🔑 Доступ к ASIC (на текущий сеанс)", self)
        access_act.triggered.connect(self.configure_device_access)
        file_menu.addAction(access_act)

        export_csv_act = QAction("📄 Экспорт в CSV/Excel", self)
        export_csv_act.triggered.connect(self.export_csv)
        file_menu.addAction(export_csv_act)

        export_pdf_act = QAction("📑 Экспорт в PDF", self)
        export_pdf_act.triggered.connect(self.export_pdf_pro)
        file_menu.addAction(export_pdf_act)

        file_menu.addSeparator()

        exit_act = QAction("🚪 Выход", self)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        # Меню Инструменты
        tools_menu = menubar.addMenu("Инструменты")

        settings_act = QAction("⚙️ Настройки программы...", self)
        settings_act.triggered.connect(self.open_settings_dialog)
        tools_menu.addAction(settings_act)
        settings_act.setShortcut("Ctrl+,")
        help_menu = menubar.addMenu("Справка")
        help_menu.addAction("Проверить обновления", lambda: self.check_for_updates(auto=False))
        help_menu.addAction(f"Что нового в {CURRENT_VERSION}", self.show_changelog)
        search = QAction("Поиск устройств", self)
        search.setShortcut("Ctrl+F")
        search.triggered.connect(lambda: self.search_input.setFocus())
        self.addAction(search)

    def open_settings_dialog(self):
        dlg = SettingsDialog(self.app_settings, self)
        if dlg.exec(): # Если нажали Сохранить
            try:
                save_app_settings(dlg.settings)
            except OSError as exc:
                QMessageBox.warning(self, "Настройки", f"Не удалось сохранить настройки:\n{exc}")
                return
            self.app_settings = dlg.settings
            self.dark_mode = self.app_settings["theme"] == "dark" or (self.app_settings["theme"] == "system" and is_system_dark_mode())
            self.apply_theme()

            self.apply_ui_settings() # <--- ТЕПЕРЬ ОНО ПРИМЕНИТЬСЯ МГНОВЕННО

    def apply_ui_settings(self):
        """Жестко скрывает/показывает столбцы таблицы"""
        all_cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
        ui_cols = self.app_settings.get("ui_cols", all_cols)
        self.table.verticalHeader().setDefaultSectionSize(32 if self.app_settings.get("density") == "compact" else 42)
        
        for i, col in enumerate(all_cols):
            if col in ui_cols:
                self.table.showColumn(i) # Явно показываем
            else:
                self.table.hideColumn(i) # Явно прячем

    def take_screenshot(self):
        """Делает снимок всей правой панели (Итоги + Таблица)"""
        pixmap = self.content_panel.grab()
        
        QApplication.clipboard().setPixmap(pixmap)
        self.status_bar.setText("📸 Скриншот скопирован в буфер обмена!")
        QMessageBox.information(self, "Успех", "Скриншот дашборда и таблицы успешно скопирован в буфер обмена!")

    def add_log(self, message):
        """Добавляет запись в окно логов с отметкой времени"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {message}"
        self.log_dialog.append_log(full_msg)

    def handle_worker_log(self, message):
        """Дублирует сообщения и в статус-бар, и в логи"""
        self.status_bar.setText(message)
        self.add_log(message)
    
    # ==========================================
    # ЛОГИКА АВТООБНОВЛЕНИЯ
    # ==========================================
    def check_for_updates(self, auto=False):
        if getattr(self, "update_worker", None) and self.update_worker.isRunning():
            return
        self.status_bar.setText("Проверяем обновления…")
        self.update_worker = UpdateCheckWorker(UPDATE_INFO_URL, self)
        self.update_worker.result.connect(lambda data, error: self.on_update_checked(data, error, auto))
        self.update_worker.start()

    def on_update_checked(self, data, error, auto):
        if error:
            self.add_log("Не удалось проверить обновления: " + error)
            self.status_bar.setText("Сервер обновлений недоступен")
            if not auto:
                QMessageBox.warning(self, "Обновление", "Не удалось проверить обновления. Проверьте подключение к интернету.")
            return
        latest_version = data["version"]
        if version_tuple(latest_version) > version_tuple(CURRENT_VERSION):
            self.status_bar.setText(f"Доступна версия {latest_version}")
            reply = QMessageBox.question(self, "Доступно обновление",
                f"Новая версия: {latest_version}. Установлена: {CURRENT_VERSION}.\n\n{data.get('changelog', '')}\n\nОткрыть скачивание в браузере?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.apply_update(data["url"])
        else:
            self.status_bar.setText(f"Установлена актуальная версия {CURRENT_VERSION}")
            if not auto:
                QMessageBox.information(self, "Обновление", f"Установлена актуальная версия {CURRENT_VERSION}.")

    def apply_update(self, download_url):
        webbrowser.open(download_url)

    def show_changelog(self):
        manifest = load_json(Path(current_dir) / "version.json", default={})
        message = QMessageBox(self)
        message.setWindowTitle(f"Что нового · {CURRENT_VERSION}")
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setText(manifest.get("changelog", f"ASIC Monitor {CURRENT_VERSION}"))
        message.exec()

    # --- MENU & ACTIONS LOGIC ---
    def open_remote_panel(self):
        # Получаем выбранные строки
        rows = sorted(set(i.row() for i in self.table.selectedItems()))
        if not rows:
            QMessageBox.warning(self, "No Selection", "Please select devices from the table first.")
            return

        # Открываем диалог
        dlg = CommandDialog(len(rows), self)
        if dlg.exec(): # Если нажали EXECUTE
            action = dlg.selected_action
            if action:
                self.run_action(action, confirm_needed=True)

    def toggle_select_all(self):
        """Галочка Выделить все / Снять выделение"""
        is_checked = self.btn_select_all.isChecked()
        self.table.blockSignals(True)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            selected = is_checked and not self.table.isRowHidden(row)
            if item:
                item.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
            if selected:
                self.table.selectionModel().select(self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.table.blockSignals(False)
        self.update_selected_count()

    def sync_checkboxes_with_selection(self):
        """Синхронизирует галочки с выделением строк мышкой/шифтом"""
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                # Если ячейка выделена синим - ставим галочку, иначе снимаем
                if item.isSelected() and not self.table.isRowHidden(row):
                    item.setCheckState(Qt.CheckState.Checked)
                else:
                    item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.update_selected_count()

    def on_item_changed(self, item):
        """Срабатывает при ручном клике по чекбоксу"""
        if item.column() == 0:
            self.table.blockSignals(True)
            is_checked = (item.checkState() == Qt.CheckState.Checked)
            # Выделяем или снимаем выделение со всей строки
            for col in range(self.table.columnCount()):
                cell = self.table.item(item.row(), col)
                if cell:
                    cell.setSelected(is_checked)
            self.table.blockSignals(False)
            self.update_selected_count()

    def update_selected_count(self):
        """Обновляет счетчик устройств и меняет его цвет"""
        count = sum(1 for row in range(self.table.rowCount()) 
                    if self.table.item(row, 0) and self.table.item(row, 0).checkState() == Qt.CheckState.Checked)
        
        self.lbl_selected_count.setText(f"Выделено: {count}")
        
        visible = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        self.btn_remote.setEnabled(count > 0)
        self.btn_select_all.setEnabled(visible > 0)
        all_selected = count > 0 and count == visible
        self.btn_select_all.setChecked(all_selected)
        self.btn_select_all.setText("Снять выделение" if all_selected else "Выделить всё")

    def show_context_menu(self, pos):
        # Контекстное меню (ПКМ) в таблице
        menu = QMenu()
        menu.addAction("🔑 Настроить доступ к выбранным ASIC", self.configure_device_access)
        menu.addAction("Включить подсветку", lambda: self.run_action("led_on"))
        menu.addAction("Выключить подсветку", lambda: self.run_action("led_off"))
        menu.addAction("💡 Переключить индикацию (Avalon)", lambda: self.run_action("identify_toggle"))
        menu.addAction("Перезагрузить", lambda: self.run_action("reboot"))
        menu.addAction("Остановить майнинг", lambda: self.run_action("sleep"))
        menu.addAction("Возобновить майнинг", lambda: self.run_action("normal"))
        for action, label in (("low", "Режим Low"), ("normal_power", "Обычный режим мощности"), ("hem", "Режим HEM")):
            item = menu.addAction(label, lambda checked=False, action=action: self.run_action(action))
            selected = [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 1) for r in range(self.table.rowCount()) if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]
            item.setEnabled(any(row and row.get("Capabilities", {}).get(action) == "supported" for row in selected))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def run_action(self, action_type, confirm_needed=True):
        
        # Собираем отмеченные галочками строки!
        rows = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                rows.append(r)
        
        if not rows:
            QMessageBox.warning(self, "Внимание", "Сначала отметьте галочками устройства в таблице!")
            return

        nice_names = {"reboot": "Перезагрузить", "led_on": "Подсветить", "led_off": "Отключить подсветку", "sleep": "Остановить майнинг", "normal": "Возобновить майнинг", "identify_toggle": "Переключить индикацию", "low": "Режим Low", "normal_power": "Обычный режим мощности", "hem": "Режим HEM"}

        targets = [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 1) for r in rows]
        targets = [row for row in targets if row]
        if not targets:
            return

        consequences = ""
        if action_type == "normal" and any(row.get("ProfileId") == "canaan.avalon" for row in targets):
            consequences = "\nДля Avalon возобновление выполняется через перезагрузку."
        
        if confirm_needed:
            confirm = QMessageBox.question(
                self, "Подтверждение", 
                f"Выполнить '{nice_names.get(action_type, action_type)}' для {len(rows)} устройств?{consequences}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes: return

        self.add_log(f"🛠 Отправка команды '{action_type}' на {len(rows)} устройств...") 
        
        worker = ActionWorker(targets, action_type, getattr(self, "experimental_device_ids", set()))
        worker.log_signal.connect(self.handle_worker_log)
        if not hasattr(self, 'workers'):
            self.workers = []
        self.workers.append(worker)
        def release_worker():
            if worker in self.workers:
                self.workers.remove(worker)
            worker.deleteLater()
        worker.finished.connect(release_worker)
        worker.start()

    def configure_device_access(self):
        targets = [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 1)
                   for r in range(self.table.rowCount())
                   if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]
        dialog = QDialog(self)
        dialog.setWindowTitle("Доступ к выбранным ASIC — на текущий сеанс" if targets else "Доступ для сканирования — на текущий сеанс")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Логин"))
        username = QLineEdit()
        layout.addWidget(username)
        layout.addWidget(QLabel("Пароль (не сохраняется на диск)"))
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(password)
        auth = QComboBox()
        auth.addItems(["digest", "basic"])
        layout.addWidget(QLabel("HTTP-авторизация (для RPC используется логин выше)"))
        layout.addWidget(auth)
        experimental = QCheckBox("Разрешить перенесённые команды без аппаратного подтверждения совместимости")
        experimental.setEnabled(bool(targets))
        experimental.setChecked(all(row and row.get("DeviceId") in getattr(self, "experimental_device_ids", set()) for row in targets))
        layout.addWidget(experimental)
        button = QPushButton("Применить к выбранным" if targets else "Использовать при сканировании")
        button.clicked.connect(dialog.accept)
        layout.addWidget(button)
        if dialog.exec():
            if not username.text().strip():
                QMessageBox.warning(self, "Доступ", "Укажите логин")
                return
            credentials = Credentials(username.text().strip(), password.text(), auth.currentText())
            if not targets:
                default_service().set_default_credentials(credentials)
            if not hasattr(self, "experimental_device_ids"):
                self.experimental_device_ids = set()
            for row in targets:
                if row:
                    default_service().set_credentials(row["IP"], credentials)
                    if experimental.isChecked():
                        self.experimental_device_ids.add(row.get("DeviceId"))
                    else:
                        self.experimental_device_ids.discard(row.get("DeviceId"))
            self.add_log(f"Доступ настроен для {len(targets)} устройств на текущий сеанс" if targets else "Учётные данные для сканирования заданы на текущий сеанс")

    # --- ОСТАЛЬНЫЕ ФУНКЦИИ (Config, Scan, Export) ---
    def load_config(self):
        data = load_json(CONFIG_FILE, LEGACY_DIR / "ip_ranges.json", [])
        if isinstance(data, dict):
            data = [{"name": name, "ranges": ranges if isinstance(ranges, list) else [ranges]}
                    for name, ranges in data.items()]
        result = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            ranges = item.get("ranges", [item["range"]] if "range" in item else [])
            if isinstance(ranges, str):
                ranges = [ranges]
            if isinstance(ranges, list) and all(isinstance(r, str) for r in ranges):
                result.append({"name": str(item.get("name", "Сеть")), "ranges": ranges})
        return result

    def save_config(self):
        try:
            write_json(CONFIG_FILE, self.ranges_config)
        except Exception as e: QMessageBox.critical(self, "Error", str(e))

    def refresh_ranges_list(self):
        self.list_ranges.clear()
        for idx, r in enumerate(self.ranges_config):
            name = r.get('name', '?')
            ranges = r.get('ranges', [])
            
            # Красивое отображение в списке
            if len(ranges) > 1:
                display_text = f"{name} ({len(ranges)} диапазонов)"
            else:
                display_text = f"{name} ({ranges[0] if ranges else 'Пусто'})"
                
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, idx) # Надежно прячем индекс внутри элемента
            self.list_ranges.addItem(item)
    def toggle_all_ranges(self, state):
        if state == 2: self.list_ranges.selectAll()
        else: self.list_ranges.clearSelection()

    def add_range_dialog(self):
        dlg = IPRangeDialog(parent=self)
        if dlg.exec():
            name, ranges = dlg.get_data()
            if name and ranges:
                self.ranges_config.append({"name": name, "ranges": ranges})
                self.save_config()
                self.refresh_ranges_list()

    def delete_range(self):
        rows = self.list_ranges.selectedIndexes()
        for r in sorted(rows, reverse=True): del self.ranges_config[r.row()]
        self.save_config()
        self.refresh_ranges_list()

    
    # === ФУНКЦИЯ РЕДАКТИРОВАНИЯ ПОДСЕТИ ===
    def edit_subnet(self, item=None):
        """Открывает окно IPRangeDialog для редактирования"""
        if item is None:
            selected = self.list_ranges.selectedItems()
            if not selected:
                QMessageBox.warning(self, "Внимание", "Сначала выберите подсеть для редактирования!")
                return
            item = selected[0]
            
        # Достаем индекс из элемента
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self.ranges_config): return
        
        r_data = self.ranges_config[idx]
        old_name = r_data.get("name", "")
        old_ranges = r_data.get("ranges", [])
        
        # Открываем редактор с предзаполненными данными
        dlg = IPRangeDialog(name=old_name, ranges=old_ranges, parent=self)
        if dlg.exec():
            new_name, new_ranges = dlg.get_data()
            if new_name and new_ranges:
                self.ranges_config[idx]["name"] = new_name
                self.ranges_config[idx]["ranges"] = new_ranges
                self.save_config()
                self.refresh_ranges_list()
    # ======================================    

    def start_scan(self):
        if getattr(self, "worker", None) and self.worker.isRunning():
            return
        sel = self.list_ranges.selectedItems()
        to_scan = []
        scan_names = []

        # Теперь мы читаем данные напрямую из конфига, а не из текста кнопки!
        if not sel: 
            for r in self.ranges_config:
                to_scan.extend(r.get('ranges', []))
            self.last_scan_name = "All_Ranges"
        else:
            for item in sel:
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None and idx < len(self.ranges_config):
                    r_data = self.ranges_config[idx]
                    to_scan.extend(r_data.get('ranges', []))
                    scan_names.append(r_data.get('name', 'Net'))
            self.last_scan_name = "_".join(scan_names)

        if not to_scan: 
            QMessageBox.warning(self, "Диапазоны", "Добавьте диапазон IP-адресов слева.")
            return

        # === СОБИРАЕМ ВЫБРАННОЕ ОБОРУДОВАНИЕ ИЗ НАСТРОЕК ===
        target_filters = []
        if self.app_settings.get("scan_bitmain", True): target_filters.append("Bitmain")
        if self.app_settings.get("scan_whatsminer", True): target_filters.append("MicroBT")
        if self.app_settings.get("scan_elphapex", True): target_filters.append("Elphapex")
        if self.app_settings.get("scan_other", True): target_filters.extend(["Canaan", "iPollo", "Jasminer"])

        if not target_filters:
            QMessageBox.warning(self, "Ошибка", "В настройках отключены все типы оборудования!")
            return

        try:
            expand_ranges(to_scan)
        except ValueError as exc:
            QMessageBox.warning(self, "Диапазоны", str(exc))
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.btn_select_all.setChecked(False)
        self.btn_select_all.setText("Выделить всё")
        self.update_selected_count()
        self.table_stack.setCurrentIndex(1)
        self.empty_title.setText("Идёт поиск устройств")
        self.empty_description.setText("Результаты появятся по мере получения ответов. Сканирование можно остановить.")
        self.scan_state.setText("Сканирование…")
        # ВАЖНО: Прячем столбцы СРАЗУ ПОСЛЕ очистки таблицы, чтобы они не появились снова!
        self.apply_ui_settings() 
        self.scan_data = []
        self.refresh_dashboard({}, {}, {})
        
        self.worker = ScanWorker(to_scan, target_filters, self.app_settings.get("timeout", 2), self.app_settings.get("workers", 64))
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.result_signal.connect(self.on_result)
        self.worker.log_signal.connect(self.handle_worker_log)
        self.worker.finished.connect(self.on_finished)
        
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)

        self.scan_start_time = time.perf_counter()
        self.add_log(f"🚀 Начато сканирование. Выбрано диапазонов: {len(to_scan)}")
        
        self.worker.start()

    def stop_scan(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.scan_state.setText("Остановка…")
            self.status_bar.setText("Завершаем текущие запросы. Найденные устройства сохранятся.")

    def on_progress(self, curr, total):
        self.progress.setValue(int((curr / total) * 100) if total else 0)
        self.status_bar.setText(f"Проверено адресов: {curr} из {total} · Найдено: {len(self.scan_data)}")

    def on_result(self, res_list):
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        for row in res_list:
            # === ФИКС ДЛЯ JASMINER, IPOLLO И AVALON ===
            # Принудительно задаем статус в базу, если парсер его не вернул
            if 'Status' not in row:
                row['Status'] = 'Unknown'
            if 'Error' not in row:
                row['Error'] = ''
            # =========================================
            
            self.scan_data.append(row)
            r = self.table.rowCount()
            self.table.insertRow(r)
            
            # УМНЫЙ КЛАСС СОРТИРОВКИ ЯЧЕЕК
            class SmartSortItem(QTableWidgetItem):
                def __lt__(self, other):
                    # Проверяем, есть ли скрытое числовое значение (UserRole)
                    my_val = self.data(Qt.ItemDataRole.UserRole)
                    other_val = other.data(Qt.ItemDataRole.UserRole)
                    if my_val is not None and other_val is not None:
                        return my_val < other_val
                        
                    # Иначе пытаемся вытащить число из начала строки (например, "15.5 TH/s" -> 15.5)
                    try: return float(self.text().split()[0]) < float(other.text().split()[0])
                    except: return self.text() < other.text()

            # === 0. IP (Сортировка по сырому числовому IP) ===
            ip_str = str(row.get('IP', ''))
            ip_item = SmartSortItem(ip_str)
            ip_item.setFlags(ip_item.flags() | Qt.ItemFlag.ItemIsUserCheckable) # <--- ДОБАВИТЬ
            ip_item.setCheckState(Qt.CheckState.Unchecked)                      # <--- ДОБАВИТЬ
            ip_item.setData(Qt.ItemDataRole.UserRole, row.get('SortIP', 0))
            ip_item.setData(Qt.ItemDataRole.UserRole + 1, row)
            ip_item.setToolTip(f"Прошивка: {row.get('Firmware', 'Unknown')} {row.get('FirmwareVersion') or ''}\nПрофиль: {row.get('ProfileId', 'Unknown')}\nДанные: {'устарели' if row.get('Stale') else 'актуальны'}")
            self.table.setItem(r, 0, ip_item)
            
            # === 1, 2. Model, Algo ===
            self.table.setItem(r, 1, QTableWidgetItem(str(row.get('Model', ''))))
            self.table.setItem(r, 2, QTableWidgetItem(str(row.get('Algo', '-'))))
            
            # === 3. Status ===
            status_str = str(row.get('Status', 'Running'))
            status_item = QTableWidgetItem({"Running": "Работает", "Sleep": "Сон", "WaitWork": "Ожидание", "Error": "Ошибка", "Unknown": "Неизвестно"}.get(status_str, status_str))
            status_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            if status_str == "Running":
                status_item.setForeground(QColor("#00E676") if getattr(self, 'dark_mode', False) else QColor("#007e33"))
            elif status_str == "WaitWork":
                status_item.setForeground(QColor("#FFA000"))
            self.table.setItem(r, 3, status_item)
            
            # === 4. Error ===
            err_str = str(row.get('Error', ''))
            err_item = QTableWidgetItem(err_str)
            if err_str and err_str != '-':
                err_item.setForeground(QColor("#FF4444"))
                err_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                details = str(row.get('ErrorDetails', ''))
                if details:
                    err_item.setToolTip(details)
            self.table.setItem(r, 4, err_item)
            
            # === 5. Uptime (Переводим дни и часы в минуты для математической сортировки) ===
            up_str = str(row.get('Uptime', ''))
            up_minutes = 0
            
            # Умный и безопасный поиск чисел, даже если они написаны слитно
            d_match = re.search(r'(\d+)d', up_str)
            h_match = re.search(r'(\d+)h', up_str)
            m_match = re.search(r'(\d+)m', up_str)
            
            if d_match: up_minutes += int(d_match.group(1)) * 1440
            if h_match: up_minutes += int(h_match.group(1)) * 60
            if m_match: up_minutes += int(m_match.group(1))
            
            up_item = SmartSortItem(up_str)
            up_item.setData(Qt.ItemDataRole.UserRole, up_minutes)
            self.table.setItem(r, 5, up_item)
            
            # === 6. Real HR ===
            hr = str(row.get('Real', '0'))
            hr_item = SmartSortItem(hr)
            if getattr(self, 'dark_mode', False): 
                hr_item.setForeground(QColor("#00E676"))
            else: 
                hr_item.setForeground(QColor("#007e33")) 
            
            # Мы удалили hr_item.setData(...), чтобы сортировка работала по тексту (как в Avg HR)
            self.table.setItem(r, 6, hr_item)
            
            # === 7, 8, 9, 10, 11 (Остальные) ===
            self.table.setItem(r, 7, SmartSortItem(str(row.get('Avg'))))
            self.table.setItem(r, 8, SmartSortItem(str(row.get('Temp'))))
            self.table.setItem(r, 9, QTableWidgetItem(str(row.get('Fan'))))
            self.table.setItem(r, 10, QTableWidgetItem(str(row.get('Pool'))))
            self.table.setItem(r, 11, QTableWidgetItem(str(row.get('Worker', '-'))))

        self.table.blockSignals(False)
        self.table.setSortingEnabled(sorting)
        self.apply_table_filter()
        if not self.stats_timer.isActive():
            self.stats_timer.start()

    def open_web_interface(self, item):
        """Открывает веб-интерфейс асика в браузере по двойному клику на IP"""
        # Получаем таблицу, в которой кликнули
        table = item.tableWidget()
        
        # Проверяем, как называется заголовок колонки, по которой кликнули
        header_item = table.horizontalHeaderItem(item.column())
        
        # Если кликнули именно по колонке "IP"
        if item.column() == 0:
            ip_address = item.text().strip()
            # Открываем дефолтный браузер
            webbrowser.open(f"http://{ip_address}")

    def on_finished(self):
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.table.setSortingEnabled(True)
        
        # --- ОСТАНОВКА ТАЙМЕРА ---
        elapsed = time.perf_counter() - getattr(self, 'scan_start_time', time.perf_counter())
        cancelled = getattr(getattr(self, "worker", None), "cancel", Event()).is_set()
        failed = getattr(getattr(self, "worker", None), "failure", None)
        state = "Ошибка сканирования" if failed else "Остановлено" if cancelled else "Сканирование завершено"
        msg = f"{state} · Найдено: {len(self.scan_data)} · Время: {elapsed:.1f} с"
        self.scan_state.setText(state)
        if not self.scan_data:
            self.empty_title.setText("Устройства не найдены" if not failed else "Не удалось завершить сканирование")
            self.empty_description.setText("Проверьте диапазоны, подключение к локальной сети и доступ к ASIC. Подробности — в журнале.")
        self.stats_timer.stop()
        self.update_stats()
        self.apply_table_filter()
        self.status_bar.setText(msg)
        self.add_log(f"⏱️ {msg}") 
        
        # --- АНАЛИТИКА: СРЕДНЕЕ ВРЕМЯ ОПРОСА И КОЛИЧЕСТВО ---
        if self.scan_data:
            df = pd.DataFrame(self.scan_data)
            if 'ScanTime' in df.columns and 'Make' in df.columns:
                # Группируем, считаем среднее (mean) и количество (count)
                grouped = df.groupby('Make')['ScanTime'].agg(['mean', 'count'])
                
                # Формируем красивую строку: "Bitmain: 4.454s (10 шт.)"
                log_text = " | ".join([f"{make}: {row['mean']:.3f}s ({int(row['count'])} шт.)" 
                                      for make, row in grouped.iterrows()])
                self.add_log(f"📊 Аналитика отклика: {log_text}")
                
        self.apply_ui_settings()

    def update_stats(self):
        states = Counter(row.get("Status", "Unknown") for row in self.scan_data)
        statuses = {"Всего устройств": {"val": str(len(self.scan_data)), "color": None}}
        for code, label in (("Running", "Работает"), ("Sleep", "Сон"), ("WaitWork", "Ожидание"),
                            ("Error", "Ошибка"), ("Unknown", "Неизвестно")):
            if states[code]:
                statuses[label] = {"val": str(states[code]), "color": None}
        stale = sum(bool(row.get("Stale")) for row in self.scan_data)
        if stale:
            statuses["Устаревшие данные"] = {"val": str(stale), "color": None}
        models = {name: {"val": str(count), "color": None} for name, count in
                  Counter(str(row.get("Model") or "Неизвестная модель") for row in self.scan_data).most_common()}
        totals = defaultdict(float)
        for row in self.scan_data:
            if row.get("Stale"):
                continue
            match = re.fullmatch(r"([\d.,]+)\s+(\S+)", str(row.get("Real", "")))
            if match:
                totals[(str(row.get("Algo") or "Неизвестно"), match[2])] += float(match[1].replace(",", "."))
        # Do not add different units or turn unavailable measurements into a zero.
        rates = {f"{algo} · {unit}": {"val": f"{value:,.2f}", "color": None}
                 for (algo, unit), value in totals.items()}
        self.refresh_dashboard(statuses, models, rates)

    def refresh_dashboard(self, statuses, models, hashrates):
        summaries = (
            (self.layout_status, "Устройства", str(len(self.scan_data)),
             " · ".join(f"{key}: {value['val']}" for key, value in statuses.items() if key != "Всего устройств") or "Ожидание результатов"),
            (self.layout_models, "Модели", str(len(models)),
             " · ".join(f"{key}: {value['val']}" for key, value in models.items()) or "Появятся после сканирования"),
            (self.layout_hashrate, "Хешрейт", next(iter(hashrates.values()))["val"] if len(hashrates) == 1 else "—" if not hashrates else f"{len(hashrates)} групп",
             " · ".join(f"{key}: {value['val']}" for key, value in hashrates.items()) or "Нет актуальных измерений"),
        )
        for layout, title, value, detail in summaries:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().deleteLater()
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(4)
            heading = QLabel(title)
            heading.setObjectName("DashTitle")
            number = QLabel(value)
            number.setObjectName("CardValue")
            description = QLabel(detail)
            description.setObjectName("Muted")
            description.setWordWrap(True)
            description.setFixedHeight(38)
            description.setToolTip(detail)
            layout.addWidget(heading)
            layout.addWidget(number)
            layout.addWidget(description)

    def export_csv(self):
        if not self.scan_data: 
            QMessageBox.warning(self, "Экспорт", "Сначала выполните сканирование.")
            return

        try:
            # --- ПУТЬ СОХРАНЕНИЯ ИЗ НАСТРОЕК ---
            export_csv_dir = self.app_settings.get("export_csv_dir", "")
            export_csv_dir = export_csv_dir or str(APP_DATA_DIR / "export_csv")
            Path(export_csv_dir).mkdir(parents=True, exist_ok=True)

            base_name = getattr(self, "last_scan_name", "Manual_Scan")
            clean_name = re.sub(r"[\\/*?:\"<>|]", "", base_name)
            if len(clean_name) > 50: clean_name = clean_name[:50]

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            
            # Use QFileDialog for file type selection (CSV or XLSX)
            file_filter = "Excel (*.xlsx);;CSV (*.csv)"
            chosen_path, selected_filter = QFileDialog.getSaveFileName(self, "Сохранить отчет",
                                                                       os.path.join(export_csv_dir, f"{clean_name}_{timestamp}.xlsx"), # Default to XLSX
                                                                       file_filter)
            
            if not chosen_path: # User cancelled
                return

            full_path = chosen_path
            
            columns = [column for column in COLUMNS if column in self.app_settings.get("csv_cols", COLUMNS)]
            df = export_frame(self.scan_data, columns, self.app_settings.get("csv_sort", "IP"))
            if not Path(full_path).suffix:
                full_path += ".csv" if "*.csv" in selected_filter else ".xlsx"

            if full_path.lower().endswith(".csv"):
                df.to_csv(full_path, index=False, encoding="utf-8-sig")
            else: # .xlsx
                df.to_excel(full_path, index=False, engine="openpyxl")

            # --- ЛОГИКА БУФЕРА ОБМЕНА ---
            if self.app_settings.get("copy_csv", False):
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(full_path)])
                QApplication.clipboard().setMimeData(mime_data)
                QMessageBox.information(self, "Успех", f"Отчет сохранен и СКОПИРОВАН в буфер обмена!\n{os.path.basename(full_path)}")
            else:
                QMessageBox.information(self, "Успех", f"Отчет сохранен:\n{os.path.basename(full_path)}")

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            QMessageBox.critical(self, "CSV/Excel Error", f"{str(e)}\n\nDetails:\n{error_details}")

    def export_pdf_pro(self):
        if not FPDF_AVAIL:
            QMessageBox.critical(self, "Error", "FPDF library not installed!\nRun: pip install fpdf")
            return
            
        if not self.scan_data: 
            QMessageBox.warning(self, "Экспорт", "Сначала выполните сканирование.")
            return

        try:
            # --- ПУТЬ СОХРАНЕНИЯ ИЗ НАСТРОЕК ---
            export_dir = self.app_settings.get("export_dir", "")
            export_dir = export_dir or str(APP_DATA_DIR / "export_pdf")
            Path(export_dir).mkdir(parents=True, exist_ok=True)

            base_name = getattr(self, 'last_scan_name', 'Manual_Scan')
            clean_name = re.sub(r'[\\/*?:"<>|]', "", base_name)
            if len(clean_name) > 50: clean_name = clean_name[:50]

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = f"{clean_name}_{timestamp}.pdf"
            full_path = os.path.join(export_dir, filename)

            def safe_text(text):
                return re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(text))

            df = sorted_frame(self.scan_data, self.app_settings.get("pdf_sort", "IP"))

            # --- РАСЧЕТ ИТОГОВОЙ СВОДКИ ДЛЯ ШАПКИ ---
            total_dev = len(df)
            df['CleanedModel'] = df['Model'].replace('', pd.NA).dropna().apply(lambda x: re.sub(r'\(.*?\)', '', str(x)).strip())
            models_c = df['CleanedModel'].value_counts()
            models_str = " | ".join([f"{k}: {v}" for k, v in models_c.items()])
            
            algos_c = df['Algo'].replace('', pd.NA).dropna().value_counts()
            algos_str = " | ".join([f"{k}: {v}" for k, v in algos_c.items()])
            
            status_c = df['Status'].replace('', pd.NA).dropna().value_counts()
            status_str = " | ".join([f"{k}: {v}" for k, v in status_c.items()])

            # НОВЫЙ КОД ДЛЯ ХЕШРЕЙТА ПО АЛГОРИТМАМ
            hashrates_summary = {}
            if 'Algo' in df.columns and 'Real' in df.columns:
                df['Algo_Upper'] = df['Algo'].astype(str).str.upper()
                algos = df['Algo_Upper'].dropna().unique()
                
                for algo in algos:
                    if algo in ['NAN', 'UNKNOWN', ''] or not algo: continue
                    sub = df[df['Algo_Upper'] == algo]
                    total_hash = 0.0
                    unit = ""
                    
                    for val in sub['Real']:
                        try:
                            parts = str(val).strip().split()
                            if len(parts) >= 1: total_hash += float(parts[0].replace(',', '.'))
                            if len(parts) >= 2 and not unit: unit = parts[1] 
                        except: pass
                    
                    if not unit:
                        if "SHA" in algo: unit = "TH/s"
                        elif "SCRYPT" in algo: unit = "GH/s"
                        elif "EQUIHASH" in algo: unit = "kSol/s"
                        elif "X11" in algo: unit = "GH/s"
                        elif "ETCHASH" in algo: unit = "MH/s"
                    
                    hashrates_summary[algo] = f"{total_hash:,.2f} {unit}".strip()
            
            hashrates_summary_str = " | ".join([f"{k}: {v}" for k, v in hashrates_summary.items()])
            if not hashrates_summary_str:
                hashrates_summary_str = "N/A"

            summary_text = safe_text(
                f"Total Devices: {total_dev}\n"
                f"Models: {models_str}\n"
                f"Algorithms: {algos_str}\n"
                f"Total Hashrates: {hashrates_summary_str}\n" # ДОБАВЛЕНА НОВАЯ СТРОКА
                f"Statuses: {status_str}"
            )


            # --- ФИЛЬТРАЦИЯ СТОЛБЦОВ И ШИРИНА ---
            all_cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
            pdf_cols_setting = self.app_settings.get("pdf_cols", all_cols)
            selected_cols = [c for c in all_cols if c in pdf_cols_setting] 
            
            base_widths = {"IP": 28, "Model": 40, "Algo": 20, "Status": 20, "Error": 25, "Uptime": 20, "Real HR": 20, "Avg HR": 20, "Temp": 20, "Fan": 25, "Pool": 40, "Worker": 35}
            widths = [base_widths.get(c, 20) for c in selected_cols]

            # === ГЕНЕРАЦИЯ PDF ===
            pdf = PDFReport(orientation='L', unit='mm', format='A4')
            scale = (pdf.w - pdf.l_margin - pdf.r_margin) / sum(widths)
            widths = [width * scale for width in widths]
            
            # Передаем настройки напрямую в объект (это спасает от ошибки fpdf)
            pdf.report_title = safe_text(f"REPORT: {clean_name}")
            pdf.summary_text = summary_text
            pdf.table_cols = selected_cols
            pdf.table_widths = widths
            
            pdf.add_page() # Тут автоматически нарисуется шапка со сводкой
            pdf.set_font(pdf.report_font, size=7)
            
            data_keys = {"IP": "IP", "Model": "Model", "Algo": "Algo", "Status": "Status", "Error": "Error", "Uptime": "Uptime", "Real HR": "Real", "Avg HR": "Avg", "Temp": "Temp", "Fan": "Fan", "Pool": "Pool", "Worker": "Worker"}
            
            for _, row in df.iterrows():
                for i, col_name in enumerate(selected_cols):
                    dict_key = data_keys.get(col_name, col_name)
                    text = safe_text(str(row.get(dict_key, '')))
                    
                    if col_name == "Pool" and len(text) > 35:
                        clean_text = "..." + text[-32:]
                    elif col_name == "Worker" and len(text) > 25:
                        clean_text = "..." + text[-22:]
                    else: 
                        clean_text = text[:38]

                    pdf.cell(widths[i], 6, pdf.fit_text(clean_text, widths[i]), 1, 0, 'C')
                pdf.ln()
            
            pdf.output(full_path)
            
            # --- ЛОГИКА БУФЕРА ОБМЕНА ---
            if self.app_settings.get("copy_pdf", False):
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(full_path)])
                QApplication.clipboard().setMimeData(mime_data)
                QMessageBox.information(self, "Успех", f"Отчет сохранен и СКОПИРОВАН в буфер обмена!\n{filename}")
            else:

                QMessageBox.information(self, "Успех", f"Отчет сохранен:\n{filename}")

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            QMessageBox.critical(self, "PDF Error", f"{str(e)}\n\nDetails:\n{error_details}")

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.app_settings["theme"] = "dark" if self.dark_mode else "light"
        self.apply_theme()
        try:
            save_app_settings(self.app_settings)
        except OSError as exc:
            QMessageBox.warning(self, "Настройки", f"Не удалось сохранить тему:\n{exc}")

    def apply_theme(self):
        apply_desktop_theme(QApplication.instance(), self.dark_mode)
        self.btn_theme.setText("Светлая тема" if self.dark_mode else "Тёмная тема")
        self.apply_ui_settings()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3)
            if item:
                colors = {"Running": "#69d7ad" if self.dark_mode else "#147652",
                          "WaitWork": "#eebc65" if self.dark_mode else "#93600d",
                          "Sleep": "#a4b3c6" if self.dark_mode else "#5e7088",
                          "Error": "#ff919b" if self.dark_mode else "#b93649"}
                record = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1) or {}
                item.setForeground(QColor(colors.get(record.get("Status"), "#a4b3c6" if self.dark_mode else "#5e7088")))
            rate_item = self.table.item(row, 6)
            if rate_item:
                rate_item.setForeground(QColor("#69d7ad" if self.dark_mode else "#147652"))
        self.update_stats()

    def apply_table_filter(self):
        query = self.search_input.text().strip().casefold()
        status = self.status_filter.currentData()
        self.table.blockSignals(True)
        visible = 0
        for index in range(self.table.rowCount()):
            item = self.table.item(index, 0)
            record = item.data(Qt.ItemDataRole.UserRole + 1) or {}
            haystack = " ".join(str(record.get(key, "")) for key in ("IP", "Model", "Firmware", "FirmwareVersion", "Worker", "Pool")).casefold()
            matches = query in haystack and (not status or (bool(record.get("Stale")) if status == "stale" else record.get("Status") == status))
            self.table.setRowHidden(index, not matches)
            if not matches:
                item.setCheckState(Qt.CheckState.Unchecked)
                for col in range(self.table.columnCount()):
                    cell = self.table.item(index, col)
                    if cell:
                        cell.setSelected(False)
            visible += int(matches)
        self.table.blockSignals(False)
        self.update_selected_count()
        if self.scan_data and not visible:
            self.empty_title.setText("Нет совпадений")
            self.empty_description.setText("Измените поисковый запрос или выберите другое состояние.")
        self.table_stack.setCurrentIndex(0 if visible else 1)

    def closeEvent(self, event):
        workers = list(getattr(self, "workers", []))
        workers += [getattr(self, "worker", None), getattr(self, "update_worker", None)]
        if any(worker is not None and worker.isRunning() for worker in workers):
            self.stop_scan()
            self.status_bar.setText("Завершаем текущие операции перед закрытием…")
            event.ignore()
            QTimer.singleShot(250, self.close)
            return
        event.accept()

if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv)
    app.setApplicationName("ASIC Monitor")
    app.setApplicationVersion(CURRENT_VERSION)
    app.setOrganizationName("ASICMonitor")
    if "--smoke-test" in sys.argv:
        from desktop_ui.smoke import run_smoke
        output = Path(sys.argv[sys.argv.index("--smoke-test") + 1]).resolve()
        sys.exit(run_smoke(app, GeminiApp, output))
    window = GeminiApp()
    window.show()
    sys.exit(app.exec())
