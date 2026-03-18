import sys
import re
import os
import json
import time
import subprocess
import requests
import webbrowser
import pandas as pd
from datetime import datetime


import socket
import platform

# === ГЛОБАЛЬНЫЙ ПЕРЕХВАТ СОКЕТОВ ДЛЯ КНОПКИ STOP ===
# Это позволяет моментально оборвать сканирование во всех 200 потоках
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex

def abortable_connect(self, address):
    if getattr(socket, 'ABORT_SCAN', False):
        raise InterruptedError("Scan aborted by user")
    return _orig_connect(self, address)

def abortable_connect_ex(self, address):
    if getattr(socket, 'ABORT_SCAN', False):
        return 10004  # Ошибка EINTR (вызов прерван)
    return _orig_connect_ex(self, address)

socket.socket.connect = abortable_connect
socket.socket.connect_ex = abortable_connect_ex

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
CURRENT_VERSION = "1.4.0"
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
                             QComboBox) # <- ДОБАВИТЬ ЭТО

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QUrl, QMimeData # <- ДОБАВИТЬ QUrl, QMimeData
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
    from miner_scanner.handlers.miner_actions import WhatsminerManager
    ACTIONS_AVAIL = True
except ImportError:
    try:
        from handlers.miner_actions import WhatsminerManager
        ACTIONS_AVAIL = True
    except ImportError:
        ACTIONS_AVAIL = False

CONFIG_FILE = "ip_ranges.json"
SETTINGS_FILE = "app_settings.json" # <--- НОВЫЙ ФАЙЛ НАСТРОЕК
APP_TITLE = "ASIC_Monitor"

def load_app_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    
    # Настройки по умолчанию
    all_cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
    return {
        "scan_bitmain": True,
        "scan_whatsminer": True,
        "scan_elphapex": True,
        "scan_other": True,
        "timeout": 2,
        "export_dir": "", # Пустая строка = папка по умолчанию
        "copy_pdf": False,
        "pdf_sort": "IP",
        "ui_cols": all_cols.copy(),
        "pdf_cols": all_cols.copy()
    }

def save_app_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
    except: pass

VER = f"{CURRENT_VERSION}"  # Теперь версия в заголовке окна будет браться автоматически из CURRENT_VERSION

# ==========================================
# ДИАЛОГ КОМАНД (REMOTE CTRL)
# ==========================================
class CommandDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remote Control Panel")
        self.setFixedSize(350, 240)
        self.selected_action = None
        
        # Стилизация
        self.setStyleSheet("""
            QDialog { background-color: #F5F7FA; }
            QLabel { color: #333; }
            QRadioButton { font-size: 13px; padding: 5px; color: #333; }
        """)
        
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
        
        # По умолчанию выбрана подсветка
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
        self.setWindowTitle("IP Range Editor")
        self.setFixedSize(400, 350)
        
        layout = QVBoxLayout(self)
        
        # Название сети
        lbl_name = QLabel("Название сети (Network Name):")
        lbl_name.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_name)
        
        self.le_name = QLineEdit(name)
        self.le_name.setPlaceholderText("Например: Клиент А")
        layout.addWidget(self.le_name)
        
        layout.addSpacing(10)
        
        # Диапазоны (многострочное поле)
        lbl_ranges = QLabel("IP Диапазоны (каждый с новой строки):")
        lbl_ranges.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_ranges)
        
        self.te_ranges = QTextEdit()
        self.te_ranges.setPlaceholderText("192.168.1.1-255\n10.10.33.2-255")
        
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
        btn_save.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)
        
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
    finished_signal = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, ranges, filters): # <--- Добавили filters
        super().__init__()
        self.ranges = ranges
        self.filters = filters           # <--- Сохранили
        self.is_running = True

    def run(self):
        socket.ABORT_SCAN = False # Сбрасываем блокировку сокетов при старте
        if not SCANNER_AVAIL:
            self.log_signal.emit("Error: Scanner core not found!")
            self.finished_signal.emit()
            return

        total = len(self.ranges)
        for i, r_str in enumerate(self.ranges):
            if not self.is_running: break
            self.log_signal.emit(f"Scanning: {r_str}...")
            try:
                res = scan_network_range(r_str, target_makes=self.filters)
                # Проверяем еще раз перед выдачей результатов, не нажал ли юзер STOP
                if res and self.is_running: 
                    cleaned_res = []
                    for item in res:
                        model = str(item.get('Model', ''))
                        if "Antminer" in model and "Elphapex" in model:
                            item['Model'] = model.replace("Elphapex ", "")
                        cleaned_res.append(item)
                    self.result_signal.emit(cleaned_res)
            except Exception as e:
                if not self.is_running: break # Игнорируем ошибки при обрыве
                self.log_signal.emit(f"Error {r_str}: {e}")
            self.progress_signal.emit(i + 1, total)
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False
        socket.ABORT_SCAN = True # Моментально обрываем все текущие соединения в core.py!

# ==========================================
# WORKER: ACTIONS
# ==========================================
class ActionWorker(QThread):
    log_signal = pyqtSignal(str)
    
    def __init__(self, ip, action_type):
        super().__init__()
        self.ip = ip
        self.action = action_type 

    def run(self):
        if not ACTIONS_AVAIL:
            self.log_signal.emit(f"❌ {self.ip}: Library missing!")
            return

        try:
            wm = WhatsminerManager(self.ip)
            
            if self.action == "reboot":
                self.log_signal.emit(f"⚡ {self.ip}: Rebooting...")
                ok, msg = wm.reboot()
                act_name = "Reboot"
            
            elif self.action == "led_on":
                self.log_signal.emit(f"💡 {self.ip}: LED Blink...")
                ok, msg = wm.blink_led(True)
                act_name = "LED ON"
                
            elif self.action == "led_off":
                self.log_signal.emit(f"🌑 {self.ip}: LED Auto...")
                ok, msg = wm.blink_led(False)
                act_name = "LED OFF"
            else:
                ok, msg = False, "Unknown action"
                act_name = "?"

            icon = "✅" if ok else "❌"
            self.log_signal.emit(f"{icon} {self.ip}: {act_name} -> {msg}")
            
        except Exception as e:
            self.log_signal.emit(f"🔥 Critical Error {self.ip}: {str(e)}")

# ==========================================
# PDF REPORT CLASS (С умной шапкой и сводкой)
# ==========================================
class PDFReport(FPDF):
    def header(self):
        # Главный заголовок
        self.set_font('Arial', 'B', 14)
        self.cell(0, 8, 'MinerHotel Daily Report', 0, 1, 'L')
        self.line(10, 18, 287, 18)
        self.ln(3)
        
        # Название скана
        self.set_font("Arial", 'B', 12)
        if hasattr(self, 'report_title'):
            self.cell(0, 6, self.report_title, 0, 1, 'L')
        
        # СВОДКА (Рисуется ТОЛЬКО на первой странице)
        if self.page_no() == 1 and hasattr(self, 'summary_text'):
            self.set_font("Arial", '', 9)
            self.multi_cell(0, 5, self.summary_text)
            self.ln(3)
        else:
            self.ln(3)

        # --- ШАПКА ТАБЛИЦЫ (На каждой странице) ---
        self.set_font("Arial", 'B', 8)
        self.set_fill_color(0, 51, 153)
        self.set_text_color(255, 255, 255)
        if hasattr(self, 'table_cols') and hasattr(self, 'table_widths'):
            for i, c in enumerate(self.table_cols):
                self.cell(self.table_widths[i], 8, c, 1, 0, 'C', fill=True)
        self.ln()
        
        # Возврат цвета для обычных строк
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

# ==========================================
# ДИАЛОГ НАСТРОЕК ПРОГРАММЫ
# ==========================================
class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки программы")
        self.setFixedSize(550, 480)
        self.settings = current_settings.copy()
        
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        
        # --- ВКЛАДКА 1: СКАНЕР (ФИЛЬТРЫ) ---
        tab_scan = QWidget()
        l_scan = QVBoxLayout(tab_scan)
        
        lbl_info = QLabel("Ускорьте сканирование, отключив ненужное оборудование:")
        lbl_info.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        l_scan.addWidget(lbl_info)
        
        self.cb_bitmain = QCheckBox("Искать Antminer (Bitmain, VNish)")
        self.cb_whatsminer = QCheckBox("Искать Whatsminer (MicroBT)")
        self.cb_elphapex = QCheckBox("Искать Elphapex (DG-серия)")
        self.cb_other = QCheckBox("Искать остальные (Avalon, iPollo, Jasminer, Hammer)")
        
        self.cb_bitmain.setChecked(self.settings.get("scan_bitmain", True))
        self.cb_whatsminer.setChecked(self.settings.get("scan_whatsminer", True))
        self.cb_elphapex.setChecked(self.settings.get("scan_elphapex", True))
        self.cb_other.setChecked(self.settings.get("scan_other", True))
        
        l_scan.addWidget(self.cb_bitmain)
        l_scan.addWidget(self.cb_whatsminer)
        l_scan.addWidget(self.cb_elphapex)
        l_scan.addWidget(self.cb_other)
        l_scan.addStretch()
        tabs.addTab(tab_scan, "🔍 Сканер")
        
        # --- ВКЛАДКА 2: ИНТЕРФЕЙС (ТАБЛИЦА) ---
        tab_ui = QWidget()
        l_ui = QVBoxLayout(tab_ui)
        
        lbl_ui = QLabel("Выберите столбцы для отображения в ГЛАВНОЙ ТАБЛИЦЕ:")
        lbl_ui.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        l_ui.addWidget(lbl_ui)
        
        all_cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
        ui_cols_checked = self.settings.get("ui_cols", all_cols)
        
        self.list_ui_cols = QListWidget()
        for c in all_cols:
            item = QListWidgetItem(c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c in ui_cols_checked else Qt.CheckState.Unchecked)
            self.list_ui_cols.addItem(item)
        l_ui.addWidget(self.list_ui_cols)
        
        tabs.addTab(tab_ui, "🖥 Интерфейс")
        
        # --- ВКЛАДКА 3: PDF ОТЧЕТЫ ---
        tab_pdf = QWidget()
        l_pdf = QVBoxLayout(tab_pdf)
        
        box_dir = QHBoxLayout()
        box_dir.addWidget(QLabel("Папка отчетов:"))
        self.le_dir = QLineEdit(self.settings.get("export_dir", ""))
        self.le_dir.setPlaceholderText("По умолчанию (папка программы/export_pdf)")
        self.le_dir.setReadOnly(True)
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self.browse_dir)
        box_dir.addWidget(self.le_dir)
        box_dir.addWidget(btn_browse)
        l_pdf.addLayout(box_dir)
        
        self.cb_copy_pdf = QCheckBox("Копировать файл PDF в буфер обмена")
        self.cb_copy_pdf.setChecked(self.settings.get("copy_pdf", False))
        l_pdf.addWidget(self.cb_copy_pdf)
        
        box_sort = QHBoxLayout()
        box_sort.addWidget(QLabel("Сортировать PDF по:"))
        self.cmb_pdf_sort = QComboBox()
        self.cmb_pdf_sort.addItems(["IP", "Model", "Uptime", "Real HR", "Temp", "Status"])
        self.cmb_pdf_sort.setCurrentText(self.settings.get("pdf_sort", "IP"))
        box_sort.addWidget(self.cmb_pdf_sort)
        box_sort.addStretch()
        l_pdf.addLayout(box_sort)
        
        lbl_pdf_cols = QLabel("Столбцы для экспорта в PDF:")
        lbl_pdf_cols.setStyleSheet("font-weight: bold; margin-top: 5px;")
        l_pdf.addWidget(lbl_pdf_cols)
        
        pdf_cols_checked = self.settings.get("pdf_cols", all_cols)
        self.list_pdf_cols = QListWidget()
        for c in all_cols:
            item = QListWidgetItem(c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c in pdf_cols_checked else Qt.CheckState.Unchecked)
            self.list_pdf_cols.addItem(item)
        l_pdf.addWidget(self.list_pdf_cols)
        
        tabs.addTab(tab_pdf, "📑 PDF Отчеты")
        layout.addWidget(tabs)
        
        # Кнопки Сохранить / Отмена
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_save.setStyleSheet("background-color: #0069D9; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.save_and_close)
        
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        
        btn_box.addStretch()
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)
        
    def browse_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения отчетов")
        if folder:
            self.le_dir.setText(folder)
        
    def save_and_close(self):
        self.settings["scan_bitmain"] = self.cb_bitmain.isChecked()
        self.settings["scan_whatsminer"] = self.cb_whatsminer.isChecked()
        self.settings["scan_elphapex"] = self.cb_elphapex.isChecked()
        self.settings["scan_other"] = self.cb_other.isChecked()
        self.settings["export_dir"] = self.le_dir.text()
        self.settings["copy_pdf"] = self.cb_copy_pdf.isChecked()
        self.settings["pdf_sort"] = self.cmb_pdf_sort.currentText()
        
        ui_cols = []
        for i in range(self.list_ui_cols.count()):
            item = self.list_ui_cols.item(i)
            if item.checkState() == Qt.CheckState.Checked: ui_cols.append(item.text())
        self.settings["ui_cols"] = ui_cols
        
        pdf_cols = []
        for i in range(self.list_pdf_cols.count()):
            item = self.list_pdf_cols.item(i)
            if item.checkState() == Qt.CheckState.Checked: pdf_cols.append(item.text())
        self.settings["pdf_cols"] = pdf_cols
        
        self.accept()
# ==========================================
# ГЛАВНОЕ ОКНО
# ==========================================
class GeminiApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{VER}")
        self.resize(1350, 850)
        self.scan_data = [] 
        self.ranges_config = self.load_config()
        self.app_settings = load_app_settings()
        self.dark_mode = is_system_dark_mode()  # <--- Автоопределение темы
        
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
        sidebar.setFixedWidth(300)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(15, 20, 15, 20)
        side_layout.setSpacing(10)

        lbl_logo = QLabel("GEMINI TOOLS")
        lbl_logo.setObjectName("Logo")
        lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(lbl_logo)

        self.btn_theme = QPushButton("☀ / ☾ Switch Theme")
        self.btn_theme.setObjectName("BtnTheme")
        self.btn_theme.clicked.connect(self.toggle_theme)
        side_layout.addWidget(self.btn_theme)
        
        side_layout.addSpacing(10)

        # === КНОПКА ОБНОВЛЕНИЯ ===
        self.btn_update = QPushButton("🔄 Проверить обновления")
        self.btn_update.setObjectName("BtnUpdate")
        self.btn_update.clicked.connect(lambda: self.check_for_updates(auto=False))
        side_layout.addWidget(self.btn_update)
        # ==========================

        # === КНОПКА ИСТОРИИ ИЗМЕНЕНИЙ ===
        self.btn_changelog = QPushButton("📜 История изменений")
        self.btn_changelog.setObjectName("BtnChangelog")
        self.btn_changelog.clicked.connect(self.show_changelog)
        side_layout.addWidget(self.btn_changelog)
        # ==========================

        header_layout = QHBoxLayout()
        lbl_ranges = QLabel("IP RANGES")
        lbl_ranges.setObjectName("SectionHeader")
        
        self.chk_all = QCheckBox("All")
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
        
        side_layout.addWidget(self.list_ranges)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+ Add")
        btn_add.clicked.connect(self.add_range_dialog)
        
        # === КНОПКА ИЗМЕНИТЬ ===
        self.btn_edit_subnet = QPushButton("⚙️ Edit")
        self.btn_edit_subnet.clicked.connect(lambda: self.edit_subnet())
        
        btn_del = QPushButton("- Del")
        btn_del.clicked.connect(self.delete_range)
        
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(self.btn_edit_subnet) # Кнопка посередине
        btn_layout.addWidget(btn_del)
        side_layout.addLayout(btn_layout)

        side_layout.addSpacing(15)

        self.btn_scan = QPushButton("START SCAN")
        self.btn_scan.setObjectName("BtnScan")
        self.btn_scan.clicked.connect(self.start_scan)
        side_layout.addWidget(self.btn_scan)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        side_layout.addWidget(self.btn_stop)

        side_layout.addStretch()

        lbl_exp = QLabel("EXPORT DATA")
        lbl_exp.setObjectName("SectionHeader")
        side_layout.addWidget(lbl_exp)

        btn_csv = QPushButton("Excel / CSV")
        btn_csv.clicked.connect(self.export_csv)
        side_layout.addWidget(btn_csv)

        btn_pdf = QPushButton("PDF Report (Pro)")
        btn_pdf.clicked.connect(self.export_pdf_pro)
        side_layout.addWidget(btn_pdf)

        btn_screenshot = QPushButton("📸 Скриншот Таблицы")
        btn_screenshot.clicked.connect(self.take_screenshot)
        side_layout.addWidget(btn_screenshot)

        # === CONTENT AREA ===
        content = QWidget()
        content.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)

        # 1. DASHBOARD (3 секции: Статусы, Производители, Хешрейт)
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
        
        content_layout.addLayout(self.dash_layout)

        # Инициализируем пустой дашборд
        self.refresh_dashboard({}, {}, {})

        # 2. CONTROL BAR (Новая панель управления)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 5, 0, 5)
        
        lbl_ctrl = QLabel("Device Actions:")
        lbl_ctrl.setStyleSheet("font-weight: bold; color: #888;")
        
        btn_remote = QPushButton("🛠 Remote Ctrl")
        btn_remote.setFixedWidth(140)
        btn_remote.setStyleSheet("background-color: #555; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
        btn_remote.clicked.connect(self.open_remote_panel)
        
        ctrl_layout.addWidget(lbl_ctrl)
        ctrl_layout.addWidget(btn_remote)
        ctrl_layout.addStretch()
        
        content_layout.addLayout(ctrl_layout)

        # 3. Table
        cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
        self.table = QTableWidget()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self.table.itemDoubleClicked.connect(self.open_web_interface)
        
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(True)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(3, 90)  # Status (Новая колонка)
        self.table.setColumnWidth(4, 110) # Error (Сдвинулась)
        
        content_layout.addWidget(self.table)

        # 4. Footer
        footer = QHBoxLayout()
        self.status_bar = QLabel("Ready.")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        
        footer.addWidget(self.status_bar)
        footer.addWidget(self.progress)
        content_layout.addLayout(footer)

        main_layout.addWidget(sidebar)
        main_layout.addWidget(content)

        # === ДОБАВЛЯЕМ АВТОЗАПУСК ПРОВЕРКИ ОБНОВЛЕНИЙ ===
        self.check_for_updates(auto=True)
        self.apply_ui_settings()

    def create_menu_bar(self):
        menubar = self.menuBar()

        # Меню Файл
        file_menu = menubar.addMenu("Файл")

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

    def open_settings_dialog(self):
        dlg = SettingsDialog(self.app_settings, self)
        if dlg.exec(): # Если нажали Сохранить
            self.app_settings = dlg.settings
            save_app_settings(self.app_settings) 
            self.apply_ui_settings() # <--- ТЕПЕРЬ ОНО ПРИМЕНИТЬСЯ МГНОВЕННО

    def apply_ui_settings(self):
        """Жестко скрывает/показывает столбцы таблицы"""
        all_cols = ["IP", "Model", "Algo", "Status", "Error", "Uptime", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker"]
        ui_cols = self.app_settings.get("ui_cols", all_cols)
        
        for i, col in enumerate(all_cols):
            if col in ui_cols:
                self.table.showColumn(i) # Явно показываем
            else:
                self.table.hideColumn(i) # Явно прячем

    def take_screenshot(self):
        """Делает снимок всей правой панели (Итоги + Таблица)"""
        pixmap = self.table.parentWidget().grab() # <--- Фотаем РОДИТЕЛЬСКИЙ виджет
        
        QApplication.clipboard().setPixmap(pixmap)
        self.status_bar.setText("📸 Скриншот скопирован в буфер обмена!")
        QMessageBox.information(self, "Успех", "Скриншот дашборда и таблицы успешно скопирован в буфер обмена!")
    
    # ==========================================
    # ЛОГИКА АВТООБНОВЛЕНИЯ
    # ==========================================
    def check_for_updates(self, auto=False):
        """Проверяет наличие новой версии на GitHub"""
        try:
            response = requests.get(UPDATE_INFO_URL, timeout=5)
            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("version")
                download_url = data.get("url")
                changelog = data.get("changelog", "Обновление системы.")
                
                # Если версия в интернете больше нашей
                if latest_version > CURRENT_VERSION:
                    reply = QMessageBox.question(
                        self, 
                        'Доступно обновление!', 
                        f'Найдена новая версия: {latest_version} (У вас {CURRENT_VERSION})\n\nЧто нового:\n{changelog}\n\nОбновить сейчас?',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        self.apply_update(download_url)
                else:
                    if not auto: # Если нажали кнопку вручную, скажем что всё ок
                        QMessageBox.information(self, "Обновление", "У вас установлена самая актуальная версия!")
        except Exception as e:
            if not auto:
                QMessageBox.warning(self, "Ошибка", f"Не удалось проверить обновления:\n{e}")

    def apply_update(self, download_url):
        """Скачивает новый EXE и выполняет подмену через BAT"""
        if not getattr(sys, 'frozen', False):
            QMessageBox.warning(self, "Внимание", "Автообновление работает только в скомпилированном .exe файле!")
            return
            
        current_exe = sys.executable
        exe_dir = os.path.dirname(current_exe)
        exe_name = os.path.basename(current_exe)
        new_exe = current_exe + ".new"
        
        # Прячем батник в TEMP от OneDrive
        temp_dir = os.environ.get("TEMP", exe_dir)
        bat_file = os.path.join(temp_dir, f"updater_{int(time.time())}.bat")
        
        try:
            msg = QMessageBox(self)
            msg.setWindowTitle("Обновление")
            msg.setText("Скачиваю обновление... Пожалуйста, подождите.")
            msg.setStandardButtons(QMessageBox.StandardButton.NoButton)
            msg.show()
            QApplication.processEvents()
            
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(new_exe, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): 
                        f.write(chunk)
            msg.close()
        except Exception as e:
            msg.close()
            QMessageBox.warning(self, "Ошибка скачивания", str(e))
            return

        # Простой батник без костылей очистки
        bat_content = f"""@echo off
chcp 65001 > NUL
title Установка обновления...
echo Пожалуйста, подождите пару секунд...
echo Идет обновление программы {exe_name}
cd /d "{exe_dir}"
:loop
timeout /t 1 /nobreak > NUL
del "{exe_name}" > NUL 2>&1
if exist "{exe_name}" goto loop
ren "{exe_name}.new" "{exe_name}"
start "" "{exe_name}"
del "%~f0"
"""
        with open(bat_file, 'w', encoding='utf-8') as f:
            f.write(bat_content)
            
        # === ТОТАЛЬНАЯ ЗАЧИСТКА В PYTHON ===
        clean_env = os.environ.copy()
        # Удаляем любые следы PyInstaller из памяти
        keys_to_remove = [k for k in clean_env.keys() if 'MEI' in k.upper() or 'PYI' in k.upper()]
        for k in keys_to_remove:
            clean_env.pop(k, None)
            
        # Запускаем батник в НОВОМ видимом окне (0x00000010 = CREATE_NEW_CONSOLE)
        subprocess.Popen(f'"{bat_file}"', shell=True, env=clean_env, creationflags=0x00000010)
        
        # Жесткое отключение программы, чтобы освободить файл .exe
        os._exit(0)

    def show_changelog(self):
        """Отображает окно со списком изменений"""
        changelog_text = f"""
        <h3>ASIC_Monitor v{CURRENT_VERSION}</h3>
        <b>Версия 1.4.0 (Текущая)</b>
        <ul>
            <li><b>Интерфейс:</b> Настройки программы разделены на 3 удобные вкладки (Сканер, Интерфейс, PDF).</li>
            <li><b>Интерфейс:</b> Добавлена кнопка создания скриншота рабочей области в буфер обмена.</li>
            <li><b>Интерфейс:</b> Улучшено мгновенное скрытие/отображение столбцов таблицы.</li>
            <li><b>PDF Отчеты:</b> Добавлена итоговая сводка (Models, Algos, Statuses) в шапку первой страницы.</li>
            <li><b>PDF Отчеты:</b> Добавлена опция автоматического копирования PDF файла в буфер обмена.</li>
            <li><b>Сканер:</b> Исправлено дублирование данных кулеров и температур для Hammer и Bluestar.</li>
            <li><b>Сканер:</b> Улучшено распознавание статуса для старых веб-интерфейсов (CGMiner).</li>
        </ul>
        <br>
        <b>Версия 1.3.0</b>
        <ul>
            <li>Полный реверс-инжиниринг протокола Elphapex (работа без паролей).</li>
            <li>Добавлена поддержка темной темы оформления.</li>
        </ul>
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle("История изменений")
        msg.setTextFormat(Qt.TextFormat.RichText) 
        msg.setText(changelog_text)
        msg.exec() 

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
                self.run_action(action, rows, confirm_needed=True)

    def show_context_menu(self, pos):
        menu = QMenu()
        
        act_reboot = QAction("⚡ Reboot Device", self)
        act_reboot.triggered.connect(lambda: self.run_action("reboot"))
        menu.addAction(act_reboot)
        
        menu.addSeparator()
        
        act_led_on = QAction("💡 LED: Blink", self)
        act_led_on.triggered.connect(lambda: self.run_action("led_on"))
        menu.addAction(act_led_on)

        act_led_off = QAction("🌑 LED: Auto", self)
        act_led_off.triggered.connect(lambda: self.run_action("led_off"))
        menu.addAction(act_led_off)
        
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def run_action(self, action_type, rows=None, confirm_needed=True):
        socket.ABORT_SCAN = False # <--- Снимаем блокировку сокетов перед отправкой команд
        
        if not rows:
            rows = sorted(set(i.row() for i in self.table.selectedItems()))
        
        if not rows: return

        # Определяем красивое имя для подтверждения
        nice_name = {
            "reboot": "REBOOT",
            "led_on": "FLASH LED",
            "led_off": "NORMAL LED"
        }.get(action_type, action_type)

        # Финальное подтверждение
        if confirm_needed:
            confirm = QMessageBox.question(
                self, 
                "Confirm Action", 
                f"Are you sure you want to apply {nice_name} to {len(rows)} devices?", 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes: return

        # Запуск задач
        for r in rows:
            ip = self.table.item(r, 0).text()
            worker = ActionWorker(ip, action_type)
            worker.log_signal.connect(self.status_bar.setText)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            if not hasattr(self, 'workers'): self.workers = []
            self.workers.append(worker)

    # --- ОСТАЛЬНЫЕ ФУНКЦИИ (Config, Scan, Export) ---
    def load_config(self):
        if not os.path.exists(CONFIG_FILE): return []
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                res = []
                if isinstance(d, list): 
                    for item in d:
                        # Автоматически обновляем старый формат на новый
                        if "range" in item and "ranges" not in item:
                            item["ranges"] = [item["range"]]
                        res.append(item)
                    return res
                if isinstance(d, dict): 
                    return [{"name": k, "ranges": [v]} for k,v in d.items()]
        except: return []
        return []

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.ranges_config, f, ensure_ascii=False, indent=4)
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
            QMessageBox.warning(self, "No Range", "Please select IP range.")
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

        self.table.setRowCount(0)
        self.table.setSortingEnabled(False)
        # ВАЖНО: Прячем столбцы СРАЗУ ПОСЛЕ очистки таблицы, чтобы они не появились снова!
        self.apply_ui_settings() 
        self.scan_data = []
        self.refresh_dashboard({}, {}, {})
        
        self.worker = ScanWorker(to_scan, target_filters)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.result_signal.connect(self.on_result)
        self.worker.log_signal.connect(self.status_bar.setText)
        self.worker.finished_signal.connect(self.on_finished)
        
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        self.worker.start()

    def stop_scan(self):
        if hasattr(self, 'worker'): self.worker.stop()

    def on_progress(self, curr, total):
        self.progress.setValue(int((curr/total)*100))

    def on_result(self, res_list):
        for row in res_list:
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
            ip_item.setData(Qt.ItemDataRole.UserRole, row.get('SortIP', 0))
            self.table.setItem(r, 0, ip_item)
            
            # === 1, 2. Model, Algo ===
            self.table.setItem(r, 1, QTableWidgetItem(str(row.get('Model', ''))))
            self.table.setItem(r, 2, QTableWidgetItem(str(row.get('Algo', '-'))))
            
            # === 3. Status ===
            status_str = str(row.get('Status', 'Running'))
            status_item = QTableWidgetItem(status_str)
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
            # Скрытно передаем точный сырой хешрейт без текстовых "TH/s"
            hr_item.setData(Qt.ItemDataRole.UserRole, row.get('RawHash', 0.0))
            self.table.setItem(r, 6, hr_item) 
            
            # === 7, 8, 9, 10, 11 (Остальные) ===
            self.table.setItem(r, 7, SmartSortItem(str(row.get('Avg'))))
            self.table.setItem(r, 8, SmartSortItem(str(row.get('Temp'))))
            self.table.setItem(r, 9, QTableWidgetItem(str(row.get('Fan'))))
            self.table.setItem(r, 10, QTableWidgetItem(str(row.get('Pool'))))
            self.table.setItem(r, 11, QTableWidgetItem(str(row.get('Worker', '-'))))

        if hasattr(self, 'update_stats'):
            self.update_stats()

    def open_web_interface(self, item):
        """Открывает веб-интерфейс асика в браузере по двойному клику на IP"""
        # Получаем таблицу, в которой кликнули
        table = item.tableWidget()
        
        # Проверяем, как называется заголовок колонки, по которой кликнули
        header_item = table.horizontalHeaderItem(item.column())
        
        # Если кликнули именно по колонке "IP"
        if header_item and header_item.text() == "IP":
            ip_address = item.text().strip()
            # Открываем дефолтный браузер
            webbrowser.open(f"http://{ip_address}")

    def on_finished(self):
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.table.setSortingEnabled(True)
        self.status_bar.setText(f"Done. Found {len(self.scan_data)} devices.")
        self.apply_ui_settings()

    def update_stats(self):
        if not self.scan_data: return
        df = pd.DataFrame(self.scan_data)
        
        # --- Группа 1: СТАТУСЫ ---
        statuses_data = {"TOTAL DEVICES": {"val": str(len(df)), "color": None}}
        if 'Status' in df.columns:
            st = df['Status'].value_counts()
            if st.get("Running", 0) > 0: 
                statuses_data["✅ RUNNING"] = {"val": str(st["Running"]), "color": "#00E676" if getattr(self, 'dark_mode', False) else "#007e33"}
            if st.get("Sleep", 0) > 0: 
                statuses_data["💤 SLEEP"] = {"val": str(st["Sleep"]), "color": "#FFA000"}
            if st.get("WaitWork", 0) > 0: 
                statuses_data["⏳ WAIT WORK"] = {"val": str(st["WaitWork"]), "color": "#17A2B8"}
            if st.get("Error", 0) > 0: 
                statuses_data["❌ ERROR"] = {"val": str(st["Error"]), "color": "#FF4444"}

        # --- Группа 2: БРЕНДЫ ---
        models_data = {}
        if 'Model' in df.columns:
            makers = df['Model'].apply(lambda x: str(x).split()[0] if ' ' in str(x) else str(x)).value_counts()
            for maker, count in makers.items():
                if maker and str(maker) != 'nan':
                    models_data[str(maker).upper()] = {"val": str(count), "color": None}

        # --- Группа 3: ХЕШРЕЙТ ---
        hashrates_data = {}
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
                
                hashrates_data[algo] = {"val": f"{total_hash:,.2f} {unit}".strip(), "color": "#00E676" if getattr(self, 'dark_mode', False) else "#007e33"}

        self.refresh_dashboard(statuses_data, models_data, hashrates_data)

    def refresh_dashboard(self, statuses, models, hashrates):
        # Очистка старых данных
        for layout in [self.layout_status, self.layout_models, self.layout_hashrate]:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
                elif item.layout():
                    while item.layout().count():
                        subitem = item.layout().takeAt(0)
                        if subitem.widget(): subitem.widget().deleteLater()

        # Вспомогательная функция для добавления строчки "Ключ: Значение"
        def add_row(layout, title, data):
            row = QHBoxLayout()
            lbl_t = QLabel(title)
            lbl_t.setObjectName("DashTitle")
            
            lbl_v = QLabel(data["val"])
            lbl_v.setObjectName("DashValue")
            lbl_v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            
            if data["color"]:
                lbl_v.setStyleSheet(f"color: {data['color']};")
                
            row.addWidget(lbl_t)
            row.addWidget(lbl_v)
            layout.addLayout(row)

        # Заполнение блоков
        if not statuses: statuses = {"TOTAL DEVICES": {"val": "0", "color": None}}
        for title, data in statuses.items(): add_row(self.layout_status, title, data)
        
        if not models: models = {"NO DEVICES": {"val": "-", "color": None}}
        for title, data in models.items(): add_row(self.layout_models, title, data)
            
        if not hashrates: hashrates = {"NO HASH": {"val": "0.00", "color": None}}
        for title, data in hashrates.items(): add_row(self.layout_hashrate, title, data)

    def export_csv(self):
        if not self.scan_data: return
        p, _ = QFileDialog.getSaveFileName(self, "Save", "Report.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if p:
            df = pd.DataFrame(self.scan_data)
            drops = ['SortIP', 'RawHash', 'Status'] 
            df.drop(columns=[c for c in drops if c in df.columns], inplace=True, errors='ignore')
            if p.endswith(".csv"): df.to_csv(p, index=False)
            else: df.to_excel(p, index=False)
            QMessageBox.information(self, "OK", "Exported.")

    def export_pdf_pro(self):
        if not FPDF_AVAIL:
            QMessageBox.critical(self, "Error", "FPDF library not installed!\nRun: pip install fpdf")
            return
            
        if not self.scan_data: 
            QMessageBox.warning(self, "Empty", "No data to export.")
            return

        try:
            # --- ПУТЬ СОХРАНЕНИЯ ИЗ НАСТРОЕК ---
            export_dir = self.app_settings.get("export_dir", "")
            if not export_dir or not os.path.exists(export_dir):
                export_dir = os.path.join(current_dir, "export_pdf")
                if not os.path.exists(export_dir):
                    os.makedirs(export_dir)

            base_name = getattr(self, 'last_scan_name', 'Manual_Scan')
            clean_name = re.sub(r'[\\/*?:"<>|]', "", base_name)
            if len(clean_name) > 50: clean_name = clean_name[:50]

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = f"{clean_name}_{timestamp}.pdf"
            full_path = os.path.join(export_dir, filename)

            def safe_text(text):
                return str(text).encode('latin-1', 'ignore').decode('latin-1')

            df = pd.DataFrame(self.scan_data)
            
            # --- СОРТИРОВКА ИЗ НАСТРОЕК ---
            sort_key = self.app_settings.get("pdf_sort", "IP")
            sort_map = {"IP": "SortIP", "Model": "Model", "Uptime": "Uptime", "Real HR": "RawHash", "Temp": "Temp", "Status": "Status"}
            actual_sort_key = sort_map.get(sort_key, "SortIP")
            
            if actual_sort_key in df.columns:
                if actual_sort_key in ['SortIP', 'RawHash']:
                    df[actual_sort_key] = df[actual_sort_key].fillna(0)
                df = df.sort_values(by=actual_sort_key, ascending=True)

            # --- РАСЧЕТ ИТОГОВОЙ СВОДКИ ДЛЯ ШАПКИ ---
            total_dev = len(df)
            models_c = df['Model'].replace('', pd.NA).dropna().apply(lambda x: str(x).split()[0] if ' ' in str(x) else str(x)).value_counts()
            models_str = " | ".join([f"{k}: {v}" for k, v in models_c.items()])
            
            algos_c = df['Algo'].replace('', pd.NA).dropna().value_counts()
            algos_str = " | ".join([f"{k}: {v}" for k, v in algos_c.items()])
            
            status_c = df['Status'].replace('', pd.NA).dropna().value_counts()
            status_str = " | ".join([f"{k}: {v}" for k, v in status_c.items()])
            
            summary_text = safe_text(
                f"Total Devices: {total_dev}\n"
                f"Models: {models_str}\n"
                f"Algorithms: {algos_str}\n"
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
            
            # Передаем настройки напрямую в объект (это спасает от ошибки fpdf)
            pdf.report_title = safe_text(f"REPORT: {clean_name}")
            pdf.summary_text = summary_text
            pdf.table_cols = selected_cols
            pdf.table_widths = widths
            
            pdf.add_page() # Тут автоматически нарисуется шапка со сводкой
            pdf.set_font("Arial", size=7)
            
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

                    pdf.cell(widths[i], 6, clean_text, 1, 0, 'C')
                pdf.ln()
            
            pdf.output(full_path)
            
            # --- ЛОГИКА БУФЕРА ОБМЕНА ---
            if self.app_settings.get("copy_pdf", False):
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(full_path)])
                QApplication.clipboard().setMimeData(mime_data)
                QMessageBox.information(self, "Успех", f"Отчет сохранен и СКОПИРОВАН в буфер обмена!\n{filename}")
            else:
                if os.name == 'nt': os.startfile(full_path)
                QMessageBox.information(self, "Успех", f"Отчет сохранен:\n{filename}")

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            QMessageBox.critical(self, "PDF Error", f"{str(e)}\n\nDetails:\n{error_details}")

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3) 
            if item:
                if self.dark_mode: item.setForeground(QColor("#00E676"))
                else: item.setForeground(QColor("#007e33"))

    def apply_theme(self):
        if self.dark_mode:
            self.setStyleSheet("""
                QMainWindow { background-color: #121212; }
                QWidget { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #E0E0E0; }
                QDialog { background-color: #1E1E1E; color: #E0E0E0; }
                
                #Sidebar { background-color: #1E1E1E; border-right: 1px solid #333; }
                #ContentArea { background-color: #121212; }
                
                QScrollArea { background-color: transparent; border: none; }
                #StatsWidget { background-color: transparent; }
                
                #Logo { font-size: 20px; font-weight: bold; color: #00E676; letter-spacing: 2px; }
                #SectionHeader { color: #777; font-weight: bold; font-size: 11px; }
                
                QListWidget { background-color: #252525; border: none; border-radius: 6px; padding: 5px; color: #ccc; }
                QListWidget::item:selected { background-color: #333; color: white; border-left: 3px solid #00E676; }
                
                QPushButton { background-color: #2D2D2D; border: none; border-radius: 6px; padding: 8px; color: #E0E0E0; }
                QPushButton:hover { background-color: #3D3D3D; }
                
                #BtnScan { background-color: #00E676; color: #000; font-weight: bold; font-size: 14px; }
                #BtnScan:hover { background-color: #00C853; }
                
                #DashBox { background-color: #1E1E1E; border-radius: 8px; border: 1px solid #333; padding: 5px; }
                #DashTitle { color: #AAA; font-size: 13px; font-weight: bold; }
                #DashValue { color: #FFF; font-size: 14px; font-weight: bold; }
                
                QTableWidget { background-color: #1E1E1E; border: 1px solid #333; gridline-color: #2D2D2D; alternate-background-color: #252525; }
                QHeaderView::section { background-color: #2D2D2D; color: #BBB; border: none; padding: 6px; font-weight: bold; }
                QTableWidget::item:selected { background-color: #004D40; color: white; }
                
                QMenu { background-color: #1E1E1E; border: 1px solid #333; color: #E0E0E0; }
                QMenu::item { padding: 5px 20px; }
                QMenu::item:selected { background-color: #00E676; color: black; }
                
                QRadioButton { color: #E0E0E0; }
            """)
        else:
            self.setStyleSheet("""
                QMainWindow { background-color: #F5F7FA; }
                QWidget { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #333; }
                QDialog { background-color: #FFF; color: #333; }
                
                #Sidebar { background-color: #FFFFFF; border-right: 1px solid #E1E4E8; }
                #ContentArea { background-color: #F5F7FA; }
                
                QScrollArea { background-color: transparent; border: none; }
                #StatsWidget { background-color: transparent; }
                
                #Logo { font-size: 20px; font-weight: bold; color: #0069D9; letter-spacing: 2px; }
                #SectionHeader { color: #888; font-weight: bold; font-size: 11px; }
                
                QListWidget { background-color: #F0F2F5; border: 1px solid #E1E4E8; border-radius: 6px; padding: 5px; color: #333; }
                QListWidget::item:selected { background-color: #E6F7FF; color: #0069D9; border-left: 3px solid #0069D9; }
                
                QPushButton { background-color: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; color: #333; }
                QPushButton:hover { background-color: #F3F4F6; }
                
                #BtnScan { background-color: #0069D9; color: #FFF; font-weight: bold; font-size: 14px; border: none; }
                #BtnScan:hover { background-color: #0056b3; }
                
                #DashBox { background-color: #FFFFFF; border-radius: 8px; border: 1px solid #E1E4E8; padding: 5px; }
                #DashTitle { color: #6B7280; font-size: 13px; font-weight: bold; }
                #DashValue { color: #333; font-size: 14px; font-weight: bold; }
                
                QTableWidget { background-color: #FFFFFF; border: 1px solid #E1E4E8; gridline-color: #F0F2F5; alternate-background-color: #F9FAFB; color: #333; }
                QHeaderView::section { background-color: #F3F4F6; color: #4B5563; border: none; padding: 6px; font-weight: bold; }
                QTableWidget::item:selected { background-color: #E6F7FF; color: #000; }
                
                QMenu { background-color: #FFFFFF; border: 1px solid #CCC; color: #333; }
                QMenu::item { padding: 5px 20px; }
                QMenu::item:selected { background-color: #0069D9; color: white; }
                
                QRadioButton { color: #333; }
            """)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GeminiApp()
    window.show()
    sys.exit(app.exec())